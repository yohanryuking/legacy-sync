"""Extraccion: lee la tabla legado tal cual, sin transformar nada.

La transformacion/validacion es responsabilidad de legacy_sync.schemas -
extract.py solo se encarga de traer filas y convertirlas a dict "crudo"
con los nombres de campo que CustomerRecord espera.
"""

from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from legacy_sync.models.legacy import LegacyCustomer


def iter_legacy_records(
    session: Session, batch_size: int = 500, *, after_legacy_id: int | None = None
) -> Iterator[dict]:
    """Itera los registros del legado como dicts, en orden de legacy_id.

    Ordenar por legacy_id (y no traer todo en memoria) es lo que hace posible
    el checkpointing de Sprint 5: `after_legacy_id` retoma una corrida
    interrumpida pidiendo solo `legacy_id > after_legacy_id`, en vez de
    releer y revalidar los registros ya confirmados.
    """
    stmt = select(LegacyCustomer).order_by(LegacyCustomer.legacy_id)
    if after_legacy_id is not None:
        stmt = stmt.where(LegacyCustomer.legacy_id > after_legacy_id)
    for row in session.execute(stmt).scalars().yield_per(batch_size):
        yield {
            "legacy_id": row.legacy_id,
            "full_name": row.full_name,
            "email": row.email,
            "birth_date": row.birth_date_raw,
            "phone": row.phone,
            "source_created_at": row.created_at_raw,
        }
