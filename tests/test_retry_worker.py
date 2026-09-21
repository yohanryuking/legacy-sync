from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import func, select

from legacy_sync.etl.load import upsert_customer
from legacy_sync.etl.retry_worker import attempt_retry, process_retry_queue
from legacy_sync.models.target import MigratedCustomer, RetryQueueItem
from legacy_sync.schemas import CustomerRecord

_PAYLOAD = {
    "legacy_id": 1,
    "full_name": "Ines Gomez",
    "email": "ines@example.com",
    "birth_date": "1993-07-07",
    "phone": "555-11",
    "source_created_at": "2024-01-01T10:00:00",
}


def _queued_item(**overrides) -> RetryQueueItem:
    defaults = dict(
        legacy_id=1,
        natural_key="ines@example.com",
        payload=_PAYLOAD,
        attempt_count=0,
        max_attempts=3,
        status="pending",
        next_attempt_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    defaults.update(overrides)
    return RetryQueueItem(**defaults)


def test_successful_retry_loads_the_record_and_marks_succeeded(session):
    item = _queued_item()
    session.add(item)
    session.commit()

    result = process_retry_queue(session)
    session.commit()

    assert result.succeeded == 1
    assert result.attempted == 1
    refreshed = session.get(RetryQueueItem, item.id)
    assert refreshed.status == "succeeded"

    migrated = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert migrated == 1


def test_pending_item_before_next_attempt_at_is_not_touched(session):
    item = _queued_item(next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1))
    session.add(item)
    session.commit()

    result = process_retry_queue(session)

    assert result.attempted == 0
    refreshed = session.get(RetryQueueItem, item.id)
    assert refreshed.status == "pending"


def test_failed_retry_reschedules_with_growing_backoff(session):
    item = _queued_item(max_attempts=5)
    session.add(item)
    session.commit()

    with patch("legacy_sync.etl.retry_worker.upsert_customer", side_effect=RuntimeError("still down")):
        attempt_retry(session, item)
        first_delay = item.next_attempt_at - datetime.now(timezone.utc)

        attempt_retry(session, item)
        second_delay = item.next_attempt_at - datetime.now(timezone.utc)

    session.commit()

    assert item.attempt_count == 2
    assert item.status == "pending"
    # El backoff exponencial hace que el segundo intento espere sensiblemente
    # mas que el primero (2**2 vs 2**1 veces la base), incluso con jitter.
    assert second_delay > first_delay


def test_item_is_marked_failed_after_exhausting_max_attempts(session):
    item = _queued_item(max_attempts=2, attempt_count=1)
    session.add(item)
    session.commit()

    with patch("legacy_sync.etl.retry_worker.upsert_customer", side_effect=RuntimeError("still down")):
        result = process_retry_queue(session)
    session.commit()

    assert result.failed_permanently == 1
    refreshed = session.get(RetryQueueItem, item.id)
    assert refreshed.status == "failed"
    assert refreshed.attempt_count == 2


def test_forced_retry_ignores_next_attempt_at(session):
    """El endpoint de reintento forzado usa attempt_retry directamente,
    salteando el filtro de next_attempt_at que usa process_retry_queue."""
    item = _queued_item(next_attempt_at=datetime.now(timezone.utc) + timedelta(days=1))
    session.add(item)
    session.commit()

    success = attempt_retry(session, item)
    session.commit()

    assert success
    assert item.status == "succeeded"


def test_already_migrated_record_via_retry_does_not_duplicate(session):
    """Si el registro ya fue cargado por otra via (ej. una corrida normal
    del pipeline) mientras estaba en la cola, el reintento debe seguir
    siendo idempotente."""
    record = CustomerRecord.model_validate(_PAYLOAD)
    upsert_customer(session, record)
    session.commit()

    item = _queued_item()
    session.add(item)
    session.commit()

    process_retry_queue(session)
    session.commit()

    migrated = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert migrated == 1
