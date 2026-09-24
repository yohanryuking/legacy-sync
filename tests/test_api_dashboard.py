from datetime import datetime, timezone

from legacy_sync.models.legacy import LegacyCustomer
from legacy_sync.models.target import MigratedCustomer, MigrationLog, RetryQueueItem

_PAYLOAD = {
    "legacy_id": 1,
    "full_name": "Karen Ibarra",
    "email": "karen@example.com",
    "birth_date": "1990-01-01",
    "phone": "555-13",
    "source_created_at": "2024-01-01T10:00:00",
}


def test_summary_counts_every_stage(client, session):
    session.add(LegacyCustomer(legacy_id=1, full_name="A", email="a@example.com", birth_date_raw="1990-01-01"))
    session.add(LegacyCustomer(legacy_id=2, full_name="B", email="b@example.com", birth_date_raw="1990-01-01"))
    session.add(
        MigratedCustomer(
            natural_key="a@example.com",
            checksum="x",
            legacy_id=1,
            full_name="A",
            email="a@example.com",
            birth_date=datetime(1990, 1, 1).date(),
            source_created_at=datetime.now(timezone.utc),
        )
    )
    session.add(
        MigrationLog(
            legacy_id=2,
            stage="validation",
            success=False,
            error_field="email",
            error_message="email invalido",
            raw_payload={},
        )
    )
    session.add(
        RetryQueueItem(
            legacy_id=3,
            natural_key="c@example.com",
            payload=_PAYLOAD,
            status="pending",
        )
    )
    session.add(
        RetryQueueItem(
            legacy_id=4,
            natural_key="d@example.com",
            payload=_PAYLOAD,
            status="failed",
            attempt_count=5,
            max_attempts=5,
        )
    )
    session.add(
        RetryQueueItem(
            legacy_id=5,
            natural_key="e@example.com",
            payload=_PAYLOAD,
            status="succeeded",
        )
    )
    session.commit()

    response = client.get("/migrations/summary")

    assert response.status_code == 200
    assert response.json() == {
        "total_legacy": 2,
        "migrated": 1,
        "validation_failed": 1,
        "retry_pending": 1,
        "retry_failed_permanent": 1,
        "retry_succeeded": 1,
    }


def test_validation_failures_lists_only_validation_stage_logs(client, session):
    session.add(
        MigrationLog(
            legacy_id=1,
            stage="validation",
            success=False,
            error_field="email",
            error_message="email invalido",
            raw_payload={},
        )
    )
    session.add(
        MigrationLog(
            legacy_id=2,
            stage="load",
            success=False,
            error_message="timeout simulado",
            raw_payload={},
        )
    )
    session.commit()

    response = client.get("/migrations/validation-failures")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["legacy_id"] == 1
    assert body[0]["error_field"] == "email"


def test_retry_queue_endpoint_filters_by_status(client, session):
    session.add(RetryQueueItem(legacy_id=1, natural_key="a@example.com", payload=_PAYLOAD, status="pending"))
    session.add(RetryQueueItem(legacy_id=2, natural_key="b@example.com", payload=_PAYLOAD, status="succeeded"))
    session.commit()

    response = client.get("/retry-queue", params={"status": "pending"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["legacy_id"] == 1


def test_websocket_connects_without_a_running_postgres_listener(client):
    """El listener de LISTEN/NOTIFY es un proceso aparte (ver lifespan);
    el endpoint de WebSocket en si no depende de que ese listener este
    corriendo -- solo depende del ConnectionManager."""
    with client.websocket_connect("/migrations/live") as websocket:
        websocket.close()
