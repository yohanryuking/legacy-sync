"""Capa fina sobre legacy_sync.schemas que traduce el resultado a un MigrationLog.

Mantiene la validacion pura (schemas.py) separada de la persistencia del log,
para poder testear cada una por separado.
"""

from legacy_sync.models.target import MigrationLog
from legacy_sync.schemas import ValidationOutcome, validate_legacy_record


def validate_and_build_log(raw: dict) -> tuple[ValidationOutcome, MigrationLog | None]:
    """Valida `raw`. Si es invalido, retorna tambien el MigrationLog a persistir.

    Si es valido, el segundo elemento es None: el log de carga (exitosa o no)
    se genera despues, en la etapa de load, donde se conoce el resultado real.
    """
    outcome = validate_legacy_record(raw)
    if outcome.is_valid:
        return outcome, None

    first_error = outcome.errors[0] if outcome.errors else None
    log = MigrationLog(
        legacy_id=outcome.legacy_id,
        natural_key=None,
        stage="validation",
        success=False,
        error_field=first_error.field if first_error else None,
        error_message="; ".join(f"{e.field}: {e.message}" for e in outcome.errors) or None,
        raw_payload=raw,
    )
    return outcome, log
