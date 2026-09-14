"""Modelos de destino: datos limpios, logs de proceso y cola de reintentos.

Se mantienen en tres tablas separadas a proposito (ver docs/DECISIONS.md):
- migrated_customers: unicamente datos que ya pasaron validacion y carga OK.
- migration_logs: rastro de auditoria de *todo* lo que el pipeline hizo,
  exitoso o no (por que fallo, en que etapa, con que payload original).
- retry_queue: registros que fallaron en la etapa de CARGA (no de validacion)
  y son candidatos a reintento con backoff exponencial (logica en Sprint 3).
"""

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import JSON, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from legacy_sync.models.base import Base


class MigratedCustomer(Base):
    """Tabla final. Una fila por cliente real, deduplicada por `natural_key`."""

    __tablename__ = "migrated_customers"
    __table_args__ = (UniqueConstraint("natural_key", name="uq_migrated_customers_natural_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Clave natural de negocio (email normalizado). Es la clave de deduplicacion:
    # sin importar cuantas veces corra la migracion, o cuantos duplicados haya
    # en el legado, solo existe una fila por natural_key.
    natural_key: Mapped[str] = mapped_column(String(255), index=True)

    # Checksum del contenido relevante del registro. Permite decidir en el
    # upsert si realmente hay algo nuevo que escribir (evita UPDATEs sin
    # cambios y deja rastro de cuando cambio un dato).
    checksum: Mapped[str] = mapped_column(String(64))

    legacy_id: Mapped[int] = mapped_column(Integer, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    birth_date: Mapped[date] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    migrated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"MigratedCustomer(natural_key={self.natural_key!r})"


class MigrationStage(StrEnum):
    VALIDATION = "validation"
    LOAD = "load"


class MigrationLog(Base):
    """Auditoria de cada registro procesado (valido, invalido, o error de carga)."""

    __tablename__ = "migration_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    natural_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(20))
    success: Mapped[bool] = mapped_column(default=False)
    error_field: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"MigrationLog(legacy_id={self.legacy_id!r}, stage={self.stage!r}, success={self.success!r})"


class RetryQueueItem(Base):
    """Cola de registros validos que fallaron al cargarse (Sprint 3 procesa esto).

    Solo se define el esquema en Fase 1; el worker de backoff exponencial y
    el endpoint de "forzar reintento" se implementan en Sprint 3 (ver
    docs/ROADMAP.md).
    """

    __tablename__ = "retry_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    legacy_id: Mapped[int] = mapped_column(Integer, index=True)
    natural_key: Mapped[str] = mapped_column(String(255), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|succeeded|failed
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"RetryQueueItem(legacy_id={self.legacy_id!r}, status={self.status!r})"
