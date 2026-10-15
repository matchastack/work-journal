"""Opt-in evaluation of tailoring on the fictional person and three fictional postings.

The planner calls the heavy-tier model, and swaps, new phrasings and the claim check the
standard-tier one, so these run only with `pytest -m llm` and need ANTHROPIC_API_KEY,
LLM_MODEL_HEAVY and LLM_MODEL_STANDARD. Each resume must pass the resume rules, keep the
load-bearing role, list only catalogue skills, and fit the posting's role type.
"""

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.config import Settings
from app.llm.client import LLMClient
from app.schema.fact import Fact
from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.tailoring.tailor import Tailored, posting_spellings, tailor, use_phrasings
from app.validate.lint import check_sendable

pytestmark = pytest.mark.llm

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
FACTS = TypeAdapter(tuple[Fact, ...]).validate_json(
    (FIXTURES / "facts.json").read_text(encoding="utf-8")
)
CATALOGUE = {
    spelling.casefold() for skill in PROFILE.skills for spelling in (skill.name, *skill.aliases)
}
ROLE_TYPES = {"backend": "backend", "data": "data", "frontend": None}
"""The role type each posting should get; no role type is close to the frontend one."""


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> LLMClient:
    settings = Settings()
    needed = (settings.anthropic_api_key, settings.llm_model_heavy, settings.llm_model_standard)
    if any(value is None for value in needed):
        pytest.skip("set ANTHROPIC_API_KEY, LLM_MODEL_HEAVY and LLM_MODEL_STANDARD to call the API")
    log = tmp_path_factory.mktemp("llm") / "calls.jsonl"
    return LLMClient.from_settings(settings.model_copy(update={"llm_call_log": log}))


def tailored_to(name: str, client: LLMClient) -> tuple[JobPosting, Tailored]:
    posting = JobPosting.model_validate_json(
        (FIXTURES / "postings" / f"{name}.json").read_text(encoding="utf-8")
    )
    return posting, tailor(PROFILE, posting, client, facts=FACTS)


@pytest.mark.parametrize("name", ["backend", "data", "frontend"])
def test_the_resume_passes_the_rules_and_fits_the_posting(name: str, client: LLMClient) -> None:
    posting, tailored = tailored_to(name, client)
    accepted = {proposal.bullet_id for proposal in tailored.proposals}
    selection = use_phrasings(tailored, accepted)
    report = check_sendable(selection, PROFILE, posting_spellings=posting_spellings(posting))
    assert report.errors == (), [finding.message for finding in report.errors]
    assert "northwind" in [role.id for role in selection.work], "the load-bearing role"
    for line in selection.skills:
        for skill in line.skills:
            assert skill.casefold() in CATALOGUE, skill
            assert skill not in PROFILE.skill_gaps
    if ROLE_TYPES[name] is not None:
        assert tailored.role_type == ROLE_TYPES[name]
    for phrasing in (*tailored.swaps, *tailored.proposals):
        assert phrasing.report.passed, phrasing.report.findings


def test_the_data_posting_keeps_the_role_kept_for_data(client: LLMClient) -> None:
    _, tailored = tailored_to("data", client)
    assert [role.id for role in tailored.selection.work] == ["northwind", "contoso"]
    skills = {skill for line in tailored.selection.skills for skill in line.skills}
    assert {"Apache Airflow", "SQL"} <= skills
    assert not skills & {"dbt", "Snowflake", "BigQuery"}, "the posting's, not the person's"
