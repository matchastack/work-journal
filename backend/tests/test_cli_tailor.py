import json
import re
from datetime import date
from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage
from typer.testing import CliRunner, Result

from app import cli as cli_module
from app.cli import cli
from app.config import Settings
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import MemoryCallLog
from app.render.fit import Cut
from app.render.resume import RenderedResume
from app.schema.jobs import JobPosting
from app.schema.profile import Profile, Swap
from app.selection import Selection
from app.tailoring.applications import LoggedApplication
from app.tailoring.tailor import (
    PlannedBullet,
    PlannedItem,
    PlannedSkillLine,
    TailoringPlan,
)
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet

FIXTURES = Path(__file__).parent / "fixtures"
POSTINGS = FIXTURES / "postings"
ANSI_STYLE = re.compile(r"\x1b\[[0-9;]*m")
PLAN = TailoringPlan(
    role_type="backend",
    work=(PlannedItem(id="northwind", bullets=(PlannedBullet(id="nw_export", relevance="high"),)),),
    skills=(PlannedSkillLine(label="Languages", skills=("Python", "SQL")),),
)
REPHRASED = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
SUPPORTED = ClaimReply(
    claims=(
        JudgedClaim(
            claim="Cut the export job",
            reason="The fact says so.",
            verdict="supported",
            fact_ids=("fact_nw_export",),
        ),
    )
)
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr(cli_module, "LOCAL_DIR", tmp_path / "local")
    monkeypatch.setattr(cli_module, "_today", lambda: date(2026, 10, 15))


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> list[Selection]:
    """Stands in for LaTeX: records each selection rendered, and cuts the project to fit."""
    selections: list[Selection] = []

    def render(selection: Selection) -> RenderedResume:
        selections.append(selection)
        cut = Cut(kind="bullet", id="rb_app", text="cut")
        return RenderedResume(
            pdf=b"%PDF-1.5 fake",
            pages=1,
            text="",
            cuts=(cut,),
            warnings=(),
            selection=selection.model_copy(update={"projects": ()}),
        )

    monkeypatch.setattr(cli_module, "render_resume", render)
    return selections


def use_fake(monkeypatch: pytest.MonkeyPatch, *replies: BetaMessage) -> FakeMessages:
    fake = FakeMessages(*replies)
    settings = Settings(
        llm_model_heavy="heavy-model",
        llm_model_standard="standard-model",
        llm_model_light="light-model",
    )
    llm = LLMClient(settings, fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)
    return fake


def own_profile(tmp_path: Path, text: str | None = None) -> Path:
    """A copy of the fictional profile that a test may change, optionally with another text for
    the order-service bullet."""
    data = json.loads((FIXTURES / "profile.json").read_text(encoding="utf-8"))
    if text is not None:
        data["work"][0]["highlights"][0]["text"] = text
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def tailor(profile: Path, *options: str, answer: str = "") -> Result:
    files = ["--profile", str(profile), "--facts-file", str(FIXTURES / "facts.json")]
    return runner.invoke(cli, ["tailor", *options, *files], input=answer)


def test_tailor_writes_the_resume_for_a_parsed_posting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: list[Selection]
) -> None:
    fake = use_fake(monkeypatch, reply(PLAN))
    out = tmp_path / "resume.pdf"
    result = tailor(own_profile(tmp_path), str(POSTINGS / "backend.json"), "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"%PDF-1.5 fake"
    lines = result.stdout.splitlines()
    assert lines[:-1] == [
        "Tailoring your resume to Senior Backend Engineer, Payments at Fabrikam Logistics.",
        f"Wrote {out} (1 page).",
        "Cut to fit one page: rb_app.",
        "Covers 1 of the posting's 14 skills and key terms:",
        "   Python (must-have): skills[Languages]",
        "Gaps, which are never added:",
        "   must-have: Go, PostgreSQL, Kafka, CI/CD, Terraform",
        "   nice-to-have: payments, ledgers, Kubernetes, gRPC",
        "   key term: APIs, event-driven systems, infrastructure as code, observability",
    ], "PostgreSQL was only in the project cut to fit"
    assert re.fullmatch(
        r"Logged it as application 20261015-fabrikam-logistics-senior-backend-engineer-[0-9a-f]{6};"
        r" `wj applications` lists them\.",
        lines[-1],
    )
    assert [request["model"] for request in fake.requests] == ["heavy-model"]
    [selection] = rendered
    assert [role.id for role in selection.work] == ["northwind"]
    assert selection.max_pages == 1


def test_a_posting_as_text_is_parsed_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: list[Selection]
) -> None:
    parsed = JobPosting.model_validate_json((POSTINGS / "backend.json").read_text("utf-8"))
    fake = use_fake(monkeypatch, reply(parsed), reply(PLAN))
    result = tailor(own_profile(tmp_path), str(POSTINGS / "backend.txt"), "--out", "r.pdf")
    assert result.exit_code == 0, result.output
    assert [request["model"] for request in fake.requests] == ["light-model", "heavy-model"]
    assert len(rendered) == 1


def test_the_default_file_is_named_after_the_posting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: list[Selection]
) -> None:
    use_fake(monkeypatch, reply(PLAN))
    result = tailor(own_profile(tmp_path), str(POSTINGS / "backend.json"))
    assert result.exit_code == 0, result.output
    out = tmp_path / "local" / "resumes" / "fabrikam-logistics-senior-backend-engineer-payments.pdf"
    assert out.is_file()


def rephrasing(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = PLAN.model_copy(
        update={
            "work": (
                PlannedItem(
                    id="northwind", bullets=(PlannedBullet(id="nw_export", rephrase=True),)
                ),
            )
        }
    )
    use_fake(monkeypatch, reply(plan), reply(WrittenBullet(text=REPHRASED)), reply(SUPPORTED))


def swaps_of(profile: Path, bullet_id: str) -> tuple[Swap, ...]:
    loaded = Profile.model_validate_json(profile.read_text(encoding="utf-8"))
    return next(b for b in loaded.all_bullets() if b.id == bullet_id).swaps


def test_an_approved_phrasing_is_used_and_saved_as_a_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: list[Selection]
) -> None:
    rephrasing(monkeypatch)
    profile = own_profile(tmp_path)
    result = tailor(profile, str(POSTINGS / "backend.json"), "--out", "r.pdf", answer="y\n")
    assert result.exit_code == 0, result.output
    assert "A new phrasing of nw_export, in the posting's words:" in result.stdout
    assert f"   now: {REPHRASED}" in result.stdout
    assert f"Saved the swap on nw_export to {profile}." in result.stdout
    assert rendered[0].work[0].bullets[0].text == REPHRASED
    context = "Senior Backend Engineer, Payments at Fabrikam Logistics"
    assert swaps_of(profile, "nw_export") == (
        Swap(text=REPHRASED, contexts=(context,), origin="approved"),
    )


def test_a_declined_phrasing_is_neither_used_nor_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: list[Selection]
) -> None:
    rephrasing(monkeypatch)
    profile = own_profile(tmp_path)
    before = profile.read_text(encoding="utf-8")
    result = tailor(profile, str(POSTINGS / "backend.json"), "--out", "r.pdf", answer="n\n")
    assert result.exit_code == 0, result.output
    assert rendered[0].work[0].bullets[0].text.endswith("by batching database writes.")
    assert profile.read_text(encoding="utf-8") == before


def test_yes_uses_every_phrasing_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: list[Selection]
) -> None:
    rephrasing(monkeypatch)
    profile = own_profile(tmp_path)
    result = tailor(profile, str(POSTINGS / "backend.json"), "--out", "r.pdf", "--yes")
    assert result.exit_code == 0, result.output
    assert "Use it" not in result.stdout
    assert rendered[0].work[0].bullets[0].text == REPHRASED


def test_an_unconfirmed_skill_blocks_the_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: list[Selection]
) -> None:
    plan = PLAN.model_copy(
        update={"work": (PlannedItem(id="northwind", bullets=(PlannedBullet(id="nw_orders"),)),)}
    )
    use_fake(monkeypatch, reply(plan))
    profile = own_profile(tmp_path, text="Built Kotlin services that process order events.")
    result = tailor(profile, str(POSTINGS / "backend.json"), "--out", "r.pdf")
    assert result.exit_code == 1
    error = " ".join(ANSI_STYLE.sub("", result.stderr).split())
    assert "Kotlin is marked verify-before-shipping and isn't confirmed for this resume" in error
    assert "--confirm <skill>" in error
    assert "so it wasn't rendered" in error
    assert rendered == []

    use_fake(monkeypatch, reply(plan))
    result = tailor(
        profile, str(POSTINGS / "backend.json"), "--out", "r.pdf", "--confirm", "Kotlin"
    )
    assert result.exit_code == 0, result.output
    assert len(rendered) == 1


def test_tailor_takes_either_the_database_or_files(tmp_path: Path) -> None:
    result = runner.invoke(
        cli, ["tailor", str(POSTINGS / "backend.json"), "--db", "--profile", str(tmp_path)]
    )
    assert result.exit_code == 2


def test_tailor_needs_the_model_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = LLMClient(Settings(), FakeMessages(), MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)
    result = tailor(own_profile(tmp_path), str(POSTINGS / "backend.json"))
    assert result.exit_code == 1
    assert "set LLM_MODEL_HEAVY" in result.stderr


@pytest.mark.latex
def test_tailor_renders_a_one_page_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake(monkeypatch, reply(PLAN))
    out = tmp_path / "resume.pdf"
    result = tailor(own_profile(tmp_path), str(POSTINGS / "backend.json"), "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"%PDF")
    assert f"Wrote {out} (1 page)." in result.stdout


def logged(tmp_path: Path) -> list[LoggedApplication]:
    folder = tmp_path / "local" / "applications"
    return [
        LoggedApplication.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(folder.glob("*.json"))
    ]


def test_each_resume_is_logged_as_an_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: list[Selection]
) -> None:
    rephrasing(monkeypatch)
    out = tmp_path / "resume.pdf"
    posting = POSTINGS / "backend.json"
    result = tailor(own_profile(tmp_path), str(posting), "--out", str(out), answer="y\n")
    assert result.exit_code == 0, result.output
    [entry] = logged(tmp_path)
    application = entry.application
    assert application.applied_on == date(2026, 10, 15)
    assert (application.company, application.position) == (
        "Fabrikam Logistics",
        "Senior Backend Engineer, Payments",
    )
    assert application.posting_text == posting.read_text(encoding="utf-8").strip()
    assert application.title_variant == "Software Engineer"
    assert application.bullet_ids == ("nw_export", "ss_minor"), "not the project cut to fit"
    assert [use.text for use in application.swaps_used] == [REPHRASED]
    assert entry.pdf == str(out)
    assert [report.text for report in entry.checks.texts] == [REPHRASED]
    assert [term.term for term in entry.checks.coverage.covered] == ["Python", "PostgreSQL"]


def test_applications_lists_the_log_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: list[Selection]
) -> None:
    result = runner.invoke(cli, ["applications"])
    assert result.exit_code == 0, result.output
    assert (
        result.stdout == "No applications yet. `wj tailor <posting>` logs one with each resume.\n"
    )
    use_fake(monkeypatch, reply(PLAN))
    assert (
        tailor(own_profile(tmp_path), str(POSTINGS / "backend.json"), "--out", "a.pdf").exit_code
        == 0
    )
    monkeypatch.setattr(cli_module, "_today", lambda: date(2026, 10, 16))
    use_fake(monkeypatch, reply(PLAN))
    assert (
        tailor(own_profile(tmp_path), str(POSTINGS / "data.json"), "--out", "b.pdf").exit_code == 0
    )
    result = runner.invoke(cli, ["applications"])
    assert result.exit_code == 0, result.output
    first, second = result.stdout.splitlines()
    assert first.startswith(
        "2026-10-16  Tailspin Analytics: Data Engineer  (Software Engineer; 2 bullets)  20261016-"
    )
    assert second.startswith("2026-10-15  Fabrikam Logistics: Senior Backend Engineer, Payments")


def test_a_file_that_isnt_an_application_is_skipped(tmp_path: Path) -> None:
    folder = tmp_path / "local" / "applications"
    folder.mkdir(parents=True)
    (folder / "notes.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(cli, ["applications"])
    assert result.exit_code == 0
    assert "isn't a logged application" in result.stderr
    assert result.stdout.startswith("No applications yet.")


def test_applications_takes_a_user_only_with_the_database() -> None:
    result = runner.invoke(cli, ["applications", "--user", "casey"])
    assert result.exit_code == 2
