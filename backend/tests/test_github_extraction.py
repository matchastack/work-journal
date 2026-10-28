"""Facts from a repository's history (FR-GH-4, FR-GH-6), with the fake LLM client."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import transform_schema
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.github.api import GitHubApi
from app.github.digest import Digest, build_digest
from app.github.extraction import (
    RepoExtraction,
    RepoExtractionReply,
    RepoFactReply,
    RepoTarget,
    ask_for_repo_facts,
    check_repo_facts,
    extract_repo_facts,
    repo_context,
)
from app.github.fake import TOKEN, FakeGitHubApi
from app.github.reader import ExpectedOwner, read_history
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import MemoryCallLog
from app.schema.fact import SingleValue
from app.schema.profile import Profile

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
RECIPE_BOX = RepoTarget(project_id="recipe_box")
SHOPPING = "Added a weekly shopping list that groups ingredients by aisle."


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def digest(name: str, **options: int) -> Digest:
    payloads = FIXTURES / "github"
    fake = FakeGitHubApi(json.loads((payloads / "viewer.json").read_text(encoding="utf-8")))
    for fixture in ("recipe-box", "bird-calls", "tinyparse"):
        fake.add(json.loads((payloads / f"{fixture}.json").read_text(encoding="utf-8")))

    async def read() -> Digest:
        async with httpx2.AsyncClient(transport=fake.transport) as http:
            api = GitHubApi(SecretStr(TOKEN), http)
            history = await read_history(api, name, owner=ExpectedOwner(github_id=4242))
        return build_digest(history, **options)

    return asyncio.run(read())


@pytest.fixture(scope="module")
def recipe_box() -> Digest:
    return digest("casey-example/recipe-box")


def client(*replies: RepoExtractionReply) -> tuple[LLMClient, FakeMessages]:
    fake = FakeMessages(*(reply(answer) for answer in replies))
    settings = Settings(llm_model_standard="standard-model")
    return LLMClient(settings, fake, MemoryCallLog()), fake


def fact(items: list[str], statement: str = SHOPPING, **fields: Any) -> RepoFactReply:
    return RepoFactReply.model_validate(
        {"items": items, "statement": statement, "kind": "accomplishment", **fields}
    )


def checked(
    found: Digest, *facts: RepoFactReply, target: RepoTarget = RECIPE_BOX, index: int = 0
) -> RepoExtraction:
    reply = RepoExtractionReply(facts=facts)
    return check_repo_facts(reply, found, index, PROFILE, target=target, part="0123abcd")


# --- the request ---


def test_the_model_reads_one_part_with_the_skills_and_the_project(recipe_box: Digest) -> None:
    llm, fake = client(RepoExtractionReply(facts=()))
    ask_for_repo_facts(recipe_box, 0, PROFILE, llm, target=RECIPE_BOX)
    request = fake.requests[0]
    system = json.dumps(request["system"])
    assert "repository's history" in system
    assert '\\"project\\": \\"Recipe Box\\"' in system
    assert "Northwind" not in system, "a GitHub fact belongs to a project, never to a role"
    message = json.dumps(request["messages"])
    assert "[pr2] Pull request, merged in August 2023." in message
    assert "<history>" in message


def test_the_reply_schema_works_with_structured_outputs() -> None:
    transform_schema(RepoExtractionReply)


def test_the_reply_can_only_lower_ownership() -> None:
    for ownership in ("led", "owned"):
        with pytest.raises(ValidationError):
            fact(["pr2"], ownership=ownership)


# --- what code sets ---


def test_a_fact_from_github_links_to_its_project_and_items(recipe_box: Digest) -> None:
    kept = checked(recipe_box, fact(["pr2"], tools=["Claude Code"]))
    assert kept.dropped == ()
    (made,) = kept.facts
    assert made.id == "gh_0123abcd_1"
    assert made.origin == "github"
    assert (made.project_id, made.role_id, made.entry_id) == ("recipe_box", None, None)
    assert made.date == "2023-08"
    assert made.ownership == "owned"
    assert made.tools == ("Claude Code",)
    assert made.source is not None
    assert made.source.repository == "casey-example/recipe-box"
    assert [(item.kind, item.ref) for item in made.source.items] == [("pull", "4")]
    assert made.source.items[0].url == "https://github.com/casey-example/recipe-box/pull/4"


def test_the_agent_that_made_the_work_joins_the_tools(recipe_box: Digest) -> None:
    (made,) = checked(recipe_box, fact(["pr2"])).facts
    assert made.tools == ("Claude Code",)
    bird_calls = digest("casey-example/bird-calls")
    (typed,) = checked(bird_calls, fact(["pr3"], "Added type hints to the data loader.")).facts
    assert typed.tools == ("GitHub Copilot",)


def test_the_date_is_the_latest_cited_month(recipe_box: Digest) -> None:
    statement = "Started Recipe Box and added recipe import from a link."
    (made,) = checked(recipe_box, fact(["c1", "pr1"], statement)).facts
    assert made.date == "2023-07"
    assert made.source is not None
    assert [item.ref for item in made.source.items if item.kind == "pull"] == ["3"]


def test_a_new_project_is_named_by_the_owner(recipe_box: Digest) -> None:
    target = RepoTarget(new_project="Meal Planner")
    (made,) = checked(recipe_box, fact(["pr2"]), target=target).facts
    assert (made.project_id, made.proposed_item) == (None, "Meal Planner")


def test_the_target_must_be_one_project_and_not_led() -> None:
    with pytest.raises(ValueError, match="either"):
        RepoTarget()
    with pytest.raises(ValueError, match="either"):
        RepoTarget(project_id="recipe_box", new_project="Recipe Box")
    with pytest.raises(ValueError, match="led"):
        RepoTarget(project_id="recipe_box", ownership="led")
    with pytest.raises(ValueError, match="no project 'nope'"):
        repo_context(PROFILE, RepoTarget(project_id="nope"))


# --- ownership (FR-GH-6) ---


def test_ownership_is_capped_by_whose_repository_and_work_it_is(recipe_box: Digest) -> None:
    (own,) = checked(recipe_box, fact(["c1"], "Started Recipe Box.")).facts
    (paired,) = checked(recipe_box, fact(["c3"], "Paired on the meal planner grid.")).facts
    (lower,) = checked(recipe_box, fact(["c1"], "Started Recipe Box.", ownership="assisted")).facts
    assert (own.ownership, paired.ownership, lower.ownership) == (
        "owned",
        "contributed",
        "assisted",
    )
    elsewhere = digest("tinyparse-org/tinyparse")
    tinyparse = RepoTarget(new_project="tinyparse")
    (theirs,) = checked(
        elsewhere, fact(["c1"], "Fixed the README example."), target=tinyparse
    ).facts
    assert theirs.ownership == "contributed"


def test_the_owner_can_set_the_cap(recipe_box: Digest) -> None:
    target = RepoTarget(project_id="recipe_box", ownership="contributed")
    (made,) = checked(recipe_box, fact(["c1"], "Started Recipe Box."), target=target).facts
    assert made.ownership == "contributed"
    elsewhere = digest("tinyparse-org/tinyparse")
    owned = RepoTarget(new_project="tinyparse", ownership="owned")
    (fixed,) = checked(elsewhere, fact(["c1"], "Fixed the README example."), target=owned).facts
    assert fixed.ownership == "owned"
    (escapes,) = checked(elsewhere, fact(["pr1"], "Parsed escapes."), target=owned).facts
    assert escapes.ownership == "contributed", "another person co-authored it"


# --- what's checked against the cited items ---


def test_a_number_only_an_uncited_item_gives_drops_the_fact(recipe_box: Digest) -> None:
    statement = "Cut page loads from 1.2 s to 300 ms by caching parsed recipes."
    metric = {"subject": "page load", "kind": "change", "numbers": [1.2, 0.3], "unit": "s"}
    kept = checked(recipe_box, fact(["pr1"], statement))
    assert kept.facts == ()
    (dropped,) = kept.dropped
    assert dropped.statement == statement
    assert any("the items it cites don't give" in reason for reason in dropped.reasons)
    backed = checked(recipe_box, fact(["c2"], statement))
    assert len(backed.facts) == 1
    assert backed.dropped == ()
    assert checked(recipe_box, fact(["c2"], statement, metrics=[metric])).facts == ()


def test_numbers_the_cited_items_give_are_kept(recipe_box: Digest) -> None:
    metric = {"subject": "recipe sites supported", "kind": "single", "numbers": [40]}
    statement = "Added recipe import from a link, working with 40 recipe sites."
    (made,) = checked(recipe_box, fact(["pr1"], statement, metrics=[metric])).facts
    assert made.metrics[0].value == SingleValue(value=40)


def test_a_label_or_a_date_never_backs_a_number(recipe_box: Digest) -> None:
    for statement in ("Shipped 2 features in 2023.", "Added a shopping list for 4 people."):
        assert checked(recipe_box, fact(["pr2"], statement)).facts == ()


def test_a_tool_the_cited_items_dont_name_is_removed(recipe_box: Digest) -> None:
    kept = checked(
        recipe_box, fact(["c2"], "Cached parsed recipes.", tools=["Redis", "PostgreSQL"])
    )
    (made,) = kept.facts
    assert made.tools == ("Claude Code",)
    assert kept.notes == (
        'gh_0123abcd_1: removed the tool "Redis", which the items it cites don\'t name',
        'gh_0123abcd_1: removed the tool "PostgreSQL", which the items it cites don\'t name',
    )


def test_the_about_item_backs_the_languages_and_the_readme(recipe_box: Digest) -> None:
    statement = "Built Recipe Box, which saves recipes and plans the week's meals."
    tools = ["TypeScript", "React", "PostgreSQL"]
    (made,) = checked(recipe_box, fact(["about"], statement, kind="project", tools=tools)).facts
    assert made.kind == "project"
    assert made.tools == ("TypeScript", "React", "PostgreSQL")
    assert made.source is not None
    assert made.source.items[0].kind == "about"


def test_citing_nothing_or_an_item_the_part_doesnt_have_drops_the_fact(
    recipe_box: Digest,
) -> None:
    kept = checked(recipe_box, fact([]), fact(["pr9"]), fact(["pr2"]))
    assert [made.id for made in kept.facts] == ["gh_0123abcd_1"]
    assert [dropped.reasons for dropped in kept.dropped] == [
        ("it cites no item",),
        ("it cites pr9, which this part doesn't have",),
    ]


def test_an_item_in_another_part_cant_be_cited() -> None:
    split = digest("casey-example/recipe-box", window_items=2)
    assert [item.label for item in split.windows[0]] == ["about", "c1"]
    kept = checked(split, fact(["pr2"]), index=0)
    assert kept.dropped[0].reasons == ("it cites pr2, which this part doesn't have",)


# --- every part ---


def test_every_part_is_read_once_with_new_ids() -> None:
    split = digest("casey-example/recipe-box", window_items=3)
    assert len(split.windows) == 2
    llm, fake = client(
        RepoExtractionReply(facts=(fact(["c1"], "Started Recipe Box."),)),
        RepoExtractionReply(facts=(fact(["pr2"]),)),
    )
    extraction = extract_repo_facts(split, PROFILE, llm, target=RECIPE_BOX)
    assert len(fake.requests) == 2
    first, second = extraction.facts
    assert first.id.endswith("_1")
    assert second.id.endswith("_1")
    assert first.id != second.id
    assert all(made.id.startswith("gh_") and len(made.id) <= 64 for made in extraction.facts)


def test_a_part_with_only_routine_work_gives_no_facts(recipe_box: Digest) -> None:
    llm, _ = client(RepoExtractionReply(facts=()))
    assert extract_repo_facts(recipe_box, PROFILE, llm, target=RECIPE_BOX).facts == ()
