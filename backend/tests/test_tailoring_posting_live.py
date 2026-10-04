"""Opt-in check of job-posting parsing against the real light-tier model.

It runs only with `pytest -m llm` and needs ANTHROPIC_API_KEY and LLM_MODEL_LIGHT. It's kept apart
from the unit tests, whose isolated settings would always skip it.
"""

from pathlib import Path

import pytest

from app.config import Settings
from app.llm.client import LLMClient
from app.tailoring.posting import parse_posting

pytestmark = pytest.mark.llm

POSTINGS = Path(__file__).parent / "fixtures" / "postings"


def test_the_live_light_model_parses_a_posting(tmp_path: Path) -> None:
    settings = Settings()
    if settings.anthropic_api_key is None or settings.llm_model_light is None:
        pytest.skip("set ANTHROPIC_API_KEY and LLM_MODEL_LIGHT to call the API")
    llm = LLMClient.from_settings(
        settings.model_copy(update={"llm_call_log": tmp_path / "calls.jsonl"})
    )
    parsed = parse_posting((POSTINGS / "backend.txt").read_text(encoding="utf-8"), llm)
    assert parsed.posting.company == "Fabrikam Logistics"
    assert {"PostgreSQL", "Kafka"} <= set(parsed.posting.must_have)
