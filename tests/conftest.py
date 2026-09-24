import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from legacy_sync.models import Base


@pytest.fixture()
def session_factory():
    """Motor SQLite en memoria, aislado por test. Misma capa de modelos/ETL
    que Postgres en produccion; el upsert usa la rama de dialecto correcta
    (ver legacy_sync/etl/load.py).

    `StaticPool` + `check_same_thread=False`: una sola conexion compartida
    entre threads, necesaria porque el TestClient de FastAPI corre los
    endpoints sincronos en un thread pool distinto del test.
    """
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield factory
    engine.dispose()


@pytest.fixture()
def session(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture()
def client(session_factory):
    """TestClient de la API con `get_session` apuntando al SQLite de test.

    Se instancia sin `with`, a proposito: eso evita disparar el lifespan de
    la app (que intentaria abrir una conexion `asyncpg` real a Postgres
    para LISTEN/NOTIFY) durante los tests.
    """
    from fastapi.testclient import TestClient

    from legacy_sync.api.app import app, get_session

    def _override():
        s = session_factory()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()
