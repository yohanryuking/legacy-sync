from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

from legacy_sync import cli as cli_module
from legacy_sync.cli import app
from legacy_sync.models.target import RetryQueueItem

_PAYLOAD = {
    "legacy_id": 1,
    "full_name": "Laura Fonseca",
    "email": "laura@example.com",
    "birth_date": "1994-06-06",
    "phone": "555-14",
    "source_created_at": "2024-01-01T10:00:00",
}


def test_worker_once_processes_the_retry_queue_a_single_time(session_factory, monkeypatch):
    monkeypatch.setattr(cli_module, "SessionLocal", session_factory)

    with session_factory() as session:
        session.add(
            RetryQueueItem(
                legacy_id=1,
                natural_key="laura@example.com",
                payload=_PAYLOAD,
                status="pending",
                next_attempt_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )
        session.commit()

    runner = CliRunner()
    result = runner.invoke(app, ["worker", "--once"])

    assert result.exit_code == 0
    assert "exitosos=1" in result.stdout

    with session_factory() as session:
        item = session.query(RetryQueueItem).one()
        assert item.status == "succeeded"


def test_runs_command_lists_checkpoints(session_factory, monkeypatch):
    monkeypatch.setattr(cli_module, "SessionLocal", session_factory)

    with session_factory() as session:
        from legacy_sync.models.target import MigrationCheckpoint

        session.add(MigrationCheckpoint(run_id="abc123", last_legacy_id_processed=5, status="completed"))
        session.commit()

    runner = CliRunner()
    result = runner.invoke(app, ["runs"])

    assert result.exit_code == 0
    assert "abc123" in result.stdout
    assert "completed" in result.stdout
