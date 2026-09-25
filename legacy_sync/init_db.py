"""Aplica el esquema de la DB configurada, vía Alembic (Sprint 6).

Fase 1 usaba `Base.metadata.create_all()` por simplicidad. Ahora el
esquema (tablas + los triggers de NOTIFY de Sprint 4) esta versionado en
`alembic/versions/` -- `init_db()` corre `alembic upgrade head`
programaticamente, en vez de recrear tablas a mano. Los modelos de
SQLAlchemy (`legacy_sync/models/`) siguen siendo la fuente de verdad para
`alembic revision --autogenerate`; lo que cambia es que ahora hay un
historial versionado de como se llego al esquema actual, no solo el
estado final.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"


def _alembic_config() -> Config:
    return Config(str(_ALEMBIC_INI))


def init_db() -> None:
    """Crea/actualiza el esquema aplicando todas las migraciones pendientes."""
    command.upgrade(_alembic_config(), "head")


def drop_db() -> None:
    """Revierte todas las migraciones: borra tablas, triggers y la funcion NOTIFY."""
    command.downgrade(_alembic_config(), "base")
