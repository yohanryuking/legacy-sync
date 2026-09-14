"""Genera datos "sucios" en legacy_customers, a proposito, para poder demostrar
que el pipeline los maneja correctamente: fechas en formatos mixtos, emails
mal formados, campos faltantes y duplicados del mismo cliente.
"""

from __future__ import annotations

import random
from datetime import date

from faker import Faker
from sqlalchemy.orm import Session

from legacy_sync.models.legacy import LegacyCustomer

_fake = Faker("es_ES")

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y")


def _random_date_str(d: date) -> str:
    fmt = random.choice(_DATE_FORMATS)
    return d.strftime(fmt)


def _dirty_email(email: str) -> str:
    """Rompe un email valido de alguna forma tipica de un legado real."""
    choice = random.choice(["no_at", "no_domain", "spaces", "uppercase_ok"])
    if choice == "no_at":
        return email.replace("@", "_at_")
    if choice == "no_domain":
        return email.split("@")[0] + "@"
    if choice == "spaces":
        return f"  {email}  "
    return email.upper()  # valido, solo prueba normalizacion a lowercase


def generate_legacy_data(session: Session, count: int, *, dirty_ratio: float = 0.25, duplicate_ratio: float = 0.08) -> int:
    """Inserta `count` filas base en legacy_customers, con una fraccion sucia/duplicada.

    Retorna la cantidad total de filas insertadas (incluye duplicados, que
    agregan filas extra por encima de `count`).
    """
    rows: list[LegacyCustomer] = []
    legacy_id = 1

    for _ in range(count):
        full_name = _fake.name()
        email = _fake.unique.email()
        birth_date = _fake.date_of_birth(minimum_age=18, maximum_age=90)
        phone = _fake.phone_number()
        created_at = _fake.date_time_between(start_date="-3y", end_date="now")

        birth_date_raw = _random_date_str(birth_date)

        roll = random.random()
        if roll < dirty_ratio * 0.25:
            full_name = None
        elif roll < dirty_ratio * 0.5:
            email = None
        elif roll < dirty_ratio * 0.75:
            email = _dirty_email(email)
        elif roll < dirty_ratio:
            birth_date_raw = ""

        row = LegacyCustomer(
            legacy_id=legacy_id,
            full_name=full_name,
            email=email,
            birth_date_raw=birth_date_raw,
            phone=phone if random.random() > 0.1 else None,
            created_at_raw=created_at.isoformat(),
        )
        rows.append(row)
        legacy_id += 1

        # Duplicado deliberado: mismo email, formato de nombre levemente distinto,
        # legacy_id distinto -- simula el mismo cliente cargado dos veces al legado.
        if email and random.random() < duplicate_ratio:
            dup = LegacyCustomer(
                legacy_id=legacy_id,
                full_name=full_name.upper() if full_name else full_name,
                email=email,
                birth_date_raw=row.birth_date_raw,
                phone=phone,
                created_at_raw=created_at.isoformat(),
            )
            rows.append(dup)
            legacy_id += 1

    session.add_all(rows)
    session.commit()
    return len(rows)
