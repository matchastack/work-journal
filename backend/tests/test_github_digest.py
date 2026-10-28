"""A repository's history as the model reads it (FR-GH-3 to FR-GH-5)."""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from app.github.api import Account, Commit, GitHubApi, Pull, Repository
from app.github.digest import (
    Digest,
    Item,
    build_digest,
    clean,
    clean_readme,
    describe_left_out,
)
from app.github.fake import TOKEN, FakeGitHubApi
from app.github.reader import ExpectedOwner, History, PullHistory, read_history

FIXTURES = Path(__file__).parent / "fixtures" / "github"
CASEY = {"id": 4242, "login": "casey-example", "type": "User"}
SAM = {"id": 5151, "login": "sam-example", "type": "User"}


def read(name: str) -> History:
    """The fixture repository's history, as `read_history` reads it from GitHub."""
    fake = FakeGitHubApi(json.loads((FIXTURES / "viewer.json").read_text(encoding="utf-8")))
    for fixture in ("recipe-box", "bird-calls", "tinyparse"):
        fake.add(json.loads((FIXTURES / f"{fixture}.json").read_text(encoding="utf-8")))

    async def history() -> History:
        async with httpx2.AsyncClient(transport=fake.transport) as http:
            api = GitHubApi(SecretStr(TOKEN), http)
            return await read_history(api, name, owner=ExpectedOwner(github_id=4242))

    return asyncio.run(history())


@pytest.fixture(scope="module")
def recipe_box() -> Digest:
    return build_digest(read("casey-example/recipe-box"))


def item(digest: Digest, label: str) -> Item:
    return next(item for item in digest.items if item.label == label)


# --- what's kept, and whose it is (FR-GH-4) ---


def test_the_owners_work_is_kept_oldest_first(recipe_box: Digest) -> None:
    assert [(item.label, item.kind, item.title) for item in recipe_box.items] == [
        ("about", "about", "About the repository."),
        ("c1", "commit", "Start Recipe Box with a React and PostgreSQL skeleton"),
        ("c2", "commit", "Cache parsed recipes, cutting page loads from 1.2 s to 300 ms"),
        ("pr1", "pull", "Add recipe import from a link"),
        ("c3", "commit", "Pair on the meal planner grid"),
        ("pr2", "pull", "Add a weekly shopping list"),
    ]
    assert recipe_box.owned
    assert (recipe_box.pulls_read, recipe_box.commits_read) == (4, 13)


def test_items_say_who_made_them(recipe_box: Digest) -> None:
    assert item(recipe_box, "c1").made_by == "By you"
    assert item(recipe_box, "c2").made_by == "By you, with Claude Code"
    assert item(recipe_box, "c2").agent == "Claude Code"
    assert item(recipe_box, "c3").made_by == "By you, with another person"
    assert item(recipe_box, "c3").coauthored
    assert item(recipe_box, "pr2").made_by == "By you, with Claude Code"


def test_what_was_left_out_is_counted(recipe_box: Digest) -> None:
    assert recipe_box.left_out == {
        "merge": 1,
        "revert": 1,
        "reverted": 1,
        "empty": 1,
        "covered": 5,
        "person": 2,
        "bot": 1,
    }
    assert describe_left_out(recipe_box.left_out) == (
        "1 merge, 1 revert, 1 change that was reverted, 1 empty message, 5 commits that are "
        "part of a pull request, 2 items by other people and 1 item by an automation bot"
    )


def test_a_repository_built_by_agents_is_the_owners() -> None:
    digest = build_digest(read("casey-example/bird-calls"))
    assert [(item.label, item.made_by) for item in digest.items] == [
        ("about", ""),
        ("pr1", "By you, with Claude Code"),
        ("pr2", "By you, with Claude Code"),
        ("c1", "By Claude Code, for you"),
        ("pr3", "By GitHub Copilot, for you"),
    ]
    assert digest.left_out == {"merge": 3, "covered": 4}
    assert not item(digest, "pr3").coauthored, "the owner co-authoring isn't another person"


def test_elsewhere_only_the_owners_work_and_their_agents_is_kept() -> None:
    digest = build_digest(read("tinyparse-org/tinyparse"))
    assert not digest.owned
    assert [(item.label, item.made_by) for item in digest.items] == [
        ("c1", "By you"),
        ("pr1", "By you, with another person"),
        ("pr2", "By GitHub Copilot, for you"),
    ]
    assert digest.left_out == {"covered": 1, "person": 1}


def test_a_pull_request_lists_the_owners_and_agents_commits_only() -> None:
    digest = build_digest(read("tinyparse-org/tinyparse"))
    escapes = item(digest, "pr1")
    assert "Commits:\n- Parse escapes in quoted strings" in escapes.body
    assert "Add escape tests" not in escapes.body, "another person's commit"
    shopping = item(build_digest(read("casey-example/recipe-box")), "pr2")
    assert shopping.body.endswith(
        "Commits:\n- Add the shopping list page\n- Sum quantities of the same ingredient"
    )


# --- the about item ---


def test_the_about_item_describes_an_owned_repository(recipe_box: Digest) -> None:
    about = item(recipe_box, "about")
    assert about.body.startswith("Save recipes and plan the week's meals.")
    assert "Languages: TypeScript, CSS, HTML." in about.body
    assert "It's built with React and PostgreSQL." in about.body
    assert "## Features" in about.body
    assert about.url == "https://github.com/casey-example/recipe-box"


def test_the_readme_loses_its_roadmap_thanks_code_and_images(recipe_box: Digest) -> None:
    about = item(recipe_box, "about").body
    for gone in ("Roadmap", "5 friends", "Acknowledgements", "sam-example", "5173", "badge"):
        assert gone not in about
    assert "## Install" not in about, "a heading left with nothing under it goes too"
    assert not re.search(r"\d", about), "no sizes, counts or ports"


# --- cleaning (FR-GH-5) ---


def test_items_lose_references_mentions_addresses_and_trailers(recipe_box: Digest) -> None:
    text = "\n".join(item.evidence for item in recipe_box.items)
    for gone in ("#12", "Fixes", "@sam", "example.com", "https://", "Co-authored-by", "[x]"):
        assert gone not in text
    assert "Thanks for the profiling!" in text
    assert "Questions to" not in text, "what's left around a removed email goes"
    assert "Describe your change" not in text, "an HTML comment"


def test_the_agent_footer_and_session_links_go() -> None:
    digest = build_digest(read("casey-example/bird-calls"))
    text = "\n".join(item.evidence for item in digest.items)
    assert "Generated with" not in text
    assert "Claude-Session" not in text
    assert "claude.ai" not in text


def test_evidence_holds_no_labels_or_dates(recipe_box: Digest) -> None:
    for each in recipe_box.items:
        assert each.label not in each.evidence
        assert "2023" not in each.evidence
        assert each.render().endswith(each.evidence)
    assert item(recipe_box, "pr1").render().startswith("[pr1] Pull request, merged in July 2023.")
    assert item(recipe_box, "c1").render().startswith("[c1] Commit, June 2023.\nBy you.")


def test_numbers_the_items_give_stay(recipe_box: Digest) -> None:
    assert "1.2 s to 300 ms" in item(recipe_box, "c2").evidence
    assert "40 recipe sites" in item(recipe_box, "pr1").evidence


@pytest.mark.parametrize(
    ("text", "cleaned"),
    [
        ("Undo a1b2c3d4e5 and keep 1234567", "Undo and keep 1234567"),
        ("See #12, GH-4 and other/repo#9 too", "See, and too"),
        ("Fixes #12. Adds a cache", "Adds a cache"),
        ("Thanks @sam-example and @renovate[bot] for the help", "Thanks and for the help"),
        ("Mail ada@example.com or visit https://example.com/x today", "Mail or visit today"),
        ("Uses [FastAPI](https://fastapi.example) for the API", "Uses FastAPI for the API"),
        ("A <b>bold</b> move <!-- note -->", "A bold move"),
        ("Ship it \U0001f680 now", "Ship it now"),
        ("A change\n\nSigned-off-by: Ada <ada@example.com>\nChange-Id: I12ab", "A change"),
        ("Steps\n- [x] Tests pass\n- [ ] Docs", "Steps"),
        ("Cut 50\u201360 ms \u2014 nice", "Cut 50-60 ms - nice"),
        ("#12: fix the parser\n.gitignore the build", "fix the parser\n.gitignore the build"),
        (
            "Ported the service to .NET 8 and moved secrets to .env",
            "Ported the service to .NET 8 and moved secrets to .env",
        ),
        ("Body\n- \U0001f41b Fix crash\n\u2728 Faster builds", "Body\n- Fix crash\nFaster builds"),
        (
            "Migrate to @tanstack/react-query and @types/node",
            "Migrate to @tanstack/react-query and @types/node",
        ),
        (
            "Return Result<User> and Vec<u8> from the <b>parser</b>",
            "Return Result<User> and Vec<u8> from the parser",
        ),
        ("Use C#10 records and F# types (#12)", "Use C#10 records and F# types"),
        (
            "Body\nSigned-off-by:\nAdded caching layer for search results",
            "Body\n\nAdded caching layer for search results",
        ),
    ],
)
def test_clean(text: str, cleaned: str) -> None:
    assert clean(text, max_chars=200) == cleaned


def test_leftover_lines_go_from_text_but_not_from_titles() -> None:
    assert clean("Notes: https://example.com/notes", max_chars=100) == ""
    assert clean("Notes: https://example.com/notes", max_chars=100, leftovers=False) == "Notes:"
    assert clean("Fix #12", max_chars=100, leftovers=False) == "", "a title that only closes one"


def test_long_text_is_cut_at_a_word() -> None:
    assert clean("one two three four five", max_chars=14) == "one two..."


@pytest.mark.parametrize(
    "heading",
    ["## \U0001f5fa\ufe0f Roadmap", "## 1. Planned features", "Roadmap\n-------", "## To-do list"],
)
def test_readme_sections_are_found_however_their_heading_is_written(heading: str) -> None:
    readme = f"# Tool\n\nIntro.\n\n{heading}\n\n- Reach 500 users\n\n## Usage\n\nRun it.\n"
    assert clean_readme(readme) == "# Tool\n\nIntro.\n\n## Usage\n\nRun it."


def test_setext_headings_count_as_headings_but_rules_dont() -> None:
    readme = "Tool\n====\n\nIntro.\n\n---\n\nMore.\n\nCredits\n-------\n\nSam made the icons.\n"
    assert clean_readme(readme) == "# Tool\n\nIntro.\n\n---\n\nMore."


def test_readme_sections_are_dropped_with_their_subsections() -> None:
    readme = (
        "# Tool\n\nIntro.\n\n## To-do\n\n- More\n\n### Later\n\nAlso\n\n"
        "## Usage\n\nRun it.\n\n## License\n\nMIT\n"
    )
    assert clean_readme(readme) == "# Tool\n\nIntro.\n\n## Usage\n\nRun it."


# --- windows ---


def test_items_are_split_into_windows(recipe_box: Digest) -> None:
    history = read("casey-example/recipe-box")
    small = build_digest(history, window_items=2)
    assert [len(window) for window in small.windows] == [2, 2, 2]
    assert small.render_window(0).startswith(
        "Repository: casey-example/recipe-box. It's the person's own repository.\nPart 1 of 3."
    )
    one_each = build_digest(history, window_chars=10)
    assert len(one_each.windows) == len(one_each.items)
    assert len(recipe_box.windows) == 1


def test_a_window_elsewhere_says_whose_repository_it_is() -> None:
    digest = build_digest(read("tinyparse-org/tinyparse"))
    assert "someone else's repository; only the person's work in it is listed" in (
        digest.render_window(0)
    )


# --- reverted pull requests and rebase merges, on a history built here ---


def account(fields: dict[str, Any]) -> Account:
    return Account.model_validate(fields)


def commit(label: str, message: str, author: dict[str, Any] = CASEY, day: int = 1) -> Commit:
    signature = {"name": "x", "email": "casey@example.com", "date": f"2026-02-{day:02d}T00:00:00Z"}
    return Commit.model_validate(
        {
            "sha": f"{label:0<40}",
            "html_url": f"https://github.com/casey-example/r/commit/{label}",
            "commit": {"message": message, "author": signature, "committer": signature},
            "author": author,
            "parents": [{"sha": "f" * 40}],
        }
    )


def pull(number: int, title: str, body: str = "", user: dict[str, Any] = CASEY) -> Pull:
    return Pull.model_validate(
        {
            "number": number,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/casey-example/r/pull/{number}",
            "user": user,
            "pull_request": {"merged_at": f"2026-03-{number:02d}T00:00:00Z"},
        }
    )


def history(pulls: list[PullHistory], commits: list[Commit]) -> History:
    repository = Repository.model_validate(
        {
            "id": 1,
            "full_name": "casey-example/r",
            "html_url": "https://github.com/casey-example/r",
            "private": True,
            "owner": CASEY,
            "default_branch": "main",
        }
    )
    return History(account(CASEY), repository, tuple(pulls), tuple(commits))


def test_a_reverted_pull_request_and_its_revert_are_left_out() -> None:
    digest = build_digest(
        history(
            [
                PullHistory(pull(1, "Add search"), ()),
                PullHistory(pull(2, 'Revert "Add search"', "Reverts casey-example/r#1"), ()),
                PullHistory(pull(3, "Add tags"), ()),
            ],
            [],
        )
    )
    assert [item.title for item in digest.items] == ["Add tags"]
    assert digest.left_out == {"revert": 1, "reverted": 1}


def test_a_rebase_merges_copies_are_part_of_their_pull_request() -> None:
    original = commit("aa1", "Add tags", day=3)
    copy = commit("bb2", "Add tags", day=3)
    digest = build_digest(
        history([PullHistory(pull(4, "Tags"), (original,))], [copy, commit("cc3", "Fix typo")])
    )
    assert [item.title for item in digest.items] == ["Fix typo", "Tags"]
    assert digest.left_out == {"covered": 1}


def test_a_private_repository_says_so() -> None:
    digest = build_digest(history([], [commit("dd4", "Start")]))
    assert digest.private
    assert digest.items[0].label != "about", "no about item without its text being read"


def test_nothing_left_out_reads_as_nothing() -> None:
    assert describe_left_out({}) == ""
    assert describe_left_out({"merge": 2}) == "2 merges"
