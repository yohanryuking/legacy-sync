# Roadmap

Adaptado del roadmap original de portafolio. Reemplaza Supabase por
PostgreSQL + FastAPI/WebSocket propio (ver
[DECISIONS.md — ADR-001](DECISIONS.md#adr-001-postgresql-en-vez-de-supabase)).
Total estimado original: 5-6 semanas part-time.

## ✅ Fase 1 — Fundaciones del pipeline (implementada en este repo)

### Sprint 0 — Setup
- [x] `docker-compose.yml` con Postgres 16.
- [x] Modelos SQLAlchemy: `legacy_customers` (origen sucio), `migrated_customers`
      (destino), `migration_logs` (auditoría), `retry_queue` (cola, solo esquema).
- [x] `legacy_sync/seed.py`: genera datos sucios a propósito (fechas mixtas,
      nulls, emails mal formados, duplicados del mismo cliente).
- [x] CLI (`legacy_sync/cli.py`): `init-db`, `seed`, `migrate`, `status`.

### Sprint 1 — Extracción y validación
- [x] `legacy_sync/etl/extract.py`: lee el legado ordenado por `legacy_id`.
- [x] `legacy_sync/schemas.py`: `CustomerRecord` (Pydantic) con validadores de
      email, fechas en múltiples formatos, nombre no vacío.
- [x] `legacy_sync/etl/validate.py`: clasifica cada registro válido/inválido
      sin lanzar excepciones; los inválidos van a `migration_logs` con el
      campo y motivo específico.
- [x] Tests: `tests/test_validate.py` (incluye "un registro malo no afecta la
      validación de los demás").

### Sprint 2 — Carga idempotente
- [x] `legacy_sync/etl/checksum.py` + `legacy_sync/etl/load.py`: upsert por
      `natural_key` (email normalizado), condicionado a que el checksum
      cambie.
- [x] `legacy_sync/etl/pipeline.py`: orquesta todo, cada registro en su
      propio `SAVEPOINT`.
- [x] Tests: `tests/test_load_idempotent.py` — **correr la migración dos
      veces seguidas no duplica nada** (el test central del proyecto),
      duplicados del legado colapsan a una fila, una corrección del legado
      actualiza en vez de duplicar.
- [x] Verificado manualmente contra Postgres real (no solo SQLite de test).

**Criterio de aceptación de Fase 1 (cumplido)**: `python -m legacy_sync
migrate` corrido dos veces seguidas dejar exactamente las mismas filas en
`migrated_customers`; un registro inválido queda en `migration_logs` con el
campo y motivo exactos sin detener el resto del batch.

---

## 📋 Fase 2 — Resiliencia y observabilidad (documentada, no implementada)

### Sprint 3 — Cola de reintentos con backoff activo
El esquema de `retry_queue` ya existe (`legacy_sync/models/target.py`); falta
el worker que la procese.

- [ ] Worker (`legacy_sync/etl/retry_worker.py`) que:
  - selecciona `retry_queue` con `status='pending' AND next_attempt_at <= now()`,
  - reintenta la carga (reusa `legacy_sync/etl/load.py`),
  - en éxito: marca `status='succeeded'` (o borra la fila),
  - en fallo: incrementa `attempt_count`, recalcula `next_attempt_at` con
    backoff exponencial (`base * 2^attempt_count`, con jitter),
  - al llegar a `max_attempts`: marca `status='failed'` (fallo definitivo,
    requiere intervención manual — ya no se reintenta solo).
- [ ] Endpoint FastAPI `POST /retry-queue/{id}/retry` para forzar un
      reintento puntual desde el dashboard, saltando el `next_attempt_at`.
- [ ] Tests: backoff crece correctamente entre intentos; un ítem que agota
      `max_attempts` no se vuelve a tocar solo.

**Criterio de aceptación**: un fallo de carga simulado (`--simulate-load-failures`)
termina reintentándose automáticamente y, si el reintento tiene éxito,
aparece en `migrated_customers` sin intervención manual.

### Sprint 4 — Dashboard en tiempo real
Reemplaza Supabase Realtime por Postgres `LISTEN/NOTIFY` + WebSocket.

- [ ] API FastAPI (`api/`, nuevo paquete) con:
  - `GET /migrations/summary` — conteos actuales (procesados/éxito/fallidos).
  - `GET /migrations/failures` — lista de `migration_logs` fallidos, paginada.
  - `WS /migrations/live` — stream de eventos de progreso.
- [ ] Trigger de Postgres (`NOTIFY migration_events, ...`) en `migration_logs`
      y `migrated_customers`, consumido por FastAPI vía `asyncpg`
      `LISTEN` y reenviado a los WebSockets conectados.
- [ ] Frontend React: barra de progreso en vivo, lista de fallos con motivo,
      botón "reintentar" por registro (llama al endpoint de Sprint 3).

**Criterio de aceptación**: con el dashboard abierto en el navegador, correr
`legacy-sync migrate` en otra terminal y ver los contadores actualizarse sin
recargar la página.

### Sprint 5 — Checkpointing y resiliencia
- [ ] Tabla `migration_checkpoints` (`run_id`, `last_legacy_id_processed`,
      `updated_at`).
- [ ] `run_pipeline()` acepta un `checkpoint` opcional y arranca la
      extracción desde `legacy_id > checkpoint` (ya soportado por el orden
      de `extract.py`, falta la persistencia del punto de avance).
- [ ] Guardar el checkpoint cada N registros (no solo al final), para que una
      interrupción a mitad de camino pierda como mucho ese lote parcial.
- [ ] Test que simula una interrupción (excepción forzada a mitad del
      batch) y verifica que una segunda corrida retoma desde el checkpoint
      sin reprocesar todo desde cero.

**Criterio de aceptación**: matar el proceso de migración a mitad de camino
(`kill -9` o excepción forzada) y, al volver a correrlo, no vuelve a
reprocesar los registros ya confirmados — y sigue siendo idempotente.

### Sprint 6 — Deploy y presentación
- [ ] Adoptar Alembic para migraciones de esquema versionadas (reemplaza
      `Base.metadata.create_all`).
- [ ] Deploy: API + worker de reintentos + Postgres gestionado (Railway/Fly/
      Render — cualquiera con Postgres administrado sirve).
- [ ] README con arquitectura y decisiones (ya cubierto por
      `docs/ARCHITECTURE.md` y `docs/DECISIONS.md` — actualizar si cambia algo
      en el deploy).
- [ ] Video demo mostrando: una migración corriendo dos veces sin duplicar
      datos, un registro fallando y reintentándose automáticamente, y el
      dashboard actualizándose en vivo.

---

## Cómo continuar (para el próximo desarrollador)

1. Empezar por Sprint 3: el esquema de `retry_queue` ya está, solo falta el
   worker. `legacy_sync/etl/pipeline.py::_load_with_retry_queue` ya encola
   correctamente los fallos — el worker solo necesita leer de ahí.
2. Sprint 4 depende de tener FastAPI corriendo; no depende de Sprint 3
   terminado (el dashboard puede mostrar `migration_logs` sin que el worker
   de reintentos exista todavía).
3. Sprint 5 (checkpointing) es independiente de 3 y 4, se puede hacer en
   paralelo.
4. Antes de tocar el esquema de tablas para cualquier sprint nuevo, migrar de
   `metadata.create_all()` a Alembic (parte de Sprint 6, pero conviene
   adelantarlo si el esquema va a cambiar seguido).
