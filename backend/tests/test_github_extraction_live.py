"""Opt-in evaluation of facts from the fictional repositories' history (FR-GH-4, FR-GH-6).

It calls the standard-tier model, so it runs only with `pytest -m llm` and needs
ANTHROPIC_API_KEY and LLM_MODEL_STANDARD. GitHub is the stand-in, serving the fixtures.
"""

import asyncio
import json
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.github.api import GitHubApi
from app.github.digest import build_digest
from app.github.extraction import RepoExtraction, RepoTarget, extract_repo_facts
from app.github.fake import TOKEN, FakeGitHubApi
from app.github.reader import ExpectedOwner, read_history
from app.llm.client import LLMClient
from app.schema.fact import SingleValue
from app.schema.profile import Profile

pytestmark = pytest.mark.llm

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))


def extract(name: str, target: RepoTarget, tmp_path: Path) -> RepoExtraction:
    settings = Settings()
    if settings.anthropic_api_key is None or settings.llm_model_standard is None:
        pytest.skip("set ANTHROPIC_API_KEY and LLM_MODEL_STANDARD to call the API")
    log = tmp_path / "calls.jsonl"
    client = LLMClient.from_settings(settings.model_copy(update={"llm_call_log": log}))
    payloads = FIXTURES / "github"
    fake = FakeGitHubApi(json.loads((payloads / "viewer.json").read_text(encoding="utf-8")))
    for fixture in ("recipe-box", "bird-calls", "tinyparse"):
        fake.add(json.loads((payloads / f"{fixture}.json").read_text(encoding="utf-8")))

    async def read() -> RepoExtraction:
        async with httpx2.AsyncClient(transport=fake.transport) as http:
            api = GitHubApi(SecretStr(TOKEN), http)
            history = await read_history(api, name, owner=ExpectedOwner(github_id=4242))
        digest = build_digest(history)
        return await asyncio.to_thread(extract_repo_facts, digest, PROFILE, client, target=target)

    return asyncio.run(read())


def test_the_owners_repository_gives_their_facts_only(tmp_path: Path) -> None:
    extraction = extract("casey-example/recipe-box", RepoTarget(project_id="recipe_box"), tmp_path)
    assert extraction.facts
    assert extraction.dropped == ()
    for fact in extraction.facts:
        assert (fact.origin, fact.project_id, fact.role_id) == ("github", "recipe_box", None)
        assert fact.ownership in ("owned", "contributed", "assisted")
        text = " ".join([fact.statement, fact.outcome or "", *fact.tools]).lower()
        for other in ("vite", "dependabot", "print view", "dark theme", "fraction"):
            assert other not in text, "left-out work never becomes a fact"


def test_a_repository_built_by_agents_credits_the_owner_and_names_the_agents(
    tmp_path: Path,
) -> None:
    target = RepoTarget(project_id="bird_calls")
    extraction = extract("casey-example/bird-calls", target, tmp_path)
    assert extraction.facts
    assert extraction.dropped == ()
    tools = {tool for fact in extraction.facts for tool in fact.tools}
    assert "Claude Code" in tools
    assert {fact.ownership for fact in extraction.facts} <= {"owned"}
    values = [metric.value for fact in extraction.facts for metric in fact.metrics]
    assert SingleValue(value=91) in values


def test_work_elsewhere_is_at_most_contributed(tmp_path: Path) -> None:
    target = RepoTarget(new_project="tinyparse")
    extraction = extract("tinyparse-org/tinyparse", target, tmp_path)
    assert extraction.facts
    assert {fact.ownership for fact in extraction.facts} <= {"contributed", "assisted"}
    assert all(fact.proposed_item == "tinyparse" for fact in extraction.facts)
