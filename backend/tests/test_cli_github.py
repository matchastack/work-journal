"""`wj github digest` and `wj github extract`, against a stand-in for GitHub (FR-GH-1, FR-GH-3,
FR-GH-6), with the fake LLM client."""

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
from app.github.extraction import RepoExtractionReply
from app.github.fake import TOKEN, FakeGitHubApi
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, refusal, reply
from app.llm.usage import MemoryCallLog

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


# --- wj github extract ---

SHOPPING = "Added a weekly shopping list that groups ingredients by aisle."


def model(monkeypatch: pytest.MonkeyPatch, *replies: RepoExtractionReply) -> FakeMessages:
    fake = FakeMessages(*(reply(answer) for answer in replies))
    settings = Settings(llm_model_standard="standard-model")
    monkeypatch.setattr(
        cli_module, "_llm_client", lambda: LLMClient(settings, fake, MemoryCallLog())
    )
    return fake


def found(*facts: dict[str, object]) -> RepoExtractionReply:
    return RepoExtractionReply.model_validate({"facts": list(facts)})


def extract(*args: str) -> list[str]:
    return ["github", "extract", *args, "--profile", str(PROFILE)]


def test_extract_prints_the_facts_from_github(
    github: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = model(
        monkeypatch,
        found(
            {"items": ["pr2"], "statement": SHOPPING, "kind": "accomplishment"},
            {"items": ["pr1"], "statement": "Cut page loads to 300 ms.", "kind": "accomplishment"},
        ),
    )
    result = runner.invoke(cli, extract("casey-example/recipe-box", "--project", "recipe_box"))
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 1
    (fact,) = json.loads(result.stdout)
    assert fact["origin"] == "github"
    assert fact["projectId"] == "recipe_box"
    assert fact["ownership"] == "owned"
    assert fact["tools"] == ["Claude Code"]
    assert fact["date"] == "2023-08"
    assert fact["source"]["items"] == [
        {"kind": "pull", "ref": "4", "url": "https://github.com/casey-example/recipe-box/pull/4"}
    ]
    assert "Reading 1 part of casey-example/recipe-box." in result.stderr
    assert 'Left out "Cut page loads to 300 ms.": the items it cites don\'t give' in result.stderr


def test_extract_names_a_part_the_model_declined(
    github: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMessages(refusal("cyber"))
    settings = Settings(llm_model_standard="standard-model")
    monkeypatch.setattr(
        cli_module, "_llm_client", lambda: LLMClient(settings, fake, MemoryCallLog())
    )
    result = runner.invoke(cli, extract("casey-example/recipe-box", "--project", "recipe_box"))
    assert result.exit_code == 1
    assert json.loads(result.stdout) == []
    assert (
        "Part 1 of 1 couldn't be read, so its facts are missing: Claude declined the request "
        "(cyber)" in result.stderr
    )


def test_extract_files_facts_under_a_new_project_with_a_cap(
    github: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    model(
        monkeypatch,
        found(
            {"items": ["c1"], "statement": "Fixed the README example.", "kind": "accomplishment"}
        ),
    )
    args = ["tinyparse-org/tinyparse", "--new-project", "tinyparse", "--ownership", "assisted"]
    result = runner.invoke(cli, extract(*args))
    assert result.exit_code == 0, result.output
    (fact,) = json.loads(result.stdout)
    assert (fact["proposedItem"], fact["ownership"]) == ("tinyparse", "assisted")
    assert "projectId" not in fact


def test_extract_warns_about_a_benched_project(
    github: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    recipe = next(item for item in profile["projects"] if item["id"] == "recipe_box")
    recipe.update(status="benched", statusReason="Old.")
    benched = tmp_path / "benched.json"
    benched.write_text(json.dumps(profile), encoding="utf-8")
    model(monkeypatch, found())
    args = ["github", "extract", "casey-example/recipe-box", "--project", "recipe_box"]
    result = runner.invoke(cli, [*args, "--profile", str(benched)])
    assert result.exit_code == 0, result.output
    assert "Recipe Box is benched, so its bullets never render." in result.stderr
    assert json.loads(result.stdout) == []


@pytest.mark.parametrize(
    ("args", "words"),
    [
        ([], "name either a project"),
        (["--project", "recipe_box", "--new-project", "Box"], "either a project"),
        (["--project", "nope"], "no project 'nope'"),
        (["--project", "recipe_box", "--ownership", "led"], "Invalid value"),
    ],
)
def test_extract_needs_one_project(
    github: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch, args: list[str], words: str
) -> None:
    fake = model(monkeypatch)
    result = runner.invoke(cli, extract("casey-example/recipe-box", *args))
    assert result.exit_code == 2
    assert words in ANSI_STYLE.sub("", result.output)
    assert github.requests == []
    assert fake.requests == []
