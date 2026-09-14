"""Orquesta extract -> validate -> load para toda la tabla legado.

Cada registro se procesa en su propio SAVEPOINT (session.begin_nested):
un fallo de carga en un registro (constraint violation, error simulado de
red) no debe abortar la transaccion completa ni impedir que seguridad los
demas registros se procesen.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from legacy_sync.config import get_settings
from legacy_sync.etl.extract import iter_legacy_records
from legacy_sync.etl.load import upsert_customer
from legacy_sync.etl.validate import validate_and_build_log
from legacy_sync.models.target import MigrationLog, RetryQueueItem
from legacy_sync.schemas import CustomerRecord


class SimulatedTransientError(RuntimeError):
    """Simula un fallo de carga transitorio (timeout de red, DB ocupada, etc.)."""


@dataclass
class PipelineResult:
    total: int = 0
    valid: int = 0
    invalid: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    load_failed: int = 0
    failed_legacy_ids: list[int] = field(default_factory=list)

    @property
    def loaded(self) -> int:
        return self.inserted + self.updated + self.unchanged


def _maybe_simulate_failure(rate: float) -> None:
    if rate > 0 and random.random() < rate:
        raise SimulatedTransientError("fallo transitorio simulado durante la carga")


def _load_with_retry_queue(session: Session, record: CustomerRecord, failure_rate: float) -> str:
    """Intenta cargar `record`. Si falla, encola en retry_queue y re-lanza."""
    savepoint = session.begin_nested()
    try:
        _maybe_simulate_failure(failure_rate)
        outcome = upsert_customer(session, record)
        savepoint.commit()
        return outcome
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de carga va a la cola
        savepoint.rollback()
        session.add(
            RetryQueueItem(
                legacy_id=record.legacy_id,
                natural_key=record.natural_key,
                payload=record.model_dump(mode="json"),
                last_error=str(exc),
                status="pending",
            )
        )
        session.add(
            MigrationLog(
                legacy_id=record.legacy_id,
                natural_key=record.natural_key,
                stage="load",
                success=False,
                error_field=None,
                error_message=str(exc),
                raw_payload=record.model_dump(mode="json"),
            )
        )
        raise


def run_pipeline(session: Session, *, simulate_load_failures: bool = False) -> PipelineResult:
    """Corre la migracion completa. Idempotente: se puede llamar N veces seguidas.

    Con `simulate_load_failures=True` se usa la tasa configurada en Settings
    para forzar fallos aleatorios de carga y ejercitar la cola de reintentos
    (util para demos/tests de Sprint 3; en produccion los fallos son reales,
    no simulados).
    """
    settings = get_settings()
    failure_rate = settings.simulated_load_failure_rate if simulate_load_failures else 0.0

    result = PipelineResult()

    for raw in iter_legacy_records(session):
        result.total += 1
        outcome, invalid_log = validate_and_build_log(raw)

        if not outcome.is_valid:
            result.invalid += 1
            if invalid_log is not None:
                session.add(invalid_log)
            continue

        result.valid += 1
        assert outcome.record is not None
        try:
            load_outcome = _load_with_retry_queue(session, outcome.record, failure_rate)
        except Exception:  # noqa: BLE001 - ya quedo registrado en la cola/log
            result.load_failed += 1
            result.failed_legacy_ids.append(outcome.record.legacy_id)
            continue

        if load_outcome == "inserted":
            result.inserted += 1
        elif load_outcome == "updated":
            result.updated += 1
        else:
            result.unchanged += 1

    return result
