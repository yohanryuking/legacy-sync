# legacy-sync

Pipeline de migración de datos entre un sistema legado (con inconsistencias,
duplicados, campos faltantes) y un almacén moderno, sin perder información y
sin duplicarla si el proceso se corre dos veces.

**Stack:** Python (ETL) · SQLAlchemy 2.0 · PostgreSQL · FastAPI (dashboard, Fase 2) · React (Fase 2)

> Nota sobre el stack original: la propuesta inicial usaba Supabase como
> destino. Este repo usa **PostgreSQL simple** (vía SQLAlchemy) en su lugar —
> ver [docs/DECISIONS.md](docs/DECISIONS.md#adr-001-postgresql-en-vez-de-supabase)
> para el porqué. El dashboard en tiempo real (Fase 2) usa `LISTEN/NOTIFY` de
> Postgres + WebSockets de FastAPI, reemplazando Supabase Realtime.

## Estado del proyecto

✅ **Fase 1 implementada** (Sprints 0-2 del roadmap original): setup, seed de
datos sucios, extracción + validación con Pydantic, carga idempotente con
upsert por clave natural.

📋 **Fases siguientes documentadas, no implementadas**: cola de reintentos con
backoff activo, dashboard en tiempo real, checkpointing, deploy. Ver
[docs/ROADMAP.md](docs/ROADMAP.md).

## Documentación

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — cómo está armado el pipeline y por qué.
- [docs/ROADMAP.md](docs/ROADMAP.md) — sprints completados y pendientes, con criterios de aceptación.
- [docs/DECISIONS.md](docs/DECISIONS.md) — decisiones de diseño (ADRs) y sus alternativas descartadas.

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

### 5. Correr los tests

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
  models/        # SQLAlchemy: legado, destino, logs, cola de reintentos
  schemas.py     # Pydantic: validación estricta por registro
  etl/
    extract.py   # lee el legado
    validate.py  # aisla errores por registro (no rompe el batch)
    checksum.py  # detecta si un registro realmente cambió
    load.py      # upsert idempotente por clave natural
    pipeline.py  # orquesta extract -> validate -> load
  seed.py        # genera datos "sucios" de prueba
  cli.py         # comandos: init-db, seed, migrate, status
tests/           # incluye el test de idempotencia (correr 2x, 0 duplicados)
docs/            # arquitectura, roadmap, decisiones
```
