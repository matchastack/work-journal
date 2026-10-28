"""The few GitHub REST API calls that read a repository's history, over HTTPS with httpx2.

GitHub's reference: https://docs.github.com/en/rest. The token is a fine-grained personal access
token, read-only and limited to the repositories the owner chose (FR-GH-1). It goes only into the
Authorization header, and the client never writes to GitHub.

Addresses name the repository, so `PathFilter` removes them from httpx2's request log, and errors
never quote an address, a repository's name or anything it holds (FR-GH-9).
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from pydantic import BaseModel, ConfigDict, RootModel, SecretStr, ValidationError

from app.auth.github import API_VERSION
from app.config import Settings

API_URL = "https://api.github.com"
TIMEOUT_S = 10.0
PER_PAGE = 100
SEARCH_LIMIT = 1000
"""GitHub's search returns at most this many results for one query (C7)."""
FINE_GRAINED_PREFIX = "github_pat_"
"""How a fine-grained personal access token starts. A classic token (`ghp_`) can't be limited to
chosen repositories or to reading, so it's refused."""
REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
"""`owner/name`, as GitHub allows them."""
_JSON = "application/vnd.github+json"
_RAW = "application/vnd.github.raw+json"
_PATH_IN_URL = re.compile(r"(api\.github\.com)/[^\s\"']*")


class GitHubApiError(Exception):
    """GitHub couldn't be reached or refused a request. The message never holds the token, an
    address, a repository's name or its text, so it's safe to log."""


class GitHubTokenError(GitHubApiError):
    """The token is missing or isn't fine-grained, GitHub refused it, or it isn't the owner's."""


class GitHubNotFound(GitHubApiError):
    """The repository doesn't exist, or the token can't read it."""


class GitHubRateLimited(GitHubApiError):
    """GitHub's rate limit is used up until `reset_at`."""

    def __init__(self, reset_at: datetime | None) -> None:
        self.reset_at = reset_at
        until = "for now" if reset_at is None else f"until {reset_at:%H:%M} UTC"
        super().__init__(f"GitHub's rate limit is used up {until}; try again then")


class PathFilter(logging.Filter):
    """Removes paths from httpx2's log of each GitHub request, which quotes the whole address,
    so that no repository's name reaches a log."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "api.github.com/" in message:
            record.msg = _PATH_IN_URL.sub(r"\1/<path>", message)
            record.args = None
        return True


logging.getLogger("httpx2").addFilter(PathFilter())


class _Reply(BaseModel):
    """Part of a reply from GitHub. Fields the app doesn't use are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class Account(_Reply):
    id: int
    login: str
    type: str = "User"
    """`User`, `Organization` or `Bot`."""


class Repository(_Reply):
    id: int
    full_name: str
    html_url: str
    private: bool
    fork: bool = False
    owner: Account
    description: str | None = None
    default_branch: str
    pushed_at: datetime | None = None


class Signature(_Reply):
    """Who wrote or committed a commit, as git recorded it."""

    name: str = ""
    email: str = ""
    date: datetime


class CommitDetail(_Reply):
    message: str
    author: Signature
    committer: Signature


class _Parent(_Reply):
    sha: str


class Commit(_Reply):
    sha: str
    html_url: str
    commit: CommitDetail
    author: Account | None = None
    """The GitHub account the author's email belongs to; None when no account claims it."""
    parents: tuple[_Parent, ...] = ()

    @property
    def message(self) -> str:
        return self.commit.message

    @property
    def authored_at(self) -> datetime:
        return self.commit.author.date

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


class _PullLinks(_Reply):
    merged_at: datetime | None = None


class Pull(_Reply):
    """A merged pull request, as GitHub's issue search returns it."""

    number: int
    title: str
    body: str | None = None
    html_url: str
    user: Account
    assignees: tuple[Account, ...] = ()
    pull_request: _PullLinks

    @property
    def merged_at(self) -> datetime:
        if self.pull_request.merged_at is None:
            raise ValueError("the pull request isn't merged")
        return self.pull_request.merged_at


class _Languages(RootModel[dict[str, int]]):
    """Bytes of code in each language."""


class _Search(_Reply):
    total_count: int = 0
    items: tuple[Pull, ...]


def check_token(token: SecretStr) -> None:
    """Refuse a token that isn't fine-grained, before anything is read (FR-GH-1)."""
    if not token.get_secret_value().startswith(FINE_GRAINED_PREFIX):
        raise GitHubTokenError(
            "GITHUB_REPOS_TOKEN must be a fine-grained personal access token (github_pat_...), "
            "which can only read the repositories you chose"
        )


def check_repository_name(full_name: str) -> str:
    if not REPOSITORY_NAME.match(full_name):
        raise ValueError(f"{full_name!r} isn't a repository name such as owner/name")
    return full_name


@dataclass(frozen=True)
class GitHubApi:
    token: SecretStr
    http: httpx2.AsyncClient

    @classmethod
    def from_settings(cls, settings: Settings, http: httpx2.AsyncClient) -> "GitHubApi":
        """The client for `GITHUB_REPOS_TOKEN`, which must be set and fine-grained."""
        token = settings.github_repos_token
        if token is None:
            raise GitHubTokenError(
                "Set GITHUB_REPOS_TOKEN to a fine-grained personal access token (github_pat_...) "
                "that can read the repository"
            )
        check_token(token)
        return cls(token, http)

    async def viewer(self) -> Account:
        """The account the token belongs to."""
        return _parse(Account, await self._json("/user", what="account"), "account")

    async def repository(self, full_name: str) -> Repository:
        path = f"/repos/{check_repository_name(full_name)}"
        return _parse(Repository, await self._json(path, what="repository"), "repository")

    async def languages(self, full_name: str) -> tuple[str, ...]:
        """The repository's languages, most code first."""
        path = f"/repos/{check_repository_name(full_name)}/languages"
        sizes = _parse(_Languages, await self._json(path, what="languages"), "languages").root
        return tuple(sorted(sizes, key=lambda name: -sizes[name]))

    async def readme(self, full_name: str) -> str | None:
        """The repository's README as written, or None when it has none."""
        path = f"/repos/{check_repository_name(full_name)}/readme"
        try:
            response = await self._get(f"{API_URL}{path}", None, what="README", accept=_RAW)
        except GitHubNotFound:
            return None
        return response.text

    async def commits(
        self,
        full_name: str,
        *,
        branch: str,
        since: datetime | None = None,
        author: str | None = None,
    ) -> list[Commit]:
        """The commits on `branch`, newest first: since `since` (by commit date), and only
        `author`'s (a GitHub username) when given."""
        params: dict[str, Any] = {"sha": branch, "per_page": PER_PAGE}
        if since is not None:
            params["since"] = _iso(since)
        if author is not None:
            params["author"] = author
        path = f"/repos/{check_repository_name(full_name)}/commits"
        items = await self._pages(path, params, what="commits")
        return [_parse(Commit, item, "commits") for item in items]

    async def pull_commits(self, full_name: str, number: int) -> list[Commit]:
        """The commits of pull request `number`, as they were before it was merged."""
        path = f"/repos/{check_repository_name(full_name)}/pulls/{number}/commits"
        items = await self._pages(path, {"per_page": PER_PAGE}, what="pull request's commits")
        return [_parse(Commit, item, "pull request's commits") for item in items]

    async def merged_pulls(
        self,
        full_name: str,
        *,
        base: str | None = None,
        since: datetime | None = None,
        author: str | None = None,
        assignee: str | None = None,
    ) -> tuple[list[Pull], bool]:
        """The merged pull requests, merged into `base` (any branch when None) on or after
        `since`'s day, and only `author`'s or those assigned to `assignee` when given. Also
        whether there were more than the search returns (`SEARCH_LIMIT`)."""
        terms = [f"repo:{check_repository_name(full_name)}", "is:pr", "is:merged"]
        if base is not None:
            terms.append(f"base:{base}")
        if since is not None:
            terms.append(f"merged:>={since.astimezone(UTC):%Y-%m-%d}")
        if author is not None:
            terms.append(f"author:{author}")
        if assignee is not None:
            terms.append(f"assignee:{assignee}")
        params: dict[str, Any] | None = {"q": " ".join(terms), "per_page": PER_PAGE}
        url: str | None = f"{API_URL}/search/issues"
        pulls: list[Pull] = []
        total = 0
        while url is not None and len(pulls) < SEARCH_LIMIT:
            response = await self._get(url, params, what="pull requests")
            found = _parse(_Search, _decoded(response, "pull requests"), "pull requests")
            total = found.total_count
            pulls.extend(pull for pull in found.items if pull.pull_request.merged_at is not None)
            url, params = _next(response), None
        return pulls, total > SEARCH_LIMIT

    async def _json(self, path: str, *, what: str) -> object:
        return _decoded(await self._get(f"{API_URL}{path}", None, what=what), what)

    async def _pages(self, path: str, params: Mapping[str, Any], *, what: str) -> list[object]:
        """Every page of a list, following GitHub's `Link: <...>; rel="next"` header."""
        url: str | None = f"{API_URL}{path}"
        query: Mapping[str, Any] | None = params
        items: list[object] = []
        while url is not None:
            response = await self._get(url, query, what=what)
            body = _decoded(response, what)
            if not isinstance(body, list):
                raise GitHubApiError(f"GitHub sent an unexpected reply to the {what} request")
            items.extend(body)  # pyright: ignore[reportUnknownArgumentType]
            # The next page's address carries the query already, and httpx2 would replace it
            # with any params given.
            url, query = _next(response), None
        return items

    async def _get(
        self, url: str, params: Mapping[str, Any] | None, *, what: str, accept: str = _JSON
    ) -> httpx2.Response:
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self.token.get_secret_value()}",
            "X-GitHub-Api-Version": API_VERSION,
        }
        try:
            response = await self.http.get(
                url, params=params, headers=headers, timeout=TIMEOUT_S, follow_redirects=True
            )
        except httpx2.HTTPError as error:
            message = f"GitHub couldn't be reached for the {what} request ({type(error).__name__})"
            raise GitHubApiError(message) from None
        if response.status_code < 400:
            return response
        raise _error(response, what)


def _error(response: httpx2.Response, what: str) -> GitHubApiError:
    status = response.status_code
    headers = response.headers
    if status in (403, 429):
        if headers.get("x-ratelimit-remaining") == "0":
            reset = headers.get("x-ratelimit-reset", "")
            return GitHubRateLimited(
                datetime.fromtimestamp(int(reset), UTC) if reset.isdigit() else None
            )
        if (wait := headers.get("retry-after", "")).isdigit():
            # A secondary rate limit, for too many requests at once.
            return GitHubRateLimited(datetime.now(UTC) + timedelta(seconds=int(wait)))
    if status == 401:
        return GitHubTokenError("GitHub refused the token: it may have expired or been revoked")
    if status == 403:
        return GitHubTokenError(
            f"GitHub refused the {what} request: the token may not read this repository"
        )
    if status == 404:
        return GitHubNotFound(
            f"GitHub found no {what}: the repository doesn't exist, or the token can't read it"
        )
    return GitHubApiError(f"GitHub's {what} request failed with status {status}")


def _decoded(response: httpx2.Response, what: str) -> object:
    try:
        return response.json()
    except ValueError:
        raise GitHubApiError(f"GitHub sent an unexpected reply to the {what} request") from None


def _parse[T: BaseModel](model: type[T], body: object, what: str) -> T:
    try:
        return model.model_validate(body)
    except ValidationError:
        raise GitHubApiError(f"GitHub sent an unexpected reply to the {what} request") from None


def _next(response: httpx2.Response) -> str | None:
    link = response.links.get("next")
    return None if link is None else link.get("url")


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
