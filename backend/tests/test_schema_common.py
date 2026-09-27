import pytest
from pydantic import TypeAdapter, ValidationError

from app.schema.common import Id, Model, Note, Tag, YearMonth


class Example(Model):
    start_date: YearMonth
    item_id: Id


@pytest.mark.parametrize("value", ["st_backend", "q_st_scale", "role-1", "a"])
def test_id_accepts_stable_slugs(value: str) -> None:
    assert TypeAdapter(Id).validate_python(value) == value


@pytest.mark.parametrize("value", ["", "Upper", "has space", "_leading", "x" * 65])
def test_id_rejects_other_strings(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Id).validate_python(value)


@pytest.mark.parametrize("value", ["2025-09", "1999-12", "2024-01"])
def test_year_month_accepts_valid_months(value: str) -> None:
    assert TypeAdapter(YearMonth).validate_python(value) == value


@pytest.mark.parametrize("value", ["2025-13", "2025-00", "2025-9", "Sep 2025", "2025-09-01"])
def test_year_month_rejects_other_formats(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(YearMonth).validate_python(value)


def test_tag_rejects_uppercase() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Tag).validate_python("Backend")


def test_json_uses_camel_case_and_python_accepts_both() -> None:
    by_alias = Example.model_validate({"startDate": "2025-09", "itemId": "x"})
    by_name = Example(start_date="2025-09", item_id="x")
    assert by_alias == by_name
    assert by_name.model_dump() == {"startDate": "2025-09", "itemId": "x"}


def test_models_are_frozen_and_reject_unknown_fields() -> None:
    note = Note(text="Keep numbers exact.")
    with pytest.raises(ValidationError):
        note.text = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Note.model_validate({"text": "x", "colour": "red"})


def test_note_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        Note(text="   ")
