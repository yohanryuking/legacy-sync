"""notify triggers para el dashboard en tiempo real

Instala la funcion y los triggers de LISTEN/NOTIFY que alimentan el
WebSocket del dashboard (Sprint 4, ver legacy_sync/realtime.py). El SQL se
copia aca en vez de importarlo desde legacy_sync/realtime.py a proposito:
una migracion debe seguir funcionando igual aunque el codigo de la
aplicacion cambie despues -- es un snapshot de lo que se ejecuto en este
punto de la historia del esquema, no una llamada a codigo "vivo".

No-op fuera de Postgres (por si alguna vez se corre esta migracion contra
otro motor -- en este proyecto solo se usa contra Postgres; los tests usan
SQLite directamente vía `Base.metadata.create_all`, sin pasar por Alembic).

Revision ID: bed3584fc685
Revises: b4140c57c5a0
Create Date: 2026-09-25 21:06:12.291409

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bed3584fc685'
down_revision: Union[str, Sequence[str], None] = 'b4140c57c5a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOTIFY_CHANNEL = "legacy_sync_events"
WATCHED_TABLES = ("migrated_customers", "migration_logs", "retry_queue")

FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION legacy_sync_notify() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('{NOTIFY_CHANNEL}', TG_TABLE_NAME);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION_SQL = "DROP FUNCTION IF EXISTS legacy_sync_notify();"


def _trigger_sql(table: str) -> str:
    trigger_name = f"trg_notify_{table}"
    return f"""
    DROP TRIGGER IF EXISTS {trigger_name} ON {table};
    CREATE TRIGGER {trigger_name}
    AFTER INSERT OR UPDATE ON {table}
    FOR EACH ROW EXECUTE FUNCTION legacy_sync_notify();
    """


def _drop_trigger_sql(table: str) -> str:
    return f"DROP TRIGGER IF EXISTS trg_notify_{table} ON {table};"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(FUNCTION_SQL)
    for table in WATCHED_TABLES:
        op.execute(_trigger_sql(table))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in WATCHED_TABLES:
        op.execute(_drop_trigger_sql(table))
    op.execute(DROP_FUNCTION_SQL)
