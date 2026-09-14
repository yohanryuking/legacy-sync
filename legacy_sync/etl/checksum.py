import hashlib

from legacy_sync.schemas import CustomerRecord


def compute_checksum(record: CustomerRecord) -> str:
    """Checksum estable del contenido "de negocio" del registro.

    Se usa para decidir, en el upsert, si un registro ya existente realmente
    cambio (evita UPDATEs innecesarios cuando se re-corre la migracion sobre
    datos identicos).
    """
    payload = "|".join(
        [
            record.full_name,
            record.email,
            record.birth_date.isoformat(),
            record.phone or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
