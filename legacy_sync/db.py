from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from legacy_sync.config import get_settings


def make_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    return create_engine(url, future=True)


# Engine/sessionmaker por defecto, apuntando a la DB configurada por entorno.
engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker = SessionLocal) -> Iterator[Session]:
    """Contexto transaccional: commit si todo va bien, rollback si algo lanza."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
