"""A stand-in for GitHub's REST API, for tests: the repository calls `app/github/api.py` makes,
behind `httpx2.MockTransport`, as `app/auth/fake.py` stands in for GitHub's sign-in.

It serves fictional repositories from payloads shaped as GitHub's replies
(`tests/fixtures/github/`). It pages lists as GitHub does, with a `Link` header, and filters
commits and searches the way the client asks. The paths are written out again rather than
imported, so a wrong address in `api.py` fails the tests.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx2

TOKEN = "github_pat_test-token-not-real"
_REPOSITORY_PATH = re.compile(r"^/repos/([^/]+/[^/]+)(/.*)?$")
_PULL_COMMITS = re.compile(r"^/pulls/(\d+)/commits$")


class FakeGitHubApi:
    """GitHub's side of the calls `GitHubApi` makes, for one account (`viewer`).

    `add` serves a repository: a mapping with its `repository`, `languages`, `readme`, its
    default branch's `commits` (newest first) and its merged `pulls`, each with its `commits`
    and, when it was merged into another branch than the default one, that `base`.
    """

    def __init__(self, viewer: Mapping[str, Any]) -> None:
        self.transport = httpx2.MockTransport(self._handle)
        self.requests: list[httpx2.Request] = []
        self.viewer = dict(viewer)
        self.status: int | None = None
        """Answer every request with this status instead, e.g. 503 while GitHub is down."""
        self.rate_limited_until: datetime | None = None
        """Answer every request as GitHub does once the rate limit is used up."""
        self.page_size = 100
        """The most items a page holds, whatever the request asks for, to test paging."""
        self._repositories: dict[str, Mapping[str, Any]] = {}

    def add(self, repository: Mapping[str, Any]) -> None:
        name = str(repository["repository"]["full_name"]).lower()
        self._repositories[name] = repository

    def paths(self) -> list[str]:
        """The path of every request so far, in order."""
        return [request.url.path for request in self.requests]

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status is not None:
            return httpx2.Response(self.status, json={"message": "Unavailable"})
        if self.rate_limited_until is not None:
            reset = str(int(self.rate_limited_until.timestamp()))
            headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}
            return httpx2.Response(403, headers=headers, json={"message": "Rate limit exceeded"})
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx2.Response(401, json={"message": "Bad credentials"})
        path = request.url.path
        if path == "/user":
            return httpx2.Response(200, json=self.viewer)
        if path == "/search/issues":
            return self._search(request)
        match = _REPOSITORY_PATH.match(path)
        repository = None if match is None else self._repositories.get(match[1].lower())
        if match is None or repository is None:
            return _not_found()
        return self._repository(request, repository, match[2] or "")

    def _repository(
        self, request: httpx2.Request, repository: Mapping[str, Any], rest: str
    ) -> httpx2.Response:
        if rest == "":
            return httpx2.Response(200, json=repository["repository"])
        if rest == "/languages":
            return httpx2.Response(200, json=repository.get("languages", {}))
        if rest == "/readme":
            readme = repository.get("readme")
            if readme is None:
                return _not_found()
            if request.headers.get("Accept") != "application/vnd.github.raw+json":
                return httpx2.Response(200, json={"content": "base64 here"})
            return httpx2.Response(200, text=readme)
        if rest == "/commits":
            return self._page(request, self._commits(request, repository))
        if match := _PULL_COMMITS.match(rest):
            for pull in repository.get("pulls", []):
                if pull["pull"]["number"] == int(match[1]):
                    return self._page(request, pull["commits"])
        return _not_found()

    def _commits(
        self, request: httpx2.Request, repository: Mapping[str, Any]
    ) -> list[Mapping[str, Any]]:
        params = request.url.params
        if (
            params.get("sha", repository["repository"]["default_branch"])
            != (repository["repository"]["default_branch"])
        ):
            return []
        commits: list[Mapping[str, Any]] = list(repository.get("commits", []))
        if since := params.get("since"):
            start = _moment(since)
            commits = [c for c in commits if _moment(c["commit"]["committer"]["date"]) >= start]
        if author := params.get("author"):
            commits = [commit for commit in commits if _by(commit, author)]
        return commits

    def _search(self, request: httpx2.Request) -> httpx2.Response:
        terms = request.url.params.get("q", "").split()
        found: list[tuple[Mapping[str, Any], str]] = []
        for term in terms:
            if term.startswith("repo:"):
                repository = self._repositories.get(term.removeprefix("repo:").lower())
                if repository is not None:
                    default = repository["repository"]["default_branch"]
                    found = [(p["pull"], p.get("base", default)) for p in repository["pulls"]]
        for term in terms:
            found = [(pull, base) for pull, base in found if _matches(pull, base, term)]
        return self._page(request, [pull for pull, _ in found], search=True)

    def _page(
        self, request: httpx2.Request, items: Sequence[Mapping[str, Any]], *, search: bool = False
    ) -> httpx2.Response:
        params = request.url.params
        page = int(params.get("page", "1"))
        size = min(int(params.get("per_page", "30")), self.page_size)
        chunk = list(items[(page - 1) * size : page * size])
        headers: dict[str, str] = {}
        if page * size < len(items):
            following = request.url.copy_set_param("page", str(page + 1))
            headers["Link"] = f'<{following}>; rel="next"'
        body: object = chunk
        if search:
            body = {"total_count": len(items), "incomplete_results": False, "items": chunk}
        return httpx2.Response(200, json=body, headers=headers)


def _matches(pull: Mapping[str, Any], base: str, term: str) -> bool:
    key, _, value = term.partition(":")
    merged_at = pull["pull_request"].get("merged_at")
    match key:
        case "is" if value == "merged":
            return merged_at is not None
        case "base":
            return base == value
        case "merged":
            day = value.removeprefix(">=")
            return merged_at is not None and merged_at[:10] >= day
        case "author":
            return str(pull["user"]["login"]).lower() == value.lower()
        case "assignee":
            return any(str(a["login"]).lower() == value.lower() for a in pull["assignees"])
        case _:
            return True


def _by(commit: Mapping[str, Any], author: str) -> bool:
    """Whether GitHub counts `commit` as `author`'s: by account, or by email."""
    account: Mapping[str, Any] = commit.get("author") or {}
    email = str(commit["commit"]["author"]["email"])
    return str(account.get("login", "")).lower() == author.lower() or email == author.lower()


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _not_found() -> httpx2.Response:
    return httpx2.Response(404, json={"message": "Not Found"})
