"""Whose work a commit or a pull request is (FR-GH-4)."""

from typing import Any

from app.github.api import Commit, Pull, Repository
from app.github.authors import (
    Owner,
    agent_for,
    co_authors,
    commit_authorship,
    owns,
    pull_authorship,
)

CASEY = {"id": 4242, "login": "casey-example", "type": "User"}
SAM = {"id": 5151, "login": "sam-example", "type": "User"}
CLAUDE = {"id": 81847, "login": "claude", "type": "User"}
COPILOT = {"id": 198982749, "login": "Copilot", "type": "Bot"}
DEPENDABOT = {"id": 49699333, "login": "dependabot[bot]", "type": "Bot"}
OWNER = Owner(4242, "casey-example", frozenset({"casey@example.com"}))


def commit(
    message: str = "Add a feature",
    *,
    account: dict[str, Any] | None = CASEY,
    email: str = "casey@example.com",
) -> Commit:
    signature = {"name": "Someone", "email": email, "date": "2026-01-01T00:00:00Z"}
    return Commit.model_validate(
        {
            "sha": "a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0",
            "html_url": "https://github.com/o/r/commit/a1b2c3d",
            "commit": {"message": message, "author": signature, "committer": signature},
            "author": account,
            "parents": [{"sha": "0" * 40}],
        }
    )


def pull(
    user: dict[str, Any] = CASEY, *, body: str = "", assignees: tuple[dict[str, Any], ...] = ()
) -> Pull:
    return Pull.model_validate(
        {
            "number": 7,
            "title": "A change",
            "body": body,
            "html_url": "https://github.com/o/r/pull/7",
            "user": user,
            "assignees": list(assignees),
            "pull_request": {"merged_at": "2026-01-02T00:00:00Z"},
        }
    )


def test_agents_are_known_by_account_and_email() -> None:
    claude = agent_for("claude")
    assert claude is not None
    assert claude.name == "Claude Code"
    assert agent_for(None, "noreply@anthropic.com") == claude
    assert agent_for(None, "209825114+claude[bot]@users.noreply.github.com") == claude
    copilot = agent_for("Copilot")
    assert copilot is not None
    assert copilot.name == "GitHub Copilot"
    assert agent_for("copilot-swe-agent[bot]") == copilot
    assert agent_for("sam-example", "sam@example.com") is None
    assert agent_for("dependabot[bot]") is None


def test_co_author_lines_are_read() -> None:
    message = (
        "Fix it\n\n"
        "Co-authored-by: Sam Example <sam@example.com>\n"
        "co-authored-by: Claude <noreply@anthropic.com>"
    )
    assert co_authors(message) == [
        ("Sam Example", "sam@example.com"),
        ("Claude", "noreply@anthropic.com"),
    ]


def test_the_owners_commit_is_theirs() -> None:
    made = commit_authorship(commit(), OWNER)
    assert (made.maker, made.agent, made.coauthored) == ("owner", None, False)
    assert made.counts(owned=True)
    assert made.counts(owned=False)


def test_a_commit_from_an_email_the_owner_uses_is_theirs() -> None:
    made = commit_authorship(commit(account=None, email="Casey@Example.com"), OWNER)
    assert made.maker == "owner"
    noreply = commit(account=None, email="4242+casey-example@users.noreply.github.com")
    assert commit_authorship(noreply, Owner(4242, "casey-example")).maker == "owner"


def test_learning_the_owners_emails_from_their_commits() -> None:
    owner = Owner(4242, "casey-example").learn([commit(email="casey@laptop.example")])
    assert owner.is_email("casey@laptop.example")
    assert (
        not Owner(4242, "casey-example").learn([commit(account=SAM)]).is_email("casey@example.com")
    )


def test_an_ai_co_author_helps_without_lowering_ownership() -> None:
    made = commit_authorship(
        commit("Cache results\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"), OWNER
    )
    assert (made.maker, made.agent, made.coauthored) == ("owner", "Claude Code", False)


def test_another_persons_co_authorship_is_marked() -> None:
    made = commit_authorship(
        commit("Pair on it\n\nCo-authored-by: Sam Example <sam@example.com>"), OWNER
    )
    assert (made.maker, made.coauthored) == ("owner", True)


def test_an_agents_commit_counts_in_the_owners_repository_only() -> None:
    made = commit_authorship(commit(account=CLAUDE, email="noreply@anthropic.com"), OWNER)
    assert (made.maker, made.agent) == ("agent", "Claude Code")
    assert made.counts(owned=True)
    assert not made.counts(owned=False)


def test_an_agents_commit_that_names_the_owner_counts_elsewhere() -> None:
    message = (
        "Speed it up\n\nCo-authored-by: casey-example <4242+casey-example@users.noreply.github.com>"
    )
    made = commit_authorship(
        commit(message, account=COPILOT, email="198982749+Copilot@users.noreply.github.com"),
        OWNER,
    )
    assert (made.maker, made.agent, made.names_owner) == ("agent", "GitHub Copilot", True)
    assert made.counts(owned=False)


def test_other_peoples_and_bots_commits_never_count() -> None:
    person = commit_authorship(commit(account=SAM, email="sam@example.com"), OWNER)
    bot = commit_authorship(
        commit(account=DEPENDABOT, email="49699333+dependabot[bot]@users.noreply.github.com"),
        OWNER,
    )
    unlinked_bot = commit_authorship(
        commit(account=None, email="41898282+github-actions[bot]@users.noreply.github.com"),
        OWNER,
    )
    assert (person.maker, bot.maker, unlinked_bot.maker) == ("person", "bot", "bot")
    for made in (person, bot, unlinked_bot):
        assert not made.counts(owned=True)
        assert not made.counts(owned=False)


def test_a_pull_request_with_the_claude_code_footer_was_made_with_it() -> None:
    body = "Adds a list.\n\n\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)"
    made = pull_authorship(pull(body=body), [], OWNER)
    assert (made.maker, made.agent, made.coauthored) == ("owner", "Claude Code", False)


def test_a_pull_request_takes_its_commits_agent_and_other_people() -> None:
    claude_commit = commit(account=CLAUDE, email="noreply@anthropic.com")
    made = pull_authorship(pull(), [claude_commit], OWNER)
    assert (made.agent, made.coauthored) == ("Claude Code", False)
    sams = commit(account=SAM, email="sam@example.com")
    assert pull_authorship(pull(), [commit(), sams], OWNER).coauthored


def test_an_agents_pull_request_assigned_to_the_owner_counts_elsewhere() -> None:
    made = pull_authorship(pull(COPILOT, assignees=(CASEY,)), [], OWNER)
    assert (made.maker, made.agent, made.names_owner) == ("agent", "GitHub Copilot", True)
    assert made.counts(owned=False)
    assert not pull_authorship(pull(COPILOT, assignees=(SAM,)), [], OWNER).counts(owned=False)


def test_another_persons_pull_request_never_counts() -> None:
    made = pull_authorship(pull(SAM, assignees=(CASEY,)), [commit()], OWNER)
    assert made.maker == "person"
    assert not made.counts(owned=True)
    assert not pull_authorship(pull(DEPENDABOT), [], OWNER).counts(owned=True)


def test_only_a_repository_the_owners_account_owns_and_didnt_fork_is_theirs() -> None:
    def repository(owner: dict[str, Any], *, fork: bool = False) -> Repository:
        return Repository.model_validate(
            {
                "id": 1,
                "full_name": f"{owner['login']}/r",
                "html_url": "https://github.com/x/r",
                "private": False,
                "fork": fork,
                "owner": owner,
                "default_branch": "main",
            }
        )

    assert owns(OWNER, repository(CASEY))
    assert not owns(OWNER, repository(CASEY, fork=True))
    assert not owns(OWNER, repository({"id": 9001, "login": "org", "type": "Organization"}))
