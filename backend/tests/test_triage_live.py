"""Opt-in check of triage against the real light-tier model, on notes by the fictional person.

It runs only with `pytest -m llm` and needs ANTHROPIC_API_KEY and LLM_MODEL_LIGHT. It's kept apart
from the unit tests, whose isolated settings would always skip it.
"""

from pathlib import Path

import pytest

from app.config import Settings
from app.llm.client import LLMClient
from app.triage import Label, triage

pytestmark = pytest.mark.llm

NOTES = Path(__file__).parent / "fixtures" / "notes"


def note(name: str) -> str:
    return (NOTES / f"{name}.txt").read_text(encoding="utf-8")


CASES: dict[str, tuple[str, Label]] = {
    "work win": (note("export"), "work"),
    "side project": (note("recipe_box"), "work"),
    "instructions in the note": (note("instructions"), "work"),
    "work and a hobby": ("climbing gym after work. also merged the login fix, finally", "work"),
    "chit-chat": (note("chit_chat"), "other"),
    "testing the bot": ("hello? just testing if this bot works", "other"),
}


@pytest.fixture(scope="module")
def llm(tmp_path_factory: pytest.TempPathFactory) -> LLMClient:
    settings = Settings()
    if settings.anthropic_api_key is None or settings.llm_model_light is None:
        pytest.skip("set ANTHROPIC_API_KEY and LLM_MODEL_LIGHT to call the API")
    log = tmp_path_factory.mktemp("triage") / "calls.jsonl"
    return LLMClient.from_settings(settings.model_copy(update={"llm_call_log": log}))


@pytest.mark.parametrize("case", CASES)
def test_the_live_light_model_labels_the_note(llm: LLMClient, case: str) -> None:
    text, label = CASES[case]
    assert triage(text, llm) == label
