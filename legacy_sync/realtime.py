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
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

NOTIFY_CHANNEL = "legacy_sync_events"

_WATCHED_TABLES = ("migrated_customers", "migration_logs", "retry_queue")

_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION legacy_sync_notify() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('{NOTIFY_CHANNEL}', TG_TABLE_NAME);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def _trigger_sql(table: str) -> str:
    trigger_name = f"trg_notify_{table}"
    return f"""
    DROP TRIGGER IF EXISTS {trigger_name} ON {table};
    CREATE TRIGGER {trigger_name}
    AFTER INSERT OR UPDATE ON {table}
    FOR EACH ROW EXECUTE FUNCTION legacy_sync_notify();
    """


def install_notify_triggers(engine: Engine) -> bool:
    """Crea la funcion y los triggers de NOTIFY. No-op fuera de Postgres.

    Retorna True si se instalaron (dialecto Postgres), False si se saltearon
    (ej. SQLite en tests, donde no existen LISTEN/NOTIFY ni PL/pgSQL).
    """
    if engine.dialect.name != "postgresql":
        return False

    with engine.begin() as conn:
        conn.execute(text(_FUNCTION_SQL))
        for table in _WATCHED_TABLES:
            conn.execute(text(_trigger_sql(table)))
    return True


def to_asyncpg_dsn(sqlalchemy_url: str) -> str:
    """Convierte una URL de SQLAlchemy (`postgresql+psycopg://...`) al DSN
    plano que espera `asyncpg.connect()` (`postgresql://...`)."""
    scheme, _, rest = sqlalchemy_url.partition("://")
    driver = scheme.split("+")[0]
    return f"{driver}://{rest}"
