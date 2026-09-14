"""Schema estricto contra el que se valida cada registro del legado.

Filosofia (ver docs/DECISIONS.md): un registro invalido nunca debe tumbar el
batch completo. `validate_legacy_record` siempre retorna un resultado -
CustomerRecord si paso, o una lista de errores especificos (campo + motivo)
si no - y quien la llama decide que hacer con eso.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator

# Formatos de fecha que el legado usa "segun el humor del dia".
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y", "%Y/%m/%d")


def _parse_loose_date(value: str) -> date:
    cleaned = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"formato de fecha no reconocido: {value!r}")


class CustomerRecord(BaseModel):
    """Representacion limpia y valida de un cliente, lista para cargar."""

    legacy_id: int
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    birth_date: date
    phone: str | None = None
    source_created_at: datetime

    @field_validator("full_name", mode="before")
    @classmethod
    def _strip_and_require_name(cls, value: object) -> object:
        if value is None:
            raise ValueError("full_name es obligatorio")
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("full_name no puede estar vacio")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: object) -> object:
        if value is None:
            raise ValueError("email es obligatorio")
        if isinstance(value, str):
            value = value.strip().lower()
        return value

    @field_validator("birth_date", mode="before")
    @classmethod
    def _parse_birth_date(cls, value: object) -> object:
        if value is None or value == "":
            raise ValueError("birth_date es obligatorio")
        if isinstance(value, str):
            return _parse_loose_date(value)
        return value

    @field_validator("source_created_at", mode="before")
    @classmethod
    def _parse_created_at(cls, value: object) -> object:
        if value is None or value == "":
            raise ValueError("created_at es obligatorio")
        if isinstance(value, str):
            cleaned = value.strip()
            try:
                return datetime.fromisoformat(cleaned)
            except ValueError:
                return datetime.combine(_parse_loose_date(cleaned), datetime.min.time())
        return value

    @field_validator("phone", mode="before")
    @classmethod
    def _blank_phone_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def natural_key(self) -> str:
        """Clave de deduplicacion de negocio: email normalizado."""
        return self.email


class FieldError(BaseModel):
    field: str
    message: str


class ValidationOutcome(BaseModel):
    """Resultado de validar un registro: exito XOR lista de errores."""

    legacy_id: int | None
    record: CustomerRecord | None = None
    errors: list[FieldError] = Field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.record is not None and not self.errors


def validate_legacy_record(raw: dict) -> ValidationOutcome:
    """Valida un dict crudo (tal como sale de la tabla legado) contra CustomerRecord.

    Nunca lanza: cualquier fallo de Pydantic se traduce a una lista de
    FieldError para que el llamador pueda registrar el motivo exacto sin
    detener el resto del batch.
    """
    legacy_id = raw.get("legacy_id")
    try:
        record = CustomerRecord.model_validate(raw)
    except ValidationError as exc:
        errors = [
            FieldError(field=".".join(str(p) for p in err["loc"]) or "__root__", message=err["msg"])
            for err in exc.errors()
        ]
        return ValidationOutcome(legacy_id=legacy_id, record=None, errors=errors)
    return ValidationOutcome(legacy_id=legacy_id, record=record, errors=[])
