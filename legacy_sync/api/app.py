"""API minima de Fase 2 (Sprint 3): solo el endpoint de reintento forzado.

El resto del dashboard (resumen de progreso, lista de fallos, WebSocket en
vivo) es Sprint 4 -- ver docs/ROADMAP.md. Este archivo existe ya porque el
boton "forzar reintento" de un registro especifico necesita algo a lo que
llamar.
"""

from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from legacy_sync.db import SessionLocal
from legacy_sync.etl.retry_worker import attempt_retry
from legacy_sync.models.target import RetryQueueItem

app = FastAPI(title="legacy-sync API", version="0.1.0")


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
        )


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
