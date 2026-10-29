"""Whose work a commit or a pull request is (FR-GH-4).

Two kinds of work count as the owner's: their own, and what an AI coding agent did for them, since
the owner thought of and directed it. An agent's work counts in a repository the owner's account
owns. Elsewhere it counts when it names the owner, as a co-author or as the pull request's
assignee, or when it's part of the owner's own pull request.

Other people's work never counts, and nor does automation, such as Dependabot's: it's routine. An
agent counts as one only when `AGENTS` lists it; one the list doesn't know counts as another
person until it's added (C7). The owner is known by their account, and by the emails their own
commits use, which `Owner.learn` gathers from the history read.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.github.api import Account, Commit, Pull, Repository

Maker = Literal["owner", "agent", "person", "bot"]

_NOREPLY = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$", re.IGNORECASE)
"""GitHub's private commit email, `<id>+<login>@users.noreply.github.com`."""
_CO_AUTHOR = re.compile(
    r"^\s*co-authored-by:\s*(.*?)\s*<([^>]*)>\s*$", re.IGNORECASE | re.MULTILINE
)


@dataclass(frozen=True)
class Agent:
    """An AI coding agent, as its commits and pull requests show it."""

    name: str
    """How facts and bullets name it, as a tool."""
    logins: frozenset[str]
    """Its GitHub accounts, in lowercase."""
    emails: frozenset[str] = frozenset()
    """The emails its commits and co-author lines use, in lowercase."""
    footer: re.Pattern[str] | None = None
    """A line it adds to the pull requests it writes from someone's account."""


AGENTS: tuple[Agent, ...] = (
    Agent(
        "Claude Code",
        logins=frozenset({"claude", "claude[bot]"}),
        emails=frozenset({"noreply@anthropic.com"}),
        footer=re.compile(r"generated with \[?claude code\]?", re.IGNORECASE),
    ),
    Agent("GitHub Copilot", logins=frozenset({"copilot", "copilot-swe-agent[bot]"})),
)


@dataclass(frozen=True)
class Owner:
    """The person whose work is read: the token's account."""

    id: int
    login: str
    emails: frozenset[str] = field(default_factory=frozenset[str])

    def learn(self, commits: Iterable[Commit]) -> "Owner":
        """The owner, also known by the emails of the commits their account made."""
        mine = {
            commit.commit.author.email.lower()
            for commit in commits
            if commit.author is not None and commit.author.id == self.id
        }
        return Owner(self.id, self.login, self.emails | mine)

    def is_account(self, account: Account | None) -> bool:
        return account is not None and account.id == self.id

    def is_email(self, email: str) -> bool:
        email = email.lower()
        noreply = _NOREPLY.match(email)
        return email in self.emails or (noreply is not None and noreply[1] == self.login.lower())


@dataclass(frozen=True)
class Authorship:
    maker: Maker
    agent: str | None = None
    """The AI coding agent that made the work, or helped the owner make it."""
    coauthored: bool = False
    """Whether another person had a part in it, so the owner's part is at most contributed."""
    names_owner: bool = False
    """Whether it names the owner as a co-author or assignee, though someone else made it."""

    def counts(self, *, owned: bool) -> bool:
        """Whether it counts as the owner's work, in a repository their account owns or
        elsewhere."""
        if self.maker == "owner":
            return True
        return self.maker == "agent" and (owned or self.names_owner)


def owns(owner: Owner, repository: Repository) -> bool:
    """Whether the owner's account owns the repository, and it isn't a fork of another."""
    return repository.owner.id == owner.id and not repository.fork


def agent_for(login: str | None, email: str | None = None) -> Agent | None:
    """The agent behind an account or a commit email, if it's one `AGENTS` lists."""
    if email is not None:
        email = email.lower()
        if noreply := _NOREPLY.match(email):
            login = login or noreply[1]
    for agent in AGENTS:
        if login is not None and login.lower() in agent.logins:
            return agent
        if email is not None and email in agent.emails:
            return agent
    return None


def is_bot(account: Account | None, email: str = "") -> bool:
    if account is not None:
        return account.type == "Bot" or account.login.lower().endswith("[bot]")
    noreply = _NOREPLY.match(email)
    return noreply is not None and noreply[1].endswith("[bot]")


def co_authors(message: str) -> list[tuple[str, str]]:
    """The `Co-authored-by: Name <email>` lines of a commit message, as (name, email) pairs."""
    return [(match[1], match[2]) for match in _CO_AUTHOR.finditer(message)]


def commit_authorship(commit: Commit, owner: Owner) -> Authorship:
    email = commit.commit.author.email
    agent = agent_for(None if commit.author is None else commit.author.login, email)
    maker: Maker
    if agent is not None:
        maker = "agent"
    elif owner.is_account(commit.author) or owner.is_email(email):
        maker = "owner"
    elif is_bot(commit.author, email):
        maker = "bot"
    else:
        maker = "person"
    helper = None if agent is None else agent.name
    coauthored = names_owner = False
    for _, co_email in co_authors(commit.message):
        if (co_agent := agent_for(None, co_email)) is not None:
            helper = helper or co_agent.name
        elif owner.is_email(co_email):
            names_owner = True
        elif not is_bot(None, co_email):
            coauthored = True
    return Authorship(maker, helper, coauthored, names_owner)


def pull_maker(pull: Pull, owner: Owner) -> Maker:
    """Who opened a pull request. Only one the owner or an agent opened can count as theirs."""
    if agent_for(pull.user.login) is not None:
        return "agent"
    if owner.is_account(pull.user):
        return "owner"
    return "bot" if is_bot(pull.user) else "person"


def pull_authorship(pull: Pull, commits: Sequence[Commit], owner: Owner) -> Authorship:
    """Whose work a pull request is, from who opened it and who made its commits. Another
    person's commit in it makes it co-authored; an agent's commit, or the agent's footer in its
    description, names the agent that helped."""
    maker = pull_maker(pull, owner)
    agent = agent_for(pull.user.login)
    helper = None if agent is None else agent.name
    if helper is None:
        helper = next(
            (a.name for a in AGENTS if a.footer and a.footer.search(pull.body or "")), None
        )
    coauthored = False
    names_owner = any(owner.is_account(assignee) for assignee in pull.assignees)
    for commit in commits:
        made = commit_authorship(commit, owner)
        helper = helper or made.agent
        coauthored = coauthored or made.coauthored or made.maker == "person"
        names_owner = names_owner or made.names_owner or made.maker == "owner"
    return Authorship(maker, helper, coauthored, names_owner)
