"""Opt-in check of the catch-up interview against the real standard-tier model, with the
fictional person's profile.

It runs only with `pytest -m llm` and needs ANTHROPIC_API_KEY and LLM_MODEL_STANDARD. It's kept
apart from the unit tests, whose isolated settings would always skip it.
"""

from pathlib import Path

import pytest

from app.catch_up import FIXED_QUESTIONS, Turn, next_question
from app.config import Settings
from app.llm.client import LLMClient
from app.schema.profile import Profile

pytestmark = pytest.mark.llm

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def llm(tmp_path_factory: pytest.TempPathFactory) -> LLMClient:
    settings = Settings()
    if settings.anthropic_api_key is None or settings.llm_model_standard is None:
        pytest.skip("set ANTHROPIC_API_KEY and LLM_MODEL_STANDARD to call the API")
    log = tmp_path_factory.mktemp("catch_up") / "calls.jsonl"
    return LLMClient.from_settings(settings.model_copy(update={"llm_call_log": log}))


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))


def test_the_live_model_opens_with_its_own_question(llm: LLMClient, profile: Profile) -> None:
    question = next_question(profile, [], llm)
    assert question is not None
    assert question not in FIXED_QUESTIONS, "the model's question passes the check"


def test_the_live_model_follows_up_on_an_answer(llm: LLMClient, profile: Profile) -> None:
    turns = [
        Turn(
            question=FIXED_QUESTIONS[0],
            answer="I moved to the payments team in the spring and took over the refunds service",
        )
    ]
    question = next_question(profile, turns, llm)
    assert question is not None
    assert question not in FIXED_QUESTIONS


def test_the_live_model_ends_when_nothing_is_new(llm: LLMClient, profile: Profile) -> None:
    turns = [
        Turn(question=FIXED_QUESTIONS[0], answer="Nothing really, same job and team."),
        Turn(question=FIXED_QUESTIONS[1], answer="Same projects as before, nothing new."),
        Turn(question=FIXED_QUESTIONS[2]),
        Turn(question=FIXED_QUESTIONS[3], answer="None."),
    ]
    assert next_question(profile, turns, llm) is None
