"""Manejo de `migration_checkpoints` (Sprint 5).

`legacy_sync/etl/pipeline.py` usa estas funciones para llevar el punto de
avance de una corrida y poder retomarla si se interrumpe. Ver el docstring
de `MigrationCheckpoint` (`legacy_sync/models/target.py`) para el porque de
que una corrida nueva siempre arranque desde cero salvo que se pida
`--resume` explicitamente.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from legacy_sync.models.target import MigrationCheckpoint


def start_new_run(session: Session) -> MigrationCheckpoint:
    """Crea y persiste el checkpoint de una corrida nueva, desde cero."""
    checkpoint = MigrationCheckpoint(run_id=uuid.uuid4().hex, last_legacy_id_processed=0, status="running")
    session.add(checkpoint)
    session.commit()
    return checkpoint


def load_run_for_resume(session: Session, run_id: str | None) -> MigrationCheckpoint:
    """Busca la corrida a retomar.

    Con `run_id` explicito, esa corrida (existe o lanza `ValueError`). Sin
    `run_id` (`--resume-last`), la corrida `running` mas reciente -- pensado
    para el caso comun de "se me murio el proceso, seguí donde quedó" sin
    tener que copiar un UUID a mano.
    """
    if run_id is not None:
        checkpoint = session.get(MigrationCheckpoint, run_id)
        if checkpoint is None:
            raise ValueError(f"No existe ninguna corrida con run_id={run_id!r}")
        return checkpoint

    stmt = (
        select(MigrationCheckpoint)
        .where(MigrationCheckpoint.status == "running")
        .order_by(MigrationCheckpoint.started_at.desc())
    )
    checkpoint = session.execute(stmt).scalars().first()
    if checkpoint is None:
        raise ValueError("No hay ninguna corrida interrumpida para retomar")
    return checkpoint


def save_progress(session: Session, checkpoint: MigrationCheckpoint, legacy_id: int) -> None:
    """Actualiza el punto de avance y confirma la transaccion.

    El `commit()` es lo que hace que el checkpoint sobreviva a un crash:
    todo lo que el pipeline escribio hasta este punto (migrated_customers,
    migration_logs, retry_queue) queda durable junto con el propio
    checkpoint, en la misma transaccion.
    """
    checkpoint.last_legacy_id_processed = legacy_id
    session.commit()


def mark_completed(session: Session, checkpoint: MigrationCheckpoint) -> None:
    checkpoint.status = "completed"
    session.commit()
