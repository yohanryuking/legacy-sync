# Imagen unica para los tres roles del deploy (Sprint 6): API, worker de
# reintentos, y CLI de migracion. Cual rol corre lo decide el comando que
# se le pasa al contenedor (ver docker-compose.prod.yml y docs/DEPLOY.md).

FROM python:3.11-slim AS base

WORKDIR /app

# Dependencias del sistema para psycopg (driver de Postgres) en modo binario
# ya cubre la mayoria de los casos; libpq-dev queda por si el entorno de
# destino no tiene wheels binarios para su arquitectura.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY legacy_sync ./legacy_sync
COPY alembic ./alembic
COPY alembic.ini ./

# Instalacion editable (`-e`), no un build de wheel: este proyecto es una
# app, no una libreria para redistribuir. Con `pip install .` (sin -e),
# `legacy_sync/static/` no viaja al paquete instalado salvo que se declare
# explicitamente como package-data. `-e .` evita ese problema por completo:
# `import legacy_sync` resuelve directo a este arbol de archivos en /app
# (static/ y alembic.ini incluidos), sin ningun paso de empaquetado de por
# medio.
RUN pip install --no-cache-dir -e .

# Por defecto levanta la API; docker-compose.prod.yml sobreescribe el
# comando para el servicio de worker y para correr la migracion.
EXPOSE 8000
CMD ["uvicorn", "legacy_sync.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
