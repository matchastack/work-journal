"""Reading a repository's history from GitHub (FR-GH-3): the I/O that `digest` needs.

Before anything else, the token's account must be the owner's (FR-GH-1). Only what reached the
default branch is read. In a repository the owner's account owns that isn't a fork, that's
every pull request merged into it and every commit on it, since an agent's commits aren't
authored by the owner's account (FR-GH-4); its description, languages and README too.
Elsewhere, only what may be the owner's work: the pull requests they opened or were assigned,
and their own commits.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime

from app.github.api import Account, Commit, GitHubApi, GitHubTokenError, Pull, Repository
from app.github.authors import Owner, owns, pull_maker

PULL_READS_AT_ONCE = 4
"""How many pull requests' commits are read at the same time."""


@dataclass(frozen=True)
class ExpectedOwner:
    """Who the token must belong to: the owner's GitHub account, by number when the app knows
    it (they've signed in), otherwise by username (from the profile)."""

    github_id: int | None = None
    login: str | None = None


@dataclass(frozen=True)
class PullHistory:
    pull: Pull
    commits: tuple[Commit, ...]


@dataclass(frozen=True)
class History:
    viewer: Account
    repository: Repository
    pulls: tuple[PullHistory, ...]
    commits: tuple[Commit, ...]
    """The default branch's commits, newest first."""
    languages: tuple[str, ...] = ()
    readme: str | None = None
    with_about: bool = False
    """Whether the description, languages and README were read, to describe the project."""
    more_pulls: bool = False
    """Whether the search had more pull requests than it returns (C7)."""

    @property
    def owner(self) -> Owner:
        """The owner, also known by the emails their own commits use."""
        commits = [*self.commits, *(c for history in self.pulls for c in history.commits)]
        return Owner(self.viewer.id, self.viewer.login).learn(commits)

    @property
    def owned(self) -> bool:
        return owns(self.owner, self.repository)


def check_owner(viewer: Account, expected: ExpectedOwner) -> None:
    """Refuse a token that isn't the owner's, so no one else's history is read as theirs."""
    if expected.github_id is not None:
        if viewer.id == expected.github_id:
            return
    elif expected.login is not None:
        if viewer.login.lower() == expected.login.lower():
            return
    else:
        raise GitHubTokenError(
            "It isn't known whose history this is: add your GitHub username to the profile "
            "(basics.profiles), or sign in to the web app once"
        )
    raise GitHubTokenError("GITHUB_REPOS_TOKEN belongs to another GitHub account than yours")


async def read_history(
    api: GitHubApi,
    full_name: str,
    *,
    owner: ExpectedOwner,
    since: datetime | None = None,
    about: bool = True,
) -> History:
    """What GitHub holds of the repository since `since` (all of it when None). With `about`,
    an owned repository's description, languages and README are read too."""
    viewer = await api.viewer()
    check_owner(viewer, owner)
    repository = await api.repository(full_name)
    # GitHub answers with the name as it is now, after a rename.
    name = repository.full_name
    owned = owns(Owner(viewer.id, viewer.login), repository)
    # Only what reached the default branch, as for its commits: a pull request merged into a
    # feature or release branch may never ship.
    branch = repository.default_branch
    if owned:
        pulls, more = await api.merged_pulls(name, base=branch, since=since)
        commits = await api.commits(name, branch=branch, since=since)
    else:
        pulls, more = await api.merged_pulls(name, base=branch, since=since, author=viewer.login)
        assigned, more_assigned = await api.merged_pulls(
            name, base=branch, since=since, assignee=viewer.login
        )
        numbers = {pull.number for pull in pulls}
        pulls += [pull for pull in assigned if pull.number not in numbers]
        more = more or more_assigned
        commits = await api.commits(name, branch=branch, since=since, author=viewer.login)
    limit = asyncio.Semaphore(PULL_READS_AT_ONCE)

    async def with_commits(pull: Pull) -> PullHistory:
        # Another person's or a bot's pull request never counts, whoever made its commits, so
        # its commits aren't read: it saves a request for each of them.
        if pull_maker(pull, Owner(viewer.id, viewer.login)) not in ("owner", "agent"):
            return PullHistory(pull, ())
        async with limit:
            return PullHistory(pull, tuple(await api.pull_commits(name, pull.number)))

    histories = await asyncio.gather(*(with_commits(pull) for pull in pulls))
    languages: tuple[str, ...] = ()
    readme = None
    if owned and about:
        languages = await api.languages(name)
        readme = await api.readme(name)
    return History(
        viewer=viewer,
        repository=repository,
        pulls=tuple(histories),
        commits=tuple(commits),
        languages=languages,
        readme=readme,
        with_about=owned and about,
        more_pulls=more,
    )
