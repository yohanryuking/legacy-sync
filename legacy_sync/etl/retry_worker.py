"""Procesa la cola de reintentos (retry_queue) con backoff exponencial.

Solo los fallos de la etapa de CARGA llegan aca (ver ADR-005 en
docs/DECISIONS.md) -- un registro invalido no se reintenta nunca, porque
reintentar no arregla un dato mal formado.

Cada intento fallido empuja `next_attempt_at` mas lejos en el tiempo
(`base_seconds * 2**attempt_count`, con jitter para evitar que todos los
items reintenten en el mismo instante). Al llegar a `max_attempts`, el item
se marca `status='failed'` y deja de tocarse solo -- a partir de ahi
requiere intervencion manual (o el endpoint de reintento forzado).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from legacy_sync.config import get_settings
from legacy_sync.etl.load import upsert_customer
from legacy_sync.models.target import MigrationLog, RetryQueueItem
from legacy_sync.schemas import CustomerRecord


@dataclass
class RetryRunResult:
    attempted: int = 0
    succeeded: int = 0
    failed_permanently: int = 0
    still_pending: int = 0
    succeeded_ids: list[int] = field(default_factory=list)
    failed_permanently_ids: list[int] = field(default_factory=list)


def _next_attempt_delay(attempt_count: int) -> timedelta:
    settings = get_settings()
    backoff = settings.retry_backoff_base_seconds * (2**attempt_count)
    jitter = random.uniform(0, settings.retry_backoff_jitter_seconds)
    return timedelta(seconds=backoff + jitter)


def attempt_retry(session: Session, item: RetryQueueItem) -> bool:
    """Reintenta cargar un unico item, sin mirar `next_attempt_at` ni `status`.

    Retorna True si la carga tuvo exito (el item se marca 'succeeded'),
    False si fallo de nuevo (el item se reprograma o se marca 'failed' si
    agoto `max_attempts`). Usado tanto por `process_retry_queue` como por el
    endpoint de reintento forzado (`legacy_sync/api/app.py`).
    """
    record = CustomerRecord.model_validate(item.payload)
    savepoint = session.begin_nested()
    try:
        upsert_customer(session, record)
        savepoint.commit()
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de carga cuenta como intento fallido
        savepoint.rollback()
        item.attempt_count += 1
        item.last_error = str(exc)
        if item.attempt_count >= item.max_attempts:
            item.status = "failed"
        else:
            item.next_attempt_at = datetime.now(timezone.utc) + _next_attempt_delay(item.attempt_count)
        session.add(
            MigrationLog(
                legacy_id=item.legacy_id,
                natural_key=item.natural_key,
                stage="load",
                success=False,
                error_field=None,
                error_message=str(exc),
                raw_payload=item.payload,
            )
        )
        return False

    item.status = "succeeded"
    session.add(
        MigrationLog(
            legacy_id=item.legacy_id,
            natural_key=item.natural_key,
            stage="load",
            success=True,
            error_field=None,
            error_message=None,
            raw_payload=item.payload,
        )
    )
    return True


def process_retry_queue(session: Session, *, now: datetime | None = None) -> RetryRunResult:
    """Reintenta todos los items de `retry_queue` cuyo `next_attempt_at` ya paso.

    Idempotente y seguro de correr en un cron/loop: cada item exitoso o
    definitivamente fallido cambia de estado y no vuelve a ser seleccionado
    por `status='pending'`.
    """
    now = now or datetime.now(timezone.utc)
    stmt = select(RetryQueueItem).where(
        RetryQueueItem.status == "pending",
        RetryQueueItem.next_attempt_at <= now,
    )
    items = list(session.execute(stmt).scalars())

    result = RetryRunResult()
    for item in items:
        result.attempted += 1
        if attempt_retry(session, item):
            result.succeeded += 1
            result.succeeded_ids.append(item.id)
        elif item.status == "failed":
            result.failed_permanently += 1
            result.failed_permanently_ids.append(item.id)
        else:
            result.still_pending += 1

    return result
