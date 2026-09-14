from sqlalchemy import func, select

from legacy_sync.etl.pipeline import run_pipeline
from legacy_sync.models.legacy import LegacyCustomer
from legacy_sync.models.target import MigratedCustomer, MigrationLog


def _seed_legacy(session, rows):
    session.add_all(LegacyCustomer(**row) for row in rows)
    session.commit()


def test_running_migration_twice_does_not_duplicate(session):
    """El escenario central del proyecto: correr la migracion dos veces no
    debe duplicar ni un solo registro en migrated_customers."""
    _seed_legacy(
        session,
        [
            dict(
                legacy_id=1,
                full_name="Ana Perez",
                email="ana@example.com",
                birth_date_raw="1990-04-12",
                phone="555-1",
                created_at_raw="2024-01-01T10:00:00",
            ),
            dict(
                legacy_id=2,
                full_name="Bruno Diaz",
                email="bruno@example.com",
                birth_date_raw="15/06/1985",
                phone="555-2",
                created_at_raw="2024-02-01T10:00:00",
            ),
        ],
    )

    first = run_pipeline(session)
    session.commit()
    second = run_pipeline(session)
    session.commit()

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.unchanged == 2

    total_rows = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert total_rows == 2


def test_duplicate_emails_in_legacy_collapse_to_one_migrated_row(session):
    """Dos filas del legado con el mismo email (duplicado tipico de un
    sistema viejo) deben resultar en una unica fila en migrated_customers."""
    _seed_legacy(
        session,
        [
            dict(
                legacy_id=1,
                full_name="Carla Ruiz",
                email="carla@example.com",
                birth_date_raw="1992-03-03",
                phone="555-3",
                created_at_raw="2024-01-01T10:00:00",
            ),
            dict(
                legacy_id=2,
                full_name="CARLA RUIZ",
                email="carla@example.com",
                birth_date_raw="1992-03-03",
                phone="555-3",
                created_at_raw="2024-01-01T10:00:00",
            ),
        ],
    )

    result = run_pipeline(session)
    session.commit()

    assert result.valid == 2
    total_rows = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert total_rows == 1


def test_invalid_record_is_logged_without_blocking_the_batch(session):
    _seed_legacy(
        session,
        [
            dict(
                legacy_id=1,
                full_name="Diego Sosa",
                email=None,  # invalido: email obligatorio
                birth_date_raw="1988-08-08",
                phone="555-4",
                created_at_raw="2024-01-01T10:00:00",
            ),
            dict(
                legacy_id=2,
                full_name="Elena Vidal",
                email="elena@example.com",
                birth_date_raw="1991-05-20",
                phone="555-5",
                created_at_raw="2024-01-01T10:00:00",
            ),
        ],
    )

    result = run_pipeline(session)
    session.commit()

    assert result.invalid == 1
    assert result.valid == 1
    assert result.inserted == 1

    migrated_count = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert migrated_count == 1

    log = session.scalar(select(MigrationLog).where(MigrationLog.legacy_id == 1))
    assert log is not None
    assert log.stage == "validation"
    assert "email" in (log.error_message or "")


def test_changed_record_updates_existing_row_instead_of_duplicating(session):
    _seed_legacy(
        session,
        [
            dict(
                legacy_id=1,
                full_name="Fabian Torres",
                email="fabian@example.com",
                birth_date_raw="1980-01-01",
                phone="555-6",
                created_at_raw="2024-01-01T10:00:00",
            )
        ],
    )
    run_pipeline(session)
    session.commit()

    # Simula una correccion en el legado: mismo cliente, telefono actualizado.
    row = session.query(LegacyCustomer).filter_by(legacy_id=1).one()
    row.phone = "555-9999"
    session.commit()

    second = run_pipeline(session)
    session.commit()

    assert second.updated == 1
    migrated = session.query(MigratedCustomer).filter_by(natural_key="fabian@example.com").one()
    assert migrated.phone == "555-9999"

    total_rows = session.scalar(select(func.count()).select_from(MigratedCustomer))
    assert total_rows == 1
