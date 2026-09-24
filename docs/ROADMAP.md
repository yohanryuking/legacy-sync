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

## ✅ Fase 2 (parcial) — Sprint 3: cola de reintentos con backoff activo

El esquema de `retry_queue` ya existía (`legacy_sync/models/target.py`); este
sprint agrega el worker que la procesa.

- [x] Worker (`legacy_sync/etl/retry_worker.py`) que:
  - selecciona `retry_queue` con `status='pending' AND next_attempt_at <= now()`,
  - reintenta la carga (reusa `legacy_sync/etl/load.py`),
  - en éxito: marca `status='succeeded'`,
  - en fallo: incrementa `attempt_count`, recalcula `next_attempt_at` con
    backoff exponencial (`base * 2^attempt_count` + jitter, configurable en
    `Settings.retry_backoff_base_seconds`/`retry_backoff_jitter_seconds`),
  - al llegar a `max_attempts`: marca `status='failed'` (fallo definitivo,
    requiere intervención manual).
- [x] `legacy_sync/api/app.py`: API FastAPI mínima con
      `POST /retry-queue/{id}/retry`, para forzar un reintento puntual
      saltando `next_attempt_at` (incluso sobre un ítem ya marcado `failed`
      — útil cuando se corrigió la causa raíz manualmente). El resto del
      dashboard (resumen, lista de fallos, WebSocket) es Sprint 4.
- [x] CLI: `legacy-sync retry` corre `process_retry_queue()` una vez (pensado
      para invocarse desde un cron o loop; el scheduler en sí es Sprint 6).
- [x] Tests (`tests/test_retry_worker.py`, `tests/test_api_retry.py`): backoff
      crece entre intentos, un ítem que agota `max_attempts` se marca
      `failed` y no se vuelve a tocar solo, el endpoint fuerza el reintento
      salteando `next_attempt_at`, un ítem ya `succeeded` no se reintenta.
- [x] Verificado manualmente contra Postgres real: `migrate --simulate-load-failures`
      encoló fallos reales en `retry_queue`, `legacy-sync retry` los recuperó
      todos, y el endpoint FastAPI respondió correctamente vía `uvicorn`.

**Criterio de aceptación (cumplido)**: un fallo de carga simulado
(`--simulate-load-failures`) termina reintentándose automáticamente y, si el
reintento tiene éxito, aparece en `migrated_customers` sin intervención
manual.

## ✅ Fase 2 (parcial) — Sprint 4: dashboard en tiempo real

Reemplaza Supabase Realtime por Postgres `LISTEN/NOTIFY` + WebSocket, tal
como se planteó en `docs/DECISIONS.md` ADR-001.

- [x] `legacy_sync/realtime.py`: `install_notify_triggers()` crea una
      función `legacy_sync_notify()` y un trigger `AFTER INSERT OR UPDATE`
      por tabla (`migrated_customers`, `migration_logs`, `retry_queue`) que
      hacen `pg_notify('legacy_sync_events', <tabla>)`. No-op fuera de
      Postgres (SQLite en tests). Se instala automáticamente desde
      `legacy_sync init-db`.
- [x] `legacy_sync/api/listener.py` + `connection_manager.py`: una tarea de
      fondo de FastAPI (`asyncpg.connect` + `LISTEN`) que reenvía cada
      aviso a todos los WebSockets conectados. Un fallo al conectar se
      loguea sin tumbar el resto de la API.
- [x] `legacy_sync/api/app.py` ampliado con:
  - `GET /migrations/summary` — conteos actuales por etapa.
  - `GET /migrations/validation-failures` — fallos de validación, paginado.
  - `GET /retry-queue` — cola de reintentos (todas las etapas), con filtro
    `?status=`.
  - `WS /migrations/live` — un mensaje (el nombre de la tabla) por cada
    cambio; el cliente reacciona re-pidiendo el estado (ver ADR-009).
- [x] Frontend estático sin build step (`legacy_sync/static/`): tarjetas de
      resumen, tabla de cola de reintentos con botón "Reintentar ahora"
      (llama al endpoint de Sprint 3), tabla de fallos de validación. Se
      sirve desde la misma app FastAPI (`app.mount("/", StaticFiles(...))`).
      Ver ADR-008 para por qué no es un SPA de React con bundler.
- [x] Tests: `test_realtime.py`, `test_connection_manager.py`,
      `test_api_dashboard.py` (summary, listas, conexión de WebSocket).
- [x] Verificado manualmente contra Postgres real: con la API corriendo
      bajo `uvicorn` y un WebSocket conectado, correr `legacy-sync retry`
      **en otro proceso** hizo llegar el aviso al socket sin polling —
      exactamente el escenario cross-proceso que Supabase Realtime
      resolvía, ahora con `LISTEN/NOTIFY` liso.

**Criterio de aceptación (cumplido)**: con el dashboard abierto en el
navegador, correr `legacy-sync migrate` o `legacy-sync retry` en otra
terminal actualiza los contadores sin recargar la página.

## 📋 Fase 2 (resto) — Resiliencia (documentada, no implementada)

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
      `Base.metadata.create_all`) — importante porque `install_notify_triggers()`
      (Sprint 4) asume que corre después de crear las tablas; una migración
      de Alembic debería incluir ese paso como parte de la migración misma.
- [ ] Scheduler real para `legacy-sync retry` (hoy es un comando manual/cron
      externo; Sprint 3 solo entrega el worker, no quién lo dispara).
- [ ] Deploy: API + worker de reintentos + Postgres gestionado (Railway/Fly/
      Render — cualquiera con Postgres administrado sirve). Servir
      `legacy_sync/static/` como parte del mismo deploy de la API (ya no
      requiere un build de frontend separado, ver ADR-008).
- [ ] Si el dashboard crece más allá de tablas simples, evaluar migrar
      `legacy_sync/static/` a un SPA de React con bundler — no antes:
      ver ADR-008 sobre por qué no hace falta todavía.
- [ ] README con arquitectura y decisiones (ya cubierto por
      `docs/ARCHITECTURE.md` y `docs/DECISIONS.md` — actualizar si cambia algo
      en el deploy).
- [ ] Video demo mostrando: una migración corriendo dos veces sin duplicar
      datos, un registro fallando y reintentándose automáticamente, y el
      dashboard actualizándose en vivo.

---

## Cómo continuar (para el próximo desarrollador)

1. Seguir con Sprint 5 (checkpointing): independiente de todo lo anterior,
   se puede empezar directamente.
2. El scheduler de `legacy-sync retry` (ítem suelto en Sprint 6) puede
   adelantarse en cualquier momento — es solo un cron/loop que llama al
   comando existente, no requiere cambios de código.
3. Antes de tocar el esquema de tablas para cualquier sprint nuevo, migrar de
   `metadata.create_all()` a Alembic (parte de Sprint 6, pero conviene
   adelantarlo si el esquema va a cambiar seguido) — recordar incluir
   `install_notify_triggers()` en esa migración.
