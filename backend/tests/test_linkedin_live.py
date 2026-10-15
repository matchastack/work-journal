"""Opt-in evaluation of the LinkedIn pack on the fictional person's profile.

Every section must pass the verifier, written once more if need be, fit LinkedIn's limits and
read as LinkedIn text: the first person, and no skill the profile rules out. The writer and the
claim check call the standard-tier model, so these run only with `pytest -m llm` and need
ANTHROPIC_API_KEY and LLM_MODEL_STANDARD.
"""

import re
from pathlib import Path

import pytest

from app.config import Settings
from app.linkedin import LinkedInPack, write_pack
from app.llm.client import LLMClient
from app.schema.profile import Profile

pytestmark = pytest.mark.llm

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
NEVER = (r"\bRust\b", r"\bSwift\b", r"\bKotlin\b", r"\b(owned|led)\b[^.]*pipeline")
"""The gaps list, a skill to confirm before claiming it, and Northwind's honesty boundary."""


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> LinkedInPack:
    settings = Settings()
    if settings.anthropic_api_key is None or settings.llm_model_standard is None:
        pytest.skip("set ANTHROPIC_API_KEY and LLM_MODEL_STANDARD to call the API")
    log = tmp_path_factory.mktemp("llm") / "calls.jsonl"
    client = LLMClient.from_settings(settings.model_copy(update={"llm_call_log": log}))
    return write_pack(PROFILE, client)


def test_every_section_passes_the_verifier_within_its_limit(pack: LinkedInPack) -> None:
    assert [section.key for section in pack.sections] == [
        "headline",
        "about",
        "position:northwind",
        "position:contoso",
    ]
    for section in pack.sections:
        assert section.report is not None
        errors = [finding.message for finding in section.report.errors]
        assert section.report.passed, (section.key, section.text, errors)
        assert section.length <= section.limit, section.key


def test_about_and_positions_are_in_the_first_person(pack: LinkedInPack) -> None:
    for section in pack.sections[1:]:
        assert re.search(r"\b(I|my)\b", section.text), (section.key, section.text)


def test_nothing_the_profile_rules_out_is_claimed(pack: LinkedInPack) -> None:
    for section in pack.sections:
        for pattern in NEVER:
            assert not re.search(pattern, section.text, re.IGNORECASE), (section.key, pattern)


def test_the_past_role_is_in_the_past_tense(pack: LinkedInPack) -> None:
    contoso = pack.sections[3].text
    assert not re.search(r"\bI (build|label|survey|work)\b", contoso), contoso
