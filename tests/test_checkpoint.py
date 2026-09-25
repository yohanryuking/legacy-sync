from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from legacy_sync.etl import pipeline as pipeline_module
from legacy_sync.etl.pipeline import run_pipeline
from legacy_sync.models.legacy import LegacyCustomer
from legacy_sync.models.target import MigratedCustomer, MigrationCheckpoint


def _seed_legacy(session, count: int) -> None:
    session.add_all(
        LegacyCustomer(
            legacy_id=i,
            full_name=f"Persona {i}",
            email=f"persona{i}@example.com",
            birth_date_raw="1990-01-01",
            phone="555-0",
            created_at_raw="2024-01-01T10:00:00",
        )
        for i in range(1, count + 1)
    )
    session.commit()


def test_fresh_run_always_starts_from_the_beginning(session):
    """Sin --resume, cada corrida es nueva: el escenario central del
    proyecto ('correr la migracion dos veces no duplica nada') no cambia
    por la existencia de checkpointing."""
    _seed_legacy(session, 3)

    first = run_pipeline(session)
    session.commit()
    second = run_pipeline(session)
    session.commit()

    assert first.total == 3
    assert second.total == 3  # re-escaneo completo, no se salteo nada
    assert first.run_id != second.run_id

    migrated = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert migrated == 3


def test_completed_run_checkpoint_reaches_the_last_legacy_id(session):
    _seed_legacy(session, 5)

    result = run_pipeline(session, checkpoint_every=2)
    session.commit()

    checkpoint = session.get(MigrationCheckpoint, result.run_id)
    assert checkpoint.status == "completed"
    assert checkpoint.last_legacy_id_processed == 5


def test_interrupted_run_resumes_without_reprocessing_confirmed_records(session):
    """El escenario central de Sprint 5: matar el proceso a mitad de
    camino, y al retomarlo, no releer los registros ya confirmados."""
    _seed_legacy(session, 6)

    call_count = {"n": 0}
    original = pipeline_module.validate_and_build_log

    def flaky(raw):
        call_count["n"] += 1
        if call_count["n"] == 4:
            raise RuntimeError("crash simulado a mitad de la corrida")
        return original(raw)

    with patch("legacy_sync.etl.pipeline.validate_and_build_log", side_effect=flaky):
        with pytest.raises(RuntimeError, match="crash simulado"):
            run_pipeline(session, checkpoint_every=1)

    # Lo que ya se confirmo (checkpoint_every=1 -> commit tras cada registro)
    # sobrevive al "crash": los primeros 3 registros quedaron migrados.
    migrated_after_crash = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert migrated_after_crash == 3

    checkpoint = session.scalar(select(MigrationCheckpoint))
    assert checkpoint.status == "running"
    assert checkpoint.last_legacy_id_processed == 3
    run_id = checkpoint.run_id

    # Retomar: debe procesar solo los 3 registros restantes (legacy_id 4,5,6),
    # no los 6 originales.
    resumed = run_pipeline(session, resume_run_id=run_id)
    session.commit()

    assert resumed.total == 3
    assert resumed.run_id == run_id

    checkpoint_after = session.get(MigrationCheckpoint, run_id)
    assert checkpoint_after.status == "completed"
    assert checkpoint_after.last_legacy_id_processed == 6

    migrated_final = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert migrated_final == 6


def test_resume_last_picks_the_most_recent_running_checkpoint(session):
    _seed_legacy(session, 4)

    session.add(MigrationCheckpoint(run_id="old-completed", last_legacy_id_processed=4, status="completed"))
    session.add(MigrationCheckpoint(run_id="recent-running", last_legacy_id_processed=2, status="running"))
    session.commit()

    result = run_pipeline(session, resume_last=True)
    session.commit()

    assert result.run_id == "recent-running"
    assert result.total == 2  # legacy_id 3 y 4, retomando desde 2

    checkpoint = session.get(MigrationCheckpoint, "recent-running")
    assert checkpoint.status == "completed"
    assert checkpoint.last_legacy_id_processed == 4


def test_resume_run_id_not_found_raises(session):
    with pytest.raises(ValueError, match="No existe"):
        run_pipeline(session, resume_run_id="no-existe-este-run-id")


def test_resume_last_without_any_running_checkpoint_raises(session):
    with pytest.raises(ValueError, match="interrumpida"):
        run_pipeline(session, resume_last=True)
