"""Carga idempotente: upsert por clave natural (email normalizado).

Correr la migracion N veces sobre los mismos datos de origen debe dejar
exactamente las mismas filas en `migrated_customers` (ver
tests/test_load_idempotent.py). Se logra con INSERT ... ON CONFLICT DO
UPDATE sobre la unique constraint de `natural_key`, condicionado a que el
checksum haya cambiado.
"""

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from legacy_sync.etl.checksum import compute_checksum
from legacy_sync.models.target import MigratedCustomer
from legacy_sync.schemas import CustomerRecord


def _insert_builder(session: Session):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return postgresql.insert
    if dialect == "sqlite":
        return sqlite.insert
    raise NotImplementedError(
        f"Dialecto '{dialect}' no soportado por el upsert idempotente. "
        "Agregar rama dialect-specific en legacy_sync/etl/load.py."
    )


def upsert_customer(session: Session, record: CustomerRecord) -> str:
    """Inserta o actualiza un MigratedCustomer. Retorna 'inserted', 'updated' o 'unchanged'.

    Idempotente: correr esto dos veces con el mismo `record` nunca crea una
    segunda fila -- la unique constraint sobre `natural_key` fuerza la rama
    ON CONFLICT, y la clausula WHERE evita escribir si el checksum no cambio.
    """
    checksum = compute_checksum(record)
    values = {
        "natural_key": record.natural_key,
        "checksum": checksum,
        "legacy_id": record.legacy_id,
        "full_name": record.full_name,
        "email": record.email,
        "birth_date": record.birth_date,
        "phone": record.phone,
        "source_created_at": record.source_created_at,
    }

    insert = _insert_builder(session)
    stmt = insert(MigratedCustomer).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MigratedCustomer.natural_key],
        set_={
            "checksum": stmt.excluded.checksum,
            "legacy_id": stmt.excluded.legacy_id,
            "full_name": stmt.excluded.full_name,
            "email": stmt.excluded.email,
            "birth_date": stmt.excluded.birth_date,
            "phone": stmt.excluded.phone,
            "source_created_at": stmt.excluded.source_created_at,
        },
        where=(MigratedCustomer.checksum != stmt.excluded.checksum),
    )

    existing = session.query(MigratedCustomer).filter_by(natural_key=record.natural_key).one_or_none()
    session.execute(stmt)

    if existing is None:
        return "inserted"
    if existing.checksum != checksum:
        return "updated"
    return "unchanged"
