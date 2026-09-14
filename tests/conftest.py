import pytest
from sqlalchemy.orm import sessionmaker

from legacy_sync.db import make_engine
from legacy_sync.models import Base


@pytest.fixture()
def session_factory():
    """Motor SQLite en memoria, aislado por test. Misma capa de modelos/ETL
    que Postgres en produccion; el upsert usa la rama de dialecto correcta
    (ver legacy_sync/etl/load.py).
    """
    engine = make_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield factory
    engine.dispose()


@pytest.fixture()
def session(session_factory):
    with session_factory() as s:
        yield s
