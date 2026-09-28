"""Real Claude API calls: opt in with `pytest -m llm`. They need a key and a light-tier model."""

from pathlib import Path

import pytest

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.prompts import load_prompt
from app.schema.common import Model

pytestmark = pytest.mark.llm

PROMPT = load_prompt("triage", 1, root=Path(__file__).parent / "fixtures" / "prompts")


class Triage(Model):
    work_related: bool
    topics: tuple[str, ...] = ()


def test_the_light_tier_answers_in_the_schema(tmp_path: Path) -> None:
    settings = Settings()
    if settings.anthropic_api_key is None or settings.llm_model_light is None:
        pytest.skip("set ANTHROPIC_API_KEY and LLM_MODEL_LIGHT to call the API")
    log_path = tmp_path / "calls.jsonl"
    llm = LLMClient.from_settings(settings.model_copy(update={"llm_call_log": log_path}))
    result = llm.call(
        "message_triage",
        PROMPT,
        Triage,
        variables={
            "sender": "Casey",
            "message": "Moved our nightly import onto a queue, so it no longer times out.",
        },
    )
    assert result.output.work_related
    assert result.call.outcome == "ok"
    assert result.call.input_tokens > 0
    assert log_path.read_text(encoding="utf-8").count("\n") == 1
