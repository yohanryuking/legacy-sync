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
from legacy_sync.etl.checkpoint import load_run_for_resume, mark_completed, save_progress, start_new_run
from legacy_sync.etl.extract import iter_legacy_records
from legacy_sync.etl.load import upsert_customer
from legacy_sync.etl.validate import validate_and_build_log
from legacy_sync.models.target import MigrationLog, RetryQueueItem
from legacy_sync.schemas import CustomerRecord


class SimulatedTransientError(RuntimeError):
    """Simula un fallo de carga transitorio (timeout de red, DB ocupada, etc.)."""


@dataclass
class PipelineResult:
    run_id: str = ""
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


def run_pipeline(
    session: Session,
    *,
    simulate_load_failures: bool = False,
    resume_run_id: str | None = None,
    resume_last: bool = False,
    checkpoint_every: int = 200,
) -> PipelineResult:
    """Corre la migracion completa. Idempotente: se puede llamar N veces seguidas.

    Sin `resume_run_id`/`resume_last`, cada llamada arranca una corrida
    nueva desde `legacy_id > 0` -- el comportamiento de siempre, el que
    hace que "correr la migracion dos veces no duplica nada" siga
    cumpliendose sin sorpresas.

    Con `resume_run_id=<uuid>` o `resume_last=True` (Sprint 5), retoma una
    corrida que quedo con `status='running'` -- tipicamente porque el
    proceso se interrumpio -- arrancando en `legacy_id >
    last_legacy_id_processed` en vez de releer y revalidar todo desde el
    principio. El punto de avance se confirma (`session.commit()`) cada
    `checkpoint_every` registros, para que un crash pierda como mucho ese
    lote parcial, nunca toda la corrida.

    Con `simulate_load_failures=True` se usa la tasa configurada en Settings
    para forzar fallos aleatorios de carga y ejercitar la cola de reintentos
    (util para demos/tests de Sprint 3; en produccion los fallos son reales,
    no simulados).
    """
    settings = get_settings()
    failure_rate = settings.simulated_load_failure_rate if simulate_load_failures else 0.0

    if resume_run_id is not None or resume_last:
        checkpoint = load_run_for_resume(session, resume_run_id)
    else:
        checkpoint = start_new_run(session)

    result = PipelineResult(run_id=checkpoint.run_id)
    processed_since_checkpoint = 0
    last_seen_legacy_id: int | None = None

    for raw in iter_legacy_records(session, after_legacy_id=checkpoint.last_legacy_id_processed):
        result.total += 1
        outcome, invalid_log = validate_and_build_log(raw)

        if not outcome.is_valid:
            result.invalid += 1
            if invalid_log is not None:
                session.add(invalid_log)
        else:
            result.valid += 1
            assert outcome.record is not None
            try:
                load_outcome = _load_with_retry_queue(session, outcome.record, failure_rate)
            except Exception:  # noqa: BLE001 - ya quedo registrado en la cola/log
                result.load_failed += 1
                result.failed_legacy_ids.append(outcome.record.legacy_id)
            else:
                if load_outcome == "inserted":
                    result.inserted += 1
                elif load_outcome == "updated":
                    result.updated += 1
                else:
                    result.unchanged += 1

        last_seen_legacy_id = raw["legacy_id"]
        processed_since_checkpoint += 1
        if processed_since_checkpoint >= checkpoint_every:
            save_progress(session, checkpoint, last_seen_legacy_id)
            processed_since_checkpoint = 0

    if last_seen_legacy_id is not None:
        save_progress(session, checkpoint, last_seen_legacy_id)
    mark_completed(session, checkpoint)

    return result
