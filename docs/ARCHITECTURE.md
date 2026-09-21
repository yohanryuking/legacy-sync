# Arquitectura

## Vista general

```
┌────────────┐      extrae      ┌───────────────────────┐      carga      ┌─────────────────────────┐
│  Legado    │ ───────────────► │         ETL            │ ──────────────► │       PostgreSQL          │
│ (datos     │                  │       (Python)          │                 │        (destino)          │
│  sucios)   │                  │  ┌───────────────────┐ │                 │ ┌───────────────────────┐ │
└────────────┘                  │  │ Extracción         │ │                 │ │ migrated_customers     │ │
                                 │  │ (schemas Pydantic) │ │                 │ │ (tabla final, limpia)  │ │
                                 │  └───────────────────┘ │                 │ └───────────────────────┘ │
                                 │  ┌───────────────────┐ │                 │ ┌───────────────────────┐ │
                                 │  │ Carga              │ │                 │ │ migration_logs         │ │
                                 │  │ (upsert + backoff*) │ │                 │ │ retry_queue            │ │
                                 │  └───────────────────┘ │                 │ └───────────────────────┘ │
                                 └───────────────────────┘                 └──────────────┬────────────┘
                                                                                            │ LISTEN/NOTIFY
                                                                                            │ + WebSocket (Fase 2*)
                                                                                            ▼
                                                                              ┌───────────────────────┐
                                                                              │  Dashboard (Fase 2*)   │
                                                                              │     tiempo real         │
                                                                              └───────────────────────┘
```

`*` = diseñado y documentado, no implementado en Fase 1 (ver
[ROADMAP.md](ROADMAP.md)).

## Por qué PostgreSQL en vez de Supabase

Ver [DECISIONS.md — ADR-001](DECISIONS.md#adr-001-postgresql-en-vez-de-supabase).
En resumen: Supabase *es* Postgres + una capa de Realtime/Auth/Storage
encima. Para este proyecto (pipeline de ETL + dashboard propio con FastAPI)
esa capa no aporta nada que no se resuelva con Postgres liso + SQLAlchemy +
un WebSocket propio, y evita acoplar el proyecto a un servicio gestionado de
terceros — el objetivo del proyecto es demostrar ingeniería de datos, no
integración de un BaaS.

## Los tres bloques del ETL

### 1. Extracción (`legacy_sync/etl/extract.py`)

Lee `legacy_customers` fila por fila, ordenado por `legacy_id`, y la
convierte a un `dict` "crudo" — sin transformar nada todavía. El orden por
`legacy_id` no es cosmético: es lo que permite retomar la migración desde un
punto (`WHERE legacy_id > checkpoint`) en el checkpointing de Sprint 5.

La tabla `legacy_customers` está modelada a propósito con columnas `String`
nullable incluso donde "debería" haber un `Date` o un `Integer` — así es como
lucen los sistemas legados reales (exports de texto, planillas, dumps de un
CRM viejo). Ver `legacy_sync/seed.py` para cómo se generan los datos sucios:
nombres vacíos, emails mal formados, fechas en 4 formatos distintos, y
duplicados deliberados del mismo cliente con casing distinto.

### 2. Validación (`legacy_sync/schemas.py` + `legacy_sync/etl/validate.py`)

Cada `dict` crudo se valida contra `CustomerRecord`, un modelo de Pydantic
con validadores que:

- normalizan email (trim + lowercase) y lo verifican como email real,
- parsean fecha de nacimiento y `created_at` probando varios formatos conocidos,
- exigen `full_name` no vacío,
- convierten teléfono vacío a `None`.

`validate_legacy_record()` **nunca lanza**: si Pydantic rechaza el registro,
la función traduce cada error a `{field, message}` y retorna un
`ValidationOutcome` con `record=None` y la lista de errores. Quien orquesta
(`pipeline.py`) decide qué hacer — en este caso, seguir con el siguiente
registro y dejar constancia en `migration_logs` con el campo y motivo exacto
del fallo. Un registro malo nunca aborta el resto del lote.

### 3. Carga (`legacy_sync/etl/load.py` + `checksum.py`)

La clave de todo el proyecto. Cada `CustomerRecord` válido se identifica por
una **clave natural** (`natural_key` = email normalizado) y un **checksum**
del contenido relevante (nombre, email, fecha de nacimiento, teléfono).

El upsert usa `INSERT ... ON CONFLICT (natural_key) DO UPDATE ... WHERE
checksum IS DISTINCT FROM excluded.checksum` (sintaxis específica de
dialecto vía `sqlalchemy.dialects.postgresql`/`.sqlite`, elegida en runtime
según el engine). Esto da tres propiedades a la vez:

1. **Idempotencia real**: correr la migración N veces dejar exactamente las
   mismas filas — nunca crece `migrated_customers` por una re-corrida.
2. **Deduplicación de duplicados del legado**: dos filas de origen con el
   mismo email colapsan en una sola fila de destino.
3. **No hay escrituras fantasma**: si el contenido no cambió, el `WHERE`
   evita un `UPDATE` real (aunque el registro se re-procese).

Cada registro se carga dentro de su propio `SAVEPOINT`
(`session.begin_nested()`). Si la carga de un registro específico falla
(constraint, error transitorio simulado o real), se hace rollback *solo* de
ese savepoint, se registra en `migration_logs` (`stage=load`) y se encola en
`retry_queue` — sin afectar a los demás registros del batch ni a los ya
confirmados.

### Nota sobre duplicados con contenido distinto

Si dos filas del legado comparten `natural_key` pero tienen contenido
*distinto* entre sí (p. ej. mismo email, pero un nombre en mayúsculas y otro
en minúsculas — algo real en sistemas legados), el upsert es determinístico
por orden de `legacy_id`: la fila de destino termina reflejando el último
registro de origen procesado en esa corrida. La garantía que se mantiene es
"no hay filas duplicadas", no "el contenido nunca cambia entre corridas" —
eso último solo se cumple si el legado mismo es internamente consistente.
Ver `tests/test_load_idempotent.py::test_duplicate_emails_in_legacy_collapse_to_one_migrated_row`.

## Modelo de datos

| Tabla                 | Propósito                                                                 |
|-----------------------|-----------------------------------------------------------------------------|
| `legacy_customers`    | Origen "sucio". Nunca se escribe desde el pipeline, solo se lee.            |
| `migrated_customers`  | Destino limpio. Una fila por `natural_key`. Nunca contiene datos inválidos. |
| `migration_logs`      | Auditoría de *todo*: validaciones fallidas y cargas fallidas, con motivo.   |
| `retry_queue`         | Candidatos a reintento (solo fallos de **carga**, no de validación).       |

Separar `migration_logs` de `retry_queue` de `migrated_customers` es
deliberado (ver [DECISIONS.md — ADR-002](DECISIONS.md#adr-002-tablas-separadas-para-datos-logs-y-reintentos)):
nunca se mezclan datos limpios con registros en proceso de arreglo.

## Cola de reintentos activa (Sprint 3)

`legacy_sync/etl/retry_worker.py` procesa `retry_queue` (esquema definido
desde Fase 1, worker agregado en Fase 2):

- `process_retry_queue()` selecciona los ítems `pending` cuyo
  `next_attempt_at` ya pasó y reintenta la carga (reusa `upsert_customer`,
  también dentro de un `SAVEPOINT` por ítem).
- En éxito, el ítem pasa a `succeeded`.
- En fallo, `attempt_count` sube y `next_attempt_at` se recalcula con backoff
  exponencial (`retry_backoff_base_seconds * 2**attempt_count` + jitter
  aleatorio, para no reintentar todos los ítems en el mismo instante). Al
  llegar a `max_attempts`, el ítem pasa a `failed` y deja de tocarse solo.
- `legacy_sync/api/app.py` expone `POST /retry-queue/{id}/retry`, que llama
  a `attempt_retry()` directamente (sin mirar `next_attempt_at` ni
  `status`, salvo que ya esté `succeeded`) — es el botón "reintentar ahora"
  de un registro específico. Corre incluso sobre un ítem `failed`: permite
  rescatarlo manualmente si alguien arregló la causa raíz (ej. la
  infraestructura de red) sin depender de que el backoff automático lo
  vuelva a intentar solo.
- `legacy_sync/cli.py`: `legacy-sync retry` corre `process_retry_queue()`
  una vez — pensado para invocarse desde un cron o un loop; el *scheduler*
  en sí (quién y cuándo lo llama en producción) es tarea de Sprint 6
  (deploy).

## Fase 2 en adelante (Sprint 4+, documentado, no implementado)

- **Dashboard en tiempo real**: FastAPI exponiendo progreso vía WebSocket,
  alimentado por `LISTEN/NOTIFY` de Postgres en vez de Supabase Realtime
  (Sprint 4).
- **Checkpointing**: tabla `migration_checkpoints` con el último `legacy_id`
  procesado exitosamente, para retomar tras un crash sin reprocesar desde
  cero (Sprint 5).
- **Alembic**: migraciones versionadas del esquema en vez de
  `metadata.create_all()` (a partir de que el esquema empiece a evolucionar
  en producción).

Detalle completo, con criterios de aceptación por sprint, en
[ROADMAP.md](ROADMAP.md).
