"""Notificaciones en tiempo real vía Postgres LISTEN/NOTIFY.

Reemplaza a Supabase Realtime (ver docs/DECISIONS.md ADR-001): en vez de una
suscripcion a nivel de fila gestionada por un tercero, se usan triggers de
Postgres que emiten `NOTIFY` cada vez que una tabla relevante cambia, y un
listener de `asyncpg` (en `legacy_sync/api/listener.py`) que reenvia ese
aviso a los WebSockets conectados.

El pipeline (`legacy_sync/cli.py migrate`/`retry`) corre como un proceso
separado del API server -- por eso no alcanza con "avisar en memoria" desde
el mismo proceso: hace falta un canal a nivel de base de datos para que un
cambio hecho por la CLI llegue al dashboard.

La funcion y los triggers de NOTIFY se instalan vía la migracion de Alembic
`alembic/versions/bed3584fc685_*.py` (Sprint 6), no desde este modulo -- ver
el docstring de esa migracion para el porque de duplicar el SQL en vez de
llamar a codigo compartido.
"""

from __future__ import annotations

NOTIFY_CHANNEL = "legacy_sync_events"


def to_asyncpg_dsn(sqlalchemy_url: str) -> str:
    """Convierte una URL de SQLAlchemy (`postgresql+psycopg://...`) al DSN
    plano que espera `asyncpg.connect()` (`postgresql://...`)."""
    scheme, _, rest = sqlalchemy_url.partition("://")
    driver = scheme.split("+")[0]
    return f"{driver}://{rest}"
