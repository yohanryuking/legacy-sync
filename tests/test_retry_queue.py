from unittest.mock import patch

from sqlalchemy import func, select

from legacy_sync.etl.pipeline import run_pipeline
from legacy_sync.models.legacy import LegacyCustomer
from legacy_sync.models.target import MigratedCustomer, RetryQueueItem


def test_load_failure_goes_to_retry_queue_without_losing_other_records(session):
    session.add_all(
        [
            LegacyCustomer(
                legacy_id=1,
                full_name="Gina Lopez",
                email="gina@example.com",
                birth_date_raw="1995-09-09",
                phone="555-7",
                created_at_raw="2024-01-01T10:00:00",
            ),
            LegacyCustomer(
                legacy_id=2,
                full_name="Hugo Nunez",
                email="hugo@example.com",
                birth_date_raw="1996-10-10",
                phone="555-8",
                created_at_raw="2024-01-01T10:00:00",
            ),
        ]
    )
    session.commit()

    # Fuerza que el primer intento de carga falle (simula un timeout de red)
    # y el segundo tenga exito, para probar que un fallo no afecta al resto.
    with patch("legacy_sync.etl.pipeline._maybe_simulate_failure", side_effect=[RuntimeError("boom"), None]):
        result = run_pipeline(session, simulate_load_failures=True)
    session.commit()

    assert result.valid == 2
    assert result.load_failed == 1
    assert result.inserted == 1

    migrated_count = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert migrated_count == 1

    queued = session.scalar(select(func.count()).select_from(RetryQueueItem))
    assert queued == 1
    item = session.query(RetryQueueItem).one()
    assert item.legacy_id == 1
    assert item.status == "pending"
