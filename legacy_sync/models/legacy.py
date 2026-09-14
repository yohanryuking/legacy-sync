"""Modelo del sistema "legado": deliberadamente laxo en tipos.

En un sistema legado real casi todo termina siendo texto libre (exports de
COBOL, planillas Excel, dumps de un CRM viejo). Por eso aqui casi todas las
columnas son String/nullable, incluso cuando "deberian" ser fecha o numero:
la validacion estricta ocurre en la capa de Pydantic (legacy_sync/schemas.py),
no en el esquema de origen.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from legacy_sync.models.base import Base


class LegacyCustomer(Base):
    """Tabla de origen con datos "sucios" (generada por seed.generate)."""

    __tablename__ = "legacy_customers"

    legacy_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Fechas en formatos mixtos a proposito: "1990-04-12", "12/04/1990", "04-12-1990", vacio, etc.
    birth_date_raw: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Igual que birth_date_raw: string libre, no timestamp real.
    created_at_raw: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover - solo debug
        return f"LegacyCustomer(legacy_id={self.legacy_id!r}, email={self.email!r})"
