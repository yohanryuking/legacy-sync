"""API del dashboard (Sprint 3 + Sprint 4).

Sprint 3 aporto el endpoint de reintento forzado. Sprint 4 agrega el resto:
resumen de progreso, listas de fallos, y un WebSocket que avisa en vivo
cuando algo cambia (alimentado por Postgres LISTEN/NOTIFY, ver
legacy_sync/realtime.py y legacy_sync/api/listener.py -- reemplaza a
Supabase Realtime, ver docs/DECISIONS.md ADR-001).

El frontend es una pagina estatica sin build step (legacy_sync/static/),
servida por esta misma app. Ver ADR-008 en docs/DECISIONS.md para el
porque de no usar un SPA de React con bundler.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from legacy_sync.api.connection_manager import ConnectionManager
from legacy_sync.api.listener import listen_for_notifications
from legacy_sync.db import SessionLocal, engine
from legacy_sync.etl.retry_worker import attempt_retry
from legacy_sync.models.legacy import LegacyCustomer
from legacy_sync.models.target import MigratedCustomer, MigrationLog, RetryQueueItem
from legacy_sync.realtime import to_asyncpg_dsn

logger = logging.getLogger(__name__)

manager = ConnectionManager()
_listener_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _listener_task
    if engine.dialect.name == "postgresql":
        dsn = to_asyncpg_dsn(engine.url.render_as_string(hide_password=False))
        _listener_task = asyncio.create_task(listen_for_notifications(dsn, manager))
    else:
        logger.info("Dialecto '%s' no soporta LISTEN/NOTIFY; el WS no recibira eventos de otros procesos.", engine.dialect.name)

    yield

    if _listener_task is not None:
        _listener_task.cancel()
        _listener_task = None


app = FastAPI(title="legacy-sync API", version="0.1.0", lifespan=lifespan)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class RetryItemOut(BaseModel):
    id: int
    legacy_id: int
    natural_key: str
    status: str
    attempt_count: int
    max_attempts: int
    last_error: str | None
    next_attempt_at: datetime

    @classmethod
    def from_model(cls, item: RetryQueueItem) -> "RetryItemOut":
        return cls(
            id=item.id,
            legacy_id=item.legacy_id,
            natural_key=item.natural_key,
            status=item.status,
            attempt_count=item.attempt_count,
            max_attempts=item.max_attempts,
            last_error=item.last_error,
            next_attempt_at=item.next_attempt_at,
        )


class MigrationSummary(BaseModel):
    total_legacy: int
    migrated: int
    validation_failed: int
    retry_pending: int
    retry_failed_permanent: int
    retry_succeeded: int


class ValidationFailureOut(BaseModel):
    id: int
    legacy_id: int | None
    error_field: str | None
    error_message: str | None
    created_at: datetime


@app.post("/retry-queue/{item_id}/retry", response_model=RetryItemOut)
def force_retry(item_id: int, session: Session = Depends(get_session)) -> RetryItemOut:
    """Fuerza el reintento inmediato de un item, salteando `next_attempt_at`.

    Si el item ya esta 'succeeded', no vuelve a intentar la carga -- solo
    devuelve su estado actual (evita re-ejecutar una carga que ya se
    confirmo, por mas que alguien apriete el boton dos veces).
    """
    item = session.get(RetryQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="retry_queue item no encontrado")

    if item.status != "succeeded":
        attempt_retry(session, item)
        session.flush()

    return RetryItemOut.from_model(item)


@app.get("/retry-queue", response_model=list[RetryItemOut])
def list_retry_queue(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> list[RetryItemOut]:
    stmt = select(RetryQueueItem).order_by(RetryQueueItem.id.desc()).limit(limit).offset(offset)
    if status is not None:
        stmt = stmt.where(RetryQueueItem.status == status)
    items = session.execute(stmt).scalars().all()
    return [RetryItemOut.from_model(item) for item in items]


@app.get("/migrations/summary", response_model=MigrationSummary)
def migrations_summary(session: Session = Depends(get_session)) -> MigrationSummary:
    """Conteos actuales de cada etapa. El frontend la vuelve a pedir cada vez
    que el WebSocket avisa que algo cambio (ver ADR-009 en docs/DECISIONS.md
    -- "invalidar y refetchear" en vez de reconciliar estado incremental)."""

    def count(stmt) -> int:
        return session.scalar(stmt) or 0

    return MigrationSummary(
        total_legacy=count(select(func.count()).select_from(LegacyCustomer)),
        migrated=count(select(func.count()).select_from(MigratedCustomer)),
        validation_failed=count(
            select(func.count()).select_from(MigrationLog).where(MigrationLog.stage == "validation")
        ),
        retry_pending=count(
            select(func.count()).select_from(RetryQueueItem).where(RetryQueueItem.status == "pending")
        ),
        retry_failed_permanent=count(
            select(func.count()).select_from(RetryQueueItem).where(RetryQueueItem.status == "failed")
        ),
        retry_succeeded=count(
            select(func.count()).select_from(RetryQueueItem).where(RetryQueueItem.status == "succeeded")
        ),
    )


@app.get("/migrations/validation-failures", response_model=list[ValidationFailureOut])
def validation_failures(
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> list[ValidationFailureOut]:
    """Registros que nunca llegaron a ser validos -- no son candidatos a
    reintento (ver ADR-005): el dato de origen es el que esta mal, no la
    infraestructura."""
    stmt = (
        select(MigrationLog)
        .where(MigrationLog.stage == "validation")
        .order_by(MigrationLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    logs = session.execute(stmt).scalars().all()
    return [
        ValidationFailureOut(
            id=log.id,
            legacy_id=log.legacy_id,
            error_field=log.error_field,
            error_message=log.error_message,
            created_at=log.created_at,
        )
        for log in logs
    ]


@app.websocket("/migrations/live")
async def migrations_live(websocket: WebSocket) -> None:
    """Avisa a este socket cada vez que `migrated_customers`, `migration_logs`
    o `retry_queue` cambian en Postgres. No manda datos, solo el nombre de
    la tabla afectada -- el cliente reacciona re-pidiendo el resumen/listas."""
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="dashboard")
