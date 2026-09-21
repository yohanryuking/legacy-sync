# Decisiones de diseño (ADRs)

Formato corto: contexto, decisión, alternativas descartadas y por qué.

## ADR-001: PostgreSQL en vez de Supabase

**Contexto**: la propuesta original de portafolio usa Supabase como destino
y como motor del dashboard en tiempo real (vía Supabase Realtime), reusando
un patrón de otro proyecto del portafolio.

**Decisión**: usar PostgreSQL "liso" (self-hosted vía Docker Compose, o
cualquier Postgres gestionado) con SQLAlchemy 2.0 como capa de acceso, y
reemplazar Supabase Realtime por `LISTEN/NOTIFY` de Postgres consumido por un
WebSocket propio en FastAPI (Fase 2).

**Por qué**:
- El valor del proyecto es demostrar ingeniería de datos (validación,
  idempotencia, resiliencia) — no integrar un BaaS. Supabase es Postgres por
  debajo; usarlo directo hace explícito lo que realmente importa (SQL,
  constraints, transacciones) en vez de esconderlo detrás de un SDK.
  Contabilizei (y cualquier empresa con sistemas legados) evalúa "sabés
  modelar datos y escribir SQL/ORM correcto", no "sabés usar el SDK de
  Supabase".
- Elimina una dependencia externa gestionada por un tercero para correr el
  proyecto localmente o en CI — todo el desarrollo y los tests corren contra
  un Postgres (o SQLite, ver ADR-003) sin cuenta ni claves de terceros.
- `LISTEN/NOTIFY` + WebSocket es un patrón igual de real en la industria que
  Supabase Realtime (que internamente también usa replication de Postgres) y
  es más transferible: funciona con cualquier Postgres, no solo con Supabase.

**Alternativas descartadas**:
- *Mantener Supabase*: descartado por lo anterior — no aporta señal técnica
  adicional para el propósito del portafolio y agrega una dependencia externa.
- *MongoDB u otra NoSQL*: descartado porque el problema es inherentemente
  relacional (una clave natural única, constraints de integridad, upserts
  transaccionales) — Postgres es el motor que mejor modela exactamente esas
  garantías, y es el que mejor encaja con un stack Python/FastAPI.

## ADR-002: Tablas separadas para datos, logs y reintentos

**Contexto**: hay tres tipos de información muy distintos generados por el
pipeline: datos ya migrados y limpios, un registro de auditoría de todo lo
que pasó, y una cola operativa de "cosas por reintentar".

**Decisión**: tres tablas (`migrated_customers`, `migration_logs`,
`retry_queue`), nunca una tabla con columnas de estado mezcladas.

**Por qué**: mezclar "datos limpios" con "registros en proceso de arreglo"
en la misma tabla obliga a filtrar por estado en cada query de negocio del
sistema nuevo, y hace fácil que un bug de filtrado exponga datos a medio
migrar. Separar por tabla hace la garantía estructural: si una fila está en
`migrated_customers`, pasó validación y carga — punto. `migration_logs` es
solo apend-only para auditoría; `retry_queue` es la única tabla operativa
"mutable" del proceso de reintento.

## ADR-003: Idempotencia vía clave natural + checksum, no solo INSERT

**Contexto**: correr una migración dos veces (por error del operador, por un
timeout que hizo reintentar el job completo) es un evento normal en
producción, no una excepción.

**Decisión**: `natural_key` (email normalizado) con unique constraint +
`checksum` del contenido, usado en `INSERT ... ON CONFLICT DO UPDATE ...
WHERE checksum changed`.

**Por qué no alcanza con "solo insertar"**: un `INSERT` simple duplica todo
en la segunda corrida. Un `INSERT ... ON CONFLICT DO NOTHING` evita
duplicados pero nunca propaga correcciones del legado (ej. un teléfono
corregido) a un registro ya migrado. La combinación clave natural + checksum
da lo mejor de ambos: no duplica, y sí actualiza cuando el contenido
realmente cambió — sin generar un `UPDATE` innecesario cuando no cambió
(relevante para no disparar triggers/`updated_at`/notificaciones de más).

**Por qué checksum y no comparar campo por campo**: comparar campo por campo
en SQL es más verboso y frágil a medida que se agregan columnas; un hash
único cubre "cambió algo" con una sola comparación, a costa de no decir
*qué* campo cambió (aceptable acá porque el dato "de verdad" siempre es el
legado, no hace falta diffear campo a campo para decidir sobrescribir).

## ADR-004: Validación con Pydantic que aísla el error, no que rompe el batch

**Contexto**: en un batch de 10.000 registros es normal que algunos tengan
datos inválidos. La forma ingenua de validar (`for row in rows:
Model(**row)`) lanza una excepción en el primer registro malo y aborta todo.

**Decisión**: `validate_legacy_record()` nunca lanza — atrapa
`pydantic.ValidationError` y la traduce a una lista estructurada de
`{field, message}`, devuelta junto con el resultado (éxito o no).

**Por qué**: el pipeline debe procesar los 9.999 registros buenos y dar
visibilidad exacta de cuál falló y por qué, en vez de fallar entero por un
registro. Esto es, literalmente, "governança/qualidade de dados" — y es lo
que hace la diferencia entre un script y un pipeline de producción.

## ADR-005: Cola de reintentos separada de la validación

**Contexto**: un registro puede fallar por dos razones muy distintas — datos
mal formados (nunca se van a poder cargar sin corregir el origen) o un fallo
transitorio de infraestructura (timeout de red, DB momentáneamente ocupada,
que sí se resuelve solo con un reintento).

**Decisión**: solo los fallos de la etapa de **carga** van a `retry_queue`.
Los fallos de **validación** solo se registran en `migration_logs` — nunca
se reintentan automáticamente, porque reintentar no cambia que el dato esté
mal formado.

**Por qué**: mezclar ambos tipos de fallo en una sola cola de reintentos
haría que el sistema reintente indefinidamente algo que nunca va a
funcionar (un email vacío no se arregla solo), desperdiciando ciclos y
ensuciando las métricas de "cuántos reintentos están realmente en curso".

## ADR-006: Backoff con jitter, y el endpoint de reintento forzado ignora el estado `failed`

**Contexto** (Sprint 3): al reintentar automáticamente ítems de
`retry_queue`, si muchos fallan al mismo tiempo (ej. un corte de red breve),
todos quedarían programados para reintentar exactamente en el mismo
instante, generando un pico de carga innecesario ("thundering herd").
Además, un ítem que agotó `max_attempts` (`status='failed'`) requiere
intervención manual — pero esa intervención necesita una forma de
dispararse.

**Decisión**: el delay de backoff (`base_seconds * 2**attempt_count`) suma
un jitter aleatorio (`0` a `retry_backoff_jitter_seconds`), y el endpoint
`POST /retry-queue/{id}/retry` reintenta cualquier ítem que no esté ya
`succeeded` — incluyendo uno `failed` — saltando tanto `next_attempt_at`
como el chequeo de `max_attempts`.

**Por qué**: el jitter dispersa los reintentos en el tiempo sin necesitar
coordinación entre ítems. Permitir forzar un ítem `failed` es lo que le da
sentido a que existan dos vías: el backoff automático es para fallos
transitorios que se resuelven solos con el tiempo, y el botón manual es
para cuando alguien ya identificó y corrigió la causa raíz (ej. arregló una
credencial vencida) y no quiere esperar a que el sistema lo detecte solo —
sin eso, un ítem `failed` quedaría huérfano para siempre salvo que se le
haga un `UPDATE` manual en la base.

## ADR-007: SQLite en tests, Postgres en producción

**Contexto**: correr Postgres en CI (o en cada sandbox de desarrollo) es una
dependencia extra que ralentiza el feedback loop de los tests.

**Decisión**: los tests (`tests/`) usan SQLite en memoria; producción y
desarrollo local usan Postgres real (vía Docker Compose).

**Por qué es seguro**: el código de negocio (`schemas.py`, `pipeline.py`,
`validate.py`) es 100% agnóstico de motor — solo `load.py` tiene una rama
explícita por dialecto (`postgresql.insert` vs `sqlite.insert`) para el
`ON CONFLICT`, ambas usando la misma API de SQLAlchemy 2.0. El comportamiento
de idempotencia se valida en ambos motores: los tests cubren la rama SQLite
automáticamente en cada corrida, y este mismo pipeline fue verificado
manualmente contra Postgres real (`python -m legacy_sync migrate` corrido
dos veces seguidas) durante el desarrollo de Fase 1.
