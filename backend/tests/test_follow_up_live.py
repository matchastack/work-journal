"""Opt-in check of the follow-up question against the real light-tier model, on notes by the
fictional person.

It runs only with `pytest -m llm` and needs ANTHROPIC_API_KEY and LLM_MODEL_LIGHT. It's kept apart
from the unit tests, whose isolated settings would always skip it.
"""

from pathlib import Path

import pytest

from app.config import Settings
from app.follow_up import FIXED_QUESTIONS, follow_up_question
from app.llm.client import LLMClient

pytestmark = pytest.mark.llm

NOTES = Path(__file__).parent / "fixtures" / "notes"


def note(name: str) -> str:
    return (NOTES / f"{name}.txt").read_text(encoding="utf-8")


ASKS = {
    "a result without a number": "sped up the search page a lot this week by caching the filters",
    "work without its effect": "rewrote the retry logic in the payment worker",
}
NO_QUESTION = {
    "numbers, impact and ownership": (
        "I rebuilt the nightly export on my own and got it from about 50 minutes down to 12, "
        "so the morning reports are ready before the team starts now"
    ),
    "chit-chat": note("chit_chat"),
    "testing the bot": "hello? just testing if this bot works",
}


@pytest.fixture(scope="module")
def llm(tmp_path_factory: pytest.TempPathFactory) -> LLMClient:
    settings = Settings()
    if settings.anthropic_api_key is None or settings.llm_model_light is None:
        pytest.skip("set ANTHROPIC_API_KEY and LLM_MODEL_LIGHT to call the API")
    log = tmp_path_factory.mktemp("follow_up") / "calls.jsonl"
    return LLMClient.from_settings(settings.model_copy(update={"llm_call_log": log}))


@pytest.mark.parametrize("case", ASKS)
def test_the_live_model_asks_its_own_question(llm: LLMClient, case: str) -> None:
    question = follow_up_question(ASKS[case], llm)
    assert question is not None
    assert question not in FIXED_QUESTIONS.values(), "the model's question passes the check"


@pytest.mark.parametrize("case", NO_QUESTION)
def test_the_live_model_asks_nothing_needless(llm: LLMClient, case: str) -> None:
    assert follow_up_question(NO_QUESTION[case], llm) is None
