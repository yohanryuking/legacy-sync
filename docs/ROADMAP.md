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

## ✅ Fase 2 (parcial) — Sprint 5: checkpointing y resiliencia

- [x] Tabla `migration_checkpoints` (`run_id`, `last_legacy_id_processed`,
      `status`, `started_at`, `updated_at`) — `legacy_sync/models/target.py`.
- [x] `run_pipeline()` acepta `resume_run_id`/`resume_last`: sin ninguno de
      los dos, cada corrida es nueva (`run_id` fresco) y arranca desde
      `legacy_id > 0` — **el comportamiento por defecto no cambió**, así
      que el escenario central del proyecto ("correr la migración dos
      veces no duplica nada") sigue siendo exactamente el mismo. Con
      `--resume`/`--resume-last`, arranca en
      `legacy_id > last_legacy_id_processed` de esa corrida.
- [x] El checkpoint se confirma (`session.commit()`) cada
      `checkpoint_every` registros (default 200, configurable por CLI) y
      al finalizar — una interrupción pierde como mucho ese lote parcial,
      nunca la corrida completa.
- [x] CLI: `legacy-sync migrate --resume <run_id>` / `--resume-last`, y
      `legacy-sync runs` para listar corridas y su punto de avance sin
      tener que copiar un UUID a mano.
- [x] Tests (`tests/test_checkpoint.py`): corrida nueva siempre re-escanea
      todo; el checkpoint llega hasta el último `legacy_id` al completar;
      **una excepción forzada a mitad del batch, seguida de `--resume`,
      procesa solo los registros restantes** (el test central);
      `--resume-last` elige la corrida `running` más reciente; `--resume`
      con un `run_id` inexistente, o `--resume-last` sin ninguna corrida
      interrumpida, lanzan un error claro en vez de fallar en silencio.
- [x] Verificado manualmente contra Postgres real con un **`kill -9`
      real** (no solo una excepción de Python) a mitad de una migración
      de 54 registros: la corrida quedó `running` en el checkpoint,
      `legacy-sync migrate --resume-last` retomó exactamente después del
      último confirmado, y una corrida normal posterior (sin `--resume`)
      re-escaneó las 54 filas sin duplicar nada.

**Criterio de aceptación (cumplido)**: matar el proceso de migración a
mitad de camino (`kill -9` o excepción forzada) y, al volver a correrlo con
`--resume-last`, no vuelve a reprocesar los registros ya confirmados — y
sigue siendo idempotente.

### ✅ Sprint 6 (parcial) — Deploy y presentación

Lo que se puede dejar listo desde el repo, sin depender de cuentas o
credenciales externas:

- [x] **Alembic**: `alembic/` con dos migraciones versionadas (esquema
      inicial + triggers NOTIFY de Sprint 4). `legacy_sync/init_db.py`
      ahora corre `alembic upgrade head`/`downgrade base`
      programáticamente en vez de `Base.metadata.create_all()`. CI
      (`.github/workflows/tests.yml`, job `alembic`) levanta un Postgres
      real y corre `alembic upgrade head` + `alembic check` +
      `downgrade base && upgrade head` en cada push, para detectar
      apenas el historial de migraciones se desincronice de
      `legacy_sync/models/`.
- [x] **Scheduler real**: `legacy-sync worker [--interval N] [--once]`
      (`legacy_sync/cli.py`) — lo que le faltaba a `legacy-sync retry`
      (que solo corre una pasada). `--once` para invocarlo desde un cron
      externo; sin flags queda en loop, pensado para un contenedor/servicio
      dedicado.
- [x] **Contenedor único** (`Dockerfile`) para los tres roles (API,
      worker, CLI) — el comando decide el rol. `docker-compose.prod.yml`
      orquesta Postgres + API (corre `alembic upgrade head` antes de
      `uvicorn`) + worker, listo para correr localmente
      (`docker compose -f docker-compose.prod.yml up --build`) o servir
      de base a un deploy real.
- [x] Verificado manualmente de punta a punta: build de la imagen, y los
      tres contenedores (Postgres + API + worker) corriendo en red,
      exactamente la topología de `docker-compose.prod.yml` — seed,
      migración con fallos simulados, y el worker recogiendo
      `retry_queue` desde su propio contenedor, confirmado contra el
      dashboard vía HTTP. Detalle en [DEPLOY.md](DEPLOY.md).
- [ ] Si el dashboard crece más allá de tablas simples, evaluar migrar
      `legacy_sync/static/` a un SPA de React con bundler — no antes:
      ver ADR-008 sobre por qué no hace falta todavía.

**Dos ítems quedan fuera del alcance de este repo/agente, y son para quien
lo continúe con sus propias cuentas**:

- [ ] **Deploy real** a un proveedor gestionado (Railway/Fly/Render/etc.)
      — los pasos genéricos están en [DEPLOY.md](DEPLOY.md), pero
      ejecutarlos requiere una cuenta y credenciales que este repo no
      tiene ni debería tener.
- [ ] **Video demo** — requiere grabación manual; el guion sugerido está
      en [DEPLOY.md](DEPLOY.md#video-demo).

---

## Cómo continuar (para el próximo desarrollador)

1. Todos los sprints de funcionalidad e infraestructura de código están
   completos. Lo único que falta es ejecutar, con cuentas propias, el
   deploy real a un proveedor y grabar el video demo (ver DEPLOY.md).
2. Si el proyecto sigue evolucionando más allá de esto: el candidato más
   claro es ampliar el dashboard (Sprint 4) — más vistas, quizás ahí sí
   se justifica migrar a React (ver ADR-008).
3. Cualquier cambio de esquema nuevo va como migración de Alembic
   (`alembic revision --autogenerate -m "..."`, revisar el archivo
   generado antes de aplicarlo), nunca editando tablas a mano — ya no hay
   `metadata.create_all()` en el camino de `init_db()`.
