"""Reading a repository's history (FR-GH-1, FR-GH-3), against a stand-in for GitHub."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from app.github.api import Account, GitHubApi, GitHubTokenError
from app.github.fake import TOKEN, FakeGitHubApi
from app.github.reader import ExpectedOwner, check_owner, read_history

FIXTURES = Path(__file__).parent / "fixtures" / "github"
CASEY = ExpectedOwner(github_id=4242)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def github() -> FakeGitHubApi:
    fake = FakeGitHubApi(json.loads((FIXTURES / "viewer.json").read_text(encoding="utf-8")))
    for name in ("recipe-box", "bird-calls", "tinyparse"):
        fake.add(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))
    return fake


@pytest.fixture
async def api(github: FakeGitHubApi) -> AsyncIterator[GitHubApi]:
    async with httpx2.AsyncClient(transport=github.transport) as http:
        yield GitHubApi(SecretStr(TOKEN), http)


def searches(github: FakeGitHubApi) -> list[str]:
    return [r.url.params["q"] for r in github.requests if r.url.path == "/search/issues"]


# --- the token is the owner's (FR-GH-1) ---


def test_the_owner_is_known_by_number_or_by_username() -> None:
    viewer = Account(id=4242, login="Casey-Example")
    check_owner(viewer, ExpectedOwner(github_id=4242))
    check_owner(viewer, ExpectedOwner(login="casey-example"))
    with pytest.raises(GitHubTokenError, match="another GitHub account"):
        check_owner(viewer, ExpectedOwner(github_id=5151, login="casey-example"))
    with pytest.raises(GitHubTokenError, match="another GitHub account"):
        check_owner(viewer, ExpectedOwner(login="sam-example"))
    with pytest.raises(GitHubTokenError, match="isn't known whose history"):
        check_owner(viewer, ExpectedOwner())


@pytest.mark.anyio
async def test_another_accounts_token_reads_nothing(api: GitHubApi, github: FakeGitHubApi) -> None:
    with pytest.raises(GitHubTokenError):
        await read_history(api, "casey-example/recipe-box", owner=ExpectedOwner(github_id=5151))
    assert github.paths() == ["/user"]


# --- what's read (FR-GH-3) ---


@pytest.mark.anyio
async def test_an_owned_repository_is_read_whole(api: GitHubApi, github: FakeGitHubApi) -> None:
    history = await read_history(api, "casey-example/recipe-box", owner=CASEY)
    assert history.owned
    assert history.with_about
    assert [read.pull.number for read in history.pulls] == [3, 4, 5, 6]
    assert [len(read.commits) for read in history.pulls] == [1, 2, 1, 1]
    assert len(history.commits) == 13
    assert history.languages == ("TypeScript", "CSS", "HTML")
    assert history.readme is not None
    assert searches(github) == ["repo:casey-example/recipe-box is:pr is:merged base:main"]
    commits = next(r for r in github.requests if r.url.path.endswith("/commits"))
    assert "author" not in commits.url.params, "agents' commits aren't the owner's account's"


@pytest.mark.anyio
async def test_only_pull_requests_into_the_default_branch_are_read(
    github: FakeGitHubApi, api: GitHubApi
) -> None:
    recipe_box = json.loads((FIXTURES / "recipe-box.json").read_text(encoding="utf-8"))
    recipe_box["pulls"][1]["base"] = "release"
    github.add(recipe_box)
    history = await read_history(api, "casey-example/recipe-box", owner=CASEY)
    assert [read.pull.number for read in history.pulls] == [3, 5, 6]


@pytest.mark.anyio
async def test_the_owner_is_known_by_their_commits_emails(api: GitHubApi) -> None:
    history = await read_history(api, "casey-example/recipe-box", owner=CASEY)
    assert history.owner.is_email("casey@example.com")


@pytest.mark.anyio
async def test_the_about_text_can_be_left_unread(api: GitHubApi, github: FakeGitHubApi) -> None:
    history = await read_history(api, "casey-example/recipe-box", owner=CASEY, about=False)
    assert not history.with_about
    assert (history.languages, history.readme) == ((), None)
    assert not any(path.endswith(("/languages", "/readme")) for path in github.paths())


@pytest.mark.anyio
async def test_elsewhere_only_what_may_be_the_owners_is_read(
    api: GitHubApi, github: FakeGitHubApi
) -> None:
    history = await read_history(api, "tinyparse-org/tinyparse", owner=CASEY)
    assert not history.owned
    assert not history.with_about
    assert sorted(read.pull.number for read in history.pulls) == [41, 42, 43]
    assert searches(github) == [
        "repo:tinyparse-org/tinyparse is:pr is:merged base:main author:casey-example",
        "repo:tinyparse-org/tinyparse is:pr is:merged base:main assignee:casey-example",
    ]
    commits = next(r for r in github.requests if r.url.path.endswith("tinyparse/commits"))
    assert commits.url.params["author"] == "casey-example"
    assert not any(path.endswith(("/languages", "/readme")) for path in github.paths())


@pytest.mark.anyio
async def test_reading_from_a_month(api: GitHubApi, github: FakeGitHubApi) -> None:
    since = datetime(2023, 8, 1, tzinfo=UTC)
    history = await read_history(api, "casey-example/recipe-box", owner=CASEY, since=since)
    assert [read.pull.number for read in history.pulls] == [4, 5, 6]
    assert all(commit.commit.committer.date >= since for commit in history.commits)
    assert searches(github) == [
        "repo:casey-example/recipe-box is:pr is:merged base:main merged:>=2023-08-01"
    ]
