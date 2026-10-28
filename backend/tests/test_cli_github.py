"""`wj github digest`, against a stand-in for GitHub (FR-GH-1, FR-GH-3)."""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from typer.testing import CliRunner

from app import cli as cli_module
from app.cli import cli
from app.config import Settings, get_settings
from app.github.fake import TOKEN, FakeGitHubApi

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = FIXTURES / "profile.json"
ANSI_STYLE = re.compile(r"\x1b\[[0-9;]*m")
"""In GitHub Actions, Typer colors the options named in usage errors."""
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("GITHUB_REPOS_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> FakeGitHubApi:
    payloads = FIXTURES / "github"
    fake = FakeGitHubApi(json.loads((payloads / "viewer.json").read_text(encoding="utf-8")))
    for name in ("recipe-box", "bird-calls", "tinyparse"):
        fake.add(json.loads((payloads / f"{name}.json").read_text(encoding="utf-8")))

    def client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=fake.transport)

    monkeypatch.setattr(cli_module, "_github_http", client)
    return fake


def digest(*args: str) -> list[str]:
    return ["github", "digest", *args, "--profile", str(PROFILE)]


def test_digest_prints_what_the_model_would_read(github: FakeGitHubApi) -> None:
    result = runner.invoke(cli, digest("casey-example/recipe-box"))
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[:3] == [
        "casey-example/recipe-box: your repository, public.",
        "Read 4 merged pull requests and 13 commits, and kept 6 items in 1 part.",
        "Left out: 1 merge, 1 revert, 1 change that was reverted, 1 empty message, 5 commits "
        "that are part of a pull request, 2 items by other people and 1 item by an automation bot.",
    ]
    assert "Repository: casey-example/recipe-box. It's the person's own repository." in lines
    assert "[pr2] Pull request, merged in August 2023." in lines
    assert "By you, with Claude Code." in lines


def test_digest_reads_a_repository_built_by_agents(github: FakeGitHubApi) -> None:
    result = runner.invoke(cli, digest("casey-example/bird-calls"))
    assert result.exit_code == 0, result.output
    assert "casey-example/bird-calls: your repository, private." in result.stdout
    assert "By Claude Code, for you." in result.stdout
    assert "By GitHub Copilot, for you." in result.stdout


def test_digest_reads_from_a_month(github: FakeGitHubApi) -> None:
    result = runner.invoke(cli, digest("casey-example/recipe-box", "--since", "2023-08"))
    assert result.exit_code == 0, result.output
    queries = [r.url.params["q"] for r in github.requests if r.url.path == "/search/issues"]
    assert queries == [
        "repo:casey-example/recipe-box is:pr is:merged base:main merged:>=2023-08-01"
    ]
    assert "Read 3 merged pull requests" in result.stdout


def test_digest_stores_nothing_and_calls_no_model(github: FakeGitHubApi, tmp_path: Path) -> None:
    result = runner.invoke(cli, digest("tinyparse-org/tinyparse"))
    assert result.exit_code == 0, result.output
    assert list(tmp_path.iterdir()) == []
    assert all(request.url.host == "api.github.com" for request in github.requests)
    assert all(request.method == "GET" for request in github.requests)


def test_digest_needs_the_token(github: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_REPOS_TOKEN")
    get_settings.cache_clear()
    result = runner.invoke(cli, digest("casey-example/recipe-box"))
    assert result.exit_code == 1
    assert "Set GITHUB_REPOS_TOKEN" in result.output
    assert github.requests == []


def test_digest_refuses_a_classic_token(
    github: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_REPOS_TOKEN", "ghp_classic-not-real")
    get_settings.cache_clear()
    result = runner.invoke(cli, digest("casey-example/recipe-box"))
    assert result.exit_code == 1
    assert "fine-grained" in result.output
    assert github.requests == []


def test_digest_refuses_another_accounts_token(github: FakeGitHubApi, tmp_path: Path) -> None:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    profile["basics"]["profiles"] = [{"network": "GitHub", "url": "https://github.com/sam-example"}]
    other = tmp_path / "other.json"
    other.write_text(json.dumps(profile), encoding="utf-8")
    result = runner.invoke(
        cli, ["github", "digest", "casey-example/recipe-box", "--profile", str(other)]
    )
    assert result.exit_code == 1
    assert "belongs to another GitHub account" in result.output
    assert github.paths() == ["/user"]


def test_digest_needs_to_know_whose_history_it_reads(github: FakeGitHubApi, tmp_path: Path) -> None:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    profile["basics"]["profiles"] = []
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(profile), encoding="utf-8")
    result = runner.invoke(
        cli, ["github", "digest", "casey-example/recipe-box", "--profile", str(bare)]
    )
    assert result.exit_code == 1
    assert "Add your GitHub username to the profile's basics.profiles" in result.output
    assert github.requests == []


def test_digest_says_when_github_cant_be_read(github: FakeGitHubApi) -> None:
    github.status = 503
    result = runner.invoke(cli, digest("casey-example/recipe-box"))
    assert result.exit_code == 1
    assert "failed with status 503" in result.output


def test_digest_says_when_the_repository_isnt_there(github: FakeGitHubApi) -> None:
    result = runner.invoke(cli, digest("casey-example/no-such-repo"))
    assert result.exit_code == 1
    assert "the repository doesn't exist, or the token can't read it" in result.output


@pytest.mark.parametrize(
    ("args", "words"),
    [
        (["recipe-box"], "isn't a repository name"),
        (["casey-example/recipe-box", "--since", "2023"], "must be a month, as YYYY-MM"),
        (["casey-example/recipe-box", "--user", "casey-example"], "goes with --db"),
    ],
)
def test_digest_checks_its_arguments(github: FakeGitHubApi, args: list[str], words: str) -> None:
    result = runner.invoke(cli, digest(*args))
    assert result.exit_code == 2
    assert words in ANSI_STYLE.sub("", result.output)
    assert github.requests == []
