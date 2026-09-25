# legacy-sync

Pipeline de migración de datos entre un sistema legado (con inconsistencias,
duplicados, campos faltantes) y un almacén moderno, sin perder información y
sin duplicarla si el proceso se corre dos veces.

**Stack:** Python (ETL) · SQLAlchemy 2.0 · PostgreSQL · FastAPI + WebSocket (dashboard) · HTML/JS estático (frontend)

> Nota sobre el stack original: la propuesta inicial usaba Supabase como
> destino y Supabase Realtime + React para el dashboard. Este repo usa
> **PostgreSQL simple** (vía SQLAlchemy) — ver
> [docs/DECISIONS.md](docs/DECISIONS.md#adr-001-postgresql-en-vez-de-supabase)
> — y reemplaza Supabase Realtime por `LISTEN/NOTIFY` de Postgres +
> WebSockets de FastAPI, con un frontend HTML/JS sin build step (ver
> [ADR-008](docs/DECISIONS.md#adr-008-frontend-estatico-sin-build-step-en-vez-de-un-spa-de-react)).

## Estado del proyecto

✅ **Fase 1** (Sprints 0-2): setup, seed de datos sucios, extracción +
validación con Pydantic, carga idempotente con upsert por clave natural.

✅ **Fase 2, Sprint 3**: worker de reintentos con backoff exponencial
(`legacy_sync/etl/retry_worker.py`) + API FastAPI mínima con el endpoint de
reintento forzado.

✅ **Fase 2, Sprint 4**: dashboard en tiempo real — triggers de Postgres
(`LISTEN/NOTIFY`) + listener `asyncpg` + WebSocket (`legacy_sync/api/`), y
un frontend estático (`legacy_sync/static/`) que se actualiza solo, sin
recargar la página, cuando otro proceso (`migrate`/`retry`) cambia algo.

✅ **Fase 2, Sprint 5**: checkpointing — `legacy-sync migrate --resume-last`
retoma una corrida interrumpida (`kill -9` a mitad de camino) justo después
del último registro confirmado, sin releer todo el legado desde cero.

✅ **Fase 2, Sprint 6** (parcial): migraciones versionadas con Alembic,
scheduler real (`legacy-sync worker`), imagen de Docker única y
`docker-compose.prod.yml` para correr API + worker + Postgres juntos —
verificado de punta a punta. **Deploy real a un proveedor gestionado y el
video demo quedan pendientes**: requieren cuentas/credenciales propias y
grabación manual respectivamente — ver [docs/DEPLOY.md](docs/DEPLOY.md).

Ver [docs/ROADMAP.md](docs/ROADMAP.md) para el detalle sprint por sprint.

## Documentación

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — cómo está armado el pipeline y por qué.
- [docs/ROADMAP.md](docs/ROADMAP.md) — sprints completados y pendientes, con criterios de aceptación.
- [docs/DECISIONS.md](docs/DECISIONS.md) — decisiones de diseño (ADRs) y sus alternativas descartadas.
- [docs/DEPLOY.md](docs/DEPLOY.md) — cómo correr el stack en contenedores y llevarlo a un proveedor gestionado.

## Quickstart

### 1. Levantar Postgres

```bash
docker compose up -d
cp .env.example .env
```

(Si no tenés Docker disponible, cualquier Postgres 14+ sirve — solo ajustá
`DATABASE_URL` en `.env`.)

### 2. Instalar dependencias

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Crear tablas, generar datos sucios y migrar

```bash
python -m legacy_sync init-db
python -m legacy_sync seed --count 2000
python -m legacy_sync migrate
python -m legacy_sync status
```

### 4. Comprobar idempotencia (el demo central del proyecto)

```bash
python -m legacy_sync migrate   # correrlo de nuevo
python -m legacy_sync status    # migrated_customers no debe crecer
```

### 5. Probar la cola de reintentos (Sprint 3)

```bash
python -m legacy_sync migrate --simulate-load-failures   # fuerza fallos aleatorios de carga
python -m legacy_sync status                              # ver items en retry_queue
python -m legacy_sync retry                               # procesa la cola con backoff
python -m legacy_sync status                              # los exitosos ya están en migrated_customers
```

### 6. Levantar el dashboard en tiempo real (Sprint 4)

```bash
uvicorn legacy_sync.api.app:app --reload
```

Abrí http://localhost:8000 — vas a ver las tarjetas de resumen, la cola de
reintentos (con botón "Reintentar ahora") y los fallos de validación. Dejalo
abierto y, en otra terminal, corré:

```bash
python -m legacy_sync migrate --simulate-load-failures
python -m legacy_sync retry
```

Los contadores se actualizan solos — la API recibe el aviso vía
`LISTEN/NOTIFY` de Postgres desde el proceso de la CLI (otro proceso
completamente distinto) y lo reenvía por WebSocket, sin polling.

Endpoints REST expuestos: `GET /migrations/summary`,
`GET /migrations/validation-failures`, `GET /retry-queue`,
`POST /retry-queue/{id}/retry`, `WS /migrations/live`.

### 7. Simular un crash y retomar la migración (Sprint 5)

```bash
python -m legacy_sync seed --count 5000
python -m legacy_sync migrate &   # correrla en background
sleep 1 && kill -9 %1             # simular un crash a mitad de camino
python -m legacy_sync runs        # ver la corrida que quedó "running" y su run_id
python -m legacy_sync migrate --resume-last   # retoma justo después del último confirmado
python -m legacy_sync runs        # ahora "completed"
```

### 8. Correr el scheduler de reintentos (Sprint 6)

```bash
python -m legacy_sync worker --interval 30   # loop en foreground, Ctrl+C para detener
python -m legacy_sync worker --once          # una sola pasada, para invocar desde cron
```

### 9. Correr todo en contenedores (Sprint 6)

```bash
docker compose -f docker-compose.prod.yml up --build
docker compose -f docker-compose.prod.yml run --rm api legacy-sync seed --count 2000
docker compose -f docker-compose.prod.yml run --rm api legacy-sync migrate
```

Postgres + API (corre `alembic upgrade head` sola al arrancar) + worker,
los tres en contenedores separados. Detalle y cómo llevarlo a un
proveedor gestionado en [docs/DEPLOY.md](docs/DEPLOY.md).

### 10. Correr los tests

```bash
pytest -q
```

Los tests usan SQLite en memoria (misma capa de modelos/ETL, rama de
dialecto distinta en el upsert) para no depender de una instancia de Postgres
en CI. `tests/test_load_idempotent.py` es el test que prueba exactamente el
escenario "correr la migración dos veces no duplica nada".

## Estructura del repo

```
legacy_sync/
  models/        # SQLAlchemy: legado, destino, logs, cola de reintentos, checkpoints
  schemas.py     # Pydantic: validación estricta por registro
  realtime.py    # NOTIFY_CHANNEL + helper de DSN para asyncpg
  init_db.py     # aplica migraciones de Alembic (init-db / init-db --reset)
  etl/
    extract.py      # lee el legado (soporta after_legacy_id para resume)
    validate.py     # aisla errores por registro (no rompe el batch)
    checksum.py     # detecta si un registro realmente cambió
    load.py         # upsert idempotente por clave natural
    pipeline.py      # orquesta extract -> validate -> load, maneja checkpoints
    retry_worker.py  # procesa retry_queue con backoff exponencial (Sprint 3)
    checkpoint.py    # persistencia del punto de avance de una corrida (Sprint 5)
  api/
    app.py                # FastAPI: dashboard + reintento forzado (Sprint 3-4)
    listener.py           # LISTEN de Postgres vía asyncpg (Sprint 4)
    connection_manager.py # registro de WebSockets conectados (Sprint 4)
  static/        # frontend HTML/JS sin build step (Sprint 4)
  seed.py        # genera datos "sucios" de prueba
  cli.py         # comandos: init-db, seed, migrate, retry, worker, runs, status
alembic/         # migraciones versionadas del esquema (Sprint 6)
Dockerfile                 # imagen unica: API, worker o CLI segun el comando (Sprint 6)
docker-compose.prod.yml    # postgres + api + worker juntos (Sprint 6)
tests/           # incluye el test de idempotencia (correr 2x, 0 duplicados)
docs/            # arquitectura, roadmap, decisiones, deploy
```
