"""The read-only GitHub client (FR-GH-1, FR-GH-9), against a stand-in for GitHub's REST API."""

import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.github.api import (
    GitHubApi,
    GitHubApiError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubTokenError,
    check_repository_name,
    check_token,
)
from app.github.fake import TOKEN, FakeGitHubApi

FIXTURES = Path(__file__).parent / "fixtures" / "github"
RECIPE_BOX = "casey-example/recipe-box"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def payload(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def github() -> FakeGitHubApi:
    fake = FakeGitHubApi(payload("viewer"))
    for name in ("recipe-box", "bird-calls", "tinyparse"):
        fake.add(payload(name))
    return fake


@pytest.fixture
async def api(github: FakeGitHubApi) -> AsyncIterator[GitHubApi]:
    async with httpx2.AsyncClient(transport=github.transport) as http:
        yield GitHubApi(SecretStr(TOKEN), http)


def answering(handler: Callable[[httpx2.Request], httpx2.Response]) -> GitHubApi:
    return GitHubApi(SecretStr(TOKEN), httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


# --- the token (FR-GH-1) ---


def test_a_classic_token_is_refused() -> None:
    with pytest.raises(GitHubTokenError, match="fine-grained"):
        check_token(SecretStr("ghp_classic-token"))
    check_token(SecretStr(TOKEN))


def test_the_client_needs_the_token_setting() -> None:
    http = httpx2.AsyncClient()
    with pytest.raises(GitHubTokenError, match="Set GITHUB_REPOS_TOKEN"):
        GitHubApi.from_settings(Settings(github_repos_token=None), http)
    with pytest.raises(GitHubTokenError, match="fine-grained"):
        GitHubApi.from_settings(Settings(github_repos_token=SecretStr("gho_oauth")), http)
    api = GitHubApi.from_settings(Settings(github_repos_token=SecretStr(TOKEN)), http)
    assert api.token.get_secret_value() == TOKEN


def test_repository_names_are_checked() -> None:
    assert check_repository_name("casey-example/recipe-box") == "casey-example/recipe-box"
    for name in ("recipe-box", "a/b/c", "../etc", "casey example/x", "-casey/x"):
        with pytest.raises(ValueError, match="isn't a repository name"):
            check_repository_name(name)


@pytest.mark.anyio
async def test_requests_carry_the_token_and_the_api_version(
    api: GitHubApi, github: FakeGitHubApi
) -> None:
    await api.viewer()
    request = github.requests[0]
    assert request.method == "GET"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"]


# --- reading ---


@pytest.mark.anyio
async def test_the_account_and_the_repository(api: GitHubApi) -> None:
    viewer = await api.viewer()
    assert (viewer.id, viewer.login) == (4242, "casey-example")
    repository = await api.repository(RECIPE_BOX)
    assert repository.owner.id == 4242
    assert repository.default_branch == "main"
    assert not repository.private


@pytest.mark.anyio
async def test_languages_come_most_code_first(api: GitHubApi) -> None:
    assert await api.languages(RECIPE_BOX) == ("TypeScript", "CSS", "HTML")


@pytest.mark.anyio
async def test_the_readme_is_read_as_written(api: GitHubApi, github: FakeGitHubApi) -> None:
    readme = await api.readme(RECIPE_BOX)
    assert readme is not None
    assert readme.startswith("# Recipe Box")
    assert github.requests[-1].headers["Accept"] == "application/vnd.github.raw+json"


@pytest.mark.anyio
async def test_a_repository_without_a_readme_has_none(api: GitHubApi) -> None:
    assert await api.readme("casey-example/bird-calls") is None


@pytest.mark.anyio
async def test_commits_follow_every_page(api: GitHubApi, github: FakeGitHubApi) -> None:
    github.page_size = 3
    commits = await api.commits(RECIPE_BOX, branch="main")
    assert len(commits) == 13
    assert len(github.requests) == 5
    assert commits[0].message == "Add a print view (#6)"
    assert commits[-1].author is None, "the first commit's email belongs to no account"


@pytest.mark.anyio
async def test_commits_ask_for_the_branch_the_time_and_the_author(
    api: GitHubApi, github: FakeGitHubApi
) -> None:
    since = datetime(2025, 1, 21, tzinfo=UTC)
    commits = await api.commits(
        "tinyparse-org/tinyparse", branch="main", since=since, author="casey-example"
    )
    assert [commit.message for commit in commits] == ["Parse quoted strings with escapes (#41)"]
    params = github.requests[-1].url.params
    assert params["sha"] == "main"
    assert params["since"] == "2025-01-21T00:00:00Z"
    assert params["author"] == "casey-example"
    assert params["per_page"] == "100"


@pytest.mark.anyio
async def test_a_pull_requests_commits(api: GitHubApi) -> None:
    commits = await api.pull_commits(RECIPE_BOX, 4)
    assert [commit.authored_at.day for commit in commits] == [18, 19]
    assert all(commit.author is not None and commit.author.login == "claude" for commit in commits)


@pytest.mark.anyio
async def test_merged_pulls_are_searched(api: GitHubApi, github: FakeGitHubApi) -> None:
    since = datetime(2025, 2, 1, tzinfo=UTC)
    pulls, more = await api.merged_pulls(
        "tinyparse-org/tinyparse", since=since, author="casey-example"
    )
    assert [pull.number for pull in pulls] == [41]
    assert not more
    query = github.requests[-1].url.params["q"]
    assert query.split() == [
        "repo:tinyparse-org/tinyparse",
        "is:pr",
        "is:merged",
        "merged:>=2025-02-01",
        "author:casey-example",
    ]
    assigned, _ = await api.merged_pulls("tinyparse-org/tinyparse", assignee="casey-example")
    assert [pull.number for pull in assigned] == [42, 43]


@pytest.mark.anyio
async def test_merged_pulls_follow_every_page(api: GitHubApi, github: FakeGitHubApi) -> None:
    github.page_size = 2
    pulls, _ = await api.merged_pulls(RECIPE_BOX)
    assert [pull.number for pull in pulls] == [3, 4, 5, 6]
    assert str(pulls[0].merged_at.date()) == "2023-07-10"


@pytest.mark.anyio
async def test_more_pulls_than_the_search_returns_are_reported() -> None:
    item = {
        "number": 1,
        "title": "A change",
        "html_url": "https://github.com/o/r/pull/1",
        "user": {"id": 1, "login": "o"},
        "pull_request": {"merged_at": "2026-01-01T00:00:00Z"},
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"total_count": 1500, "items": [item]})

    pulls, more = await answering(handler).merged_pulls("o/r")
    assert len(pulls) == 1
    assert more


# --- errors, which never quote the token, an address or a repository's name (FR-GH-9) ---


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "headers", "error", "words"),
    [
        (401, {}, GitHubTokenError, "refused the token"),
        (403, {}, GitHubTokenError, "may not read this repository"),
        (404, {}, GitHubNotFound, "doesn't exist, or the token can't read it"),
        (500, {}, GitHubApiError, "failed with status 500"),
        (
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1767225600"},
            GitHubRateLimited,
            "until 00:00",
        ),
        (429, {"retry-after": "60"}, GitHubRateLimited, "rate limit is used up"),
    ],
)
async def test_refusals_raise_clear_errors(
    status: int, headers: dict[str, str], error: type[GitHubApiError], words: str
) -> None:
    api = answering(lambda request: httpx2.Response(status, headers=headers, json={}))
    with pytest.raises(error, match=words) as raised:
        await api.commits(RECIPE_BOX, branch="main")
    message = str(raised.value)
    assert "recipe-box" not in message
    assert TOKEN not in message
    assert raised.value.__cause__ is None, "a chained error would quote the address"


@pytest.mark.anyio
async def test_the_rate_limit_says_when_it_resets() -> None:
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1767225600"}
    api = answering(lambda request: httpx2.Response(403, headers=headers))
    with pytest.raises(GitHubRateLimited) as raised:
        await api.viewer()
    assert raised.value.reset_at == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.anyio
async def test_github_out_of_reach_raises_an_error(api: GitHubApi) -> None:
    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to host", request=request)

    with pytest.raises(GitHubApiError, match=r"couldn't be reached .*ConnectError") as raised:
        await answering(unreachable).repository(RECIPE_BOX)
    assert "recipe-box" not in str(raised.value)


@pytest.mark.anyio
async def test_an_unexpected_reply_raises_an_error() -> None:
    with pytest.raises(GitHubApiError, match="unexpected reply to the repository"):
        await answering(lambda request: httpx2.Response(200, text="<html>")).repository("o/r")
    with pytest.raises(GitHubApiError, match="unexpected reply to the commits"):
        await answering(lambda request: httpx2.Response(200, json={})).commits("o/r", branch="b")
    with pytest.raises(GitHubApiError, match="unexpected reply to the account"):
        await answering(lambda request: httpx2.Response(200, json={"id": "x"})).viewer()


@pytest.mark.anyio
async def test_a_wrong_token_is_refused(github: FakeGitHubApi) -> None:
    async with httpx2.AsyncClient(transport=github.transport) as http:
        with pytest.raises(GitHubTokenError):
            await GitHubApi(SecretStr("github_pat_wrong"), http).viewer()


@pytest.mark.anyio
async def test_no_repository_name_reaches_the_log(
    api: GitHubApi, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await api.commits(RECIPE_BOX, branch="main", author="casey-example")
        await api.readme(RECIPE_BOX)
    assert any("api.github.com/<path>" in record.getMessage() for record in caplog.records)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "recipe-box" not in logged
    assert "casey-example" not in logged
    assert TOKEN not in logged
