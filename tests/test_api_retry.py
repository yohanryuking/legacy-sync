from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from legacy_sync.models.target import RetryQueueItem

_PAYLOAD = {
    "legacy_id": 1,
    "full_name": "Jose Marin",
    "email": "jose@example.com",
    "birth_date": "1991-02-02",
    "phone": "555-12",
    "source_created_at": "2024-01-01T10:00:00",
}


def test_force_retry_succeeds_and_returns_new_status(client, session):
    item = RetryQueueItem(
        legacy_id=1,
        natural_key="jose@example.com",
        payload=_PAYLOAD,
        attempt_count=1,
        max_attempts=3,
        status="pending",
        # bien en el futuro: el endpoint debe ignorar esto y reintentar ya.
        next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(item)
    session.commit()
    item_id = item.id

    response = client.post(f"/retry-queue/{item_id}/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["id"] == item_id


def test_force_retry_on_unknown_item_returns_404(client):
    response = client.post("/retry-queue/999999/retry")
    assert response.status_code == 404


def test_force_retry_on_already_succeeded_item_is_a_noop(client, session):
    item = RetryQueueItem(
        legacy_id=2,
        natural_key="ya-migrado@example.com",
        payload=_PAYLOAD,
        attempt_count=1,
        max_attempts=3,
        status="succeeded",
        next_attempt_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()
    item_id = item.id

    with patch("legacy_sync.api.app.attempt_retry") as mocked:
        response = client.post(f"/retry-queue/{item_id}/retry")

    mocked.assert_not_called()
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
