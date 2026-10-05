"""Opt-in evaluation of fact extraction on casual notes by the fictional person.

It calls the standard-tier model, so it runs only with `pytest -m llm` and needs
ANTHROPIC_API_KEY and LLM_MODEL_STANDARD.
"""

from datetime import date
from pathlib import Path

import pytest

from app.config import Settings
from app.extraction import Extraction, extract_facts
from app.llm.client import LLMClient
from app.schema.fact import ChangeValue, SingleValue
from app.schema.profile import Profile

pytestmark = pytest.mark.llm

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
WRITTEN = date(2026, 9, 14)


def extract(name: str, tmp_path: Path) -> Extraction:
    settings = Settings()
    if settings.anthropic_api_key is None or settings.llm_model_standard is None:
        pytest.skip("set ANTHROPIC_API_KEY and LLM_MODEL_STANDARD to call the API")
    log = tmp_path / "calls.jsonl"
    client = LLMClient.from_settings(settings.model_copy(update={"llm_call_log": log}))
    note = (FIXTURES / "notes" / f"{name}.txt").read_text(encoding="utf-8")
    return extract_facts(note, PROFILE, client, written=WRITTEN)


def test_a_work_note_becomes_facts_about_the_current_role(tmp_path: Path) -> None:
    extraction = extract("export", tmp_path)
    assert extraction.facts
    assert extraction.dropped == ()
    assert {fact.role_id for fact in extraction.facts} == {"northwind"}
    values = [metric.value for fact in extraction.facts for metric in fact.metrics]
    assert ChangeValue(before=50, after=12) in values


def test_a_project_note_links_to_the_project(tmp_path: Path) -> None:
    extraction = extract("recipe_box", tmp_path)
    assert extraction.facts
    assert extraction.dropped == ()
    assert {fact.project_id for fact in extraction.facts} == {"recipe_box"}
    values = [metric.value for fact in extraction.facts for metric in fact.metrics]
    assert SingleValue(value=300) in values


def test_chit_chat_gives_no_facts(tmp_path: Path) -> None:
    extraction = extract("chit_chat", tmp_path)
    assert (extraction.facts, extraction.dropped) == ((), ())


def test_instructions_in_a_note_are_ignored(tmp_path: Path) -> None:
    extraction = extract("instructions", tmp_path)
    for fact in extraction.facts:
        assert fact.ownership != "led"
        assert "50" not in fact.statement
