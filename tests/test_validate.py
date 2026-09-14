from legacy_sync.schemas import validate_legacy_record


def _base_row(**overrides):
    row = {
        "legacy_id": 1,
        "full_name": "Ana Perez",
        "email": "ana@example.com",
        "birth_date": "1990-04-12",
        "phone": "555-1234",
        "source_created_at": "2024-01-01T10:00:00",
    }
    row.update(overrides)
    return row


def test_valid_record_parses_mixed_date_formats():
    for raw_date in ("1990-04-12", "12/04/1990", "04-12-1990"):
        outcome = validate_legacy_record(_base_row(birth_date=raw_date))
        assert outcome.is_valid, outcome.errors


def test_missing_email_is_isolated_not_raised():
    outcome = validate_legacy_record(_base_row(email=None))
    assert not outcome.is_valid
    assert outcome.legacy_id == 1
    assert any(e.field == "email" for e in outcome.errors)


def test_malformed_email_is_rejected():
    outcome = validate_legacy_record(_base_row(email="not-an-email"))
    assert not outcome.is_valid
    assert any(e.field == "email" for e in outcome.errors)


def test_email_is_normalized_lowercase_and_trimmed():
    outcome = validate_legacy_record(_base_row(email="  ANA@EXAMPLE.COM  "))
    assert outcome.is_valid
    assert outcome.record.email == "ana@example.com"


def test_blank_full_name_is_rejected():
    outcome = validate_legacy_record(_base_row(full_name="   "))
    assert not outcome.is_valid
    assert any(e.field == "full_name" for e in outcome.errors)


def test_unparseable_date_reports_specific_field():
    outcome = validate_legacy_record(_base_row(birth_date="not-a-date"))
    assert not outcome.is_valid
    assert any(e.field == "birth_date" for e in outcome.errors)


def test_one_bad_record_does_not_affect_validation_of_others():
    good = validate_legacy_record(_base_row(legacy_id=1))
    bad = validate_legacy_record(_base_row(legacy_id=2, email=None))
    good_again = validate_legacy_record(_base_row(legacy_id=3))
    assert good.is_valid
    assert not bad.is_valid
    assert good_again.is_valid
