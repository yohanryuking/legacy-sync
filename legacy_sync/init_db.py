"""Crea todas las tablas (legado + destino) en la DB configurada.

Fase 1 usa `Base.metadata.create_all` por simplicidad. Cuando el esquema
empiece a evolucionar en produccion, migrar a Alembic (ver docs/ROADMAP.md,
Sprint 3+) para tener migraciones versionadas en vez de recrear tablas.
"""

from legacy_sync.db import engine
from legacy_sync.models import Base
from legacy_sync.realtime import install_notify_triggers


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    install_notify_triggers(engine)


def drop_db() -> None:
    Base.metadata.drop_all(bind=engine)
