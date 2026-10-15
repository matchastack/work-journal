"""`wj db load-profile` and the commands' --db option, against the test database."""

import asyncio
import json
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Executable, delete, func, insert, select
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner, Result

from app import cli as cli_module
from app.cli import cli
from app.config import Settings, get_settings
from app.db.crypto import new_key, use_key_ring
from app.db.models import (
    ApplicationRow,
    Artifact,
    ChangeOpRow,
    ChangeSetRow,
    FactRow,
    JobPostingRow,
    ProfileVersionRow,
    Refresh,
    Setting,
    User,
)
from app.linkedin import WrittenSection
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import MemoryCallLog
from app.render.resume import RenderedResume
from app.schema.fact import Fact
from app.schema.jobs import Application, JobPosting
from app.schema.profile import Profile, Swap
from app.selection import Selection
from app.synthesis import PlannedBullet, SynthesisPlan
from app.tailoring.tailor import PlannedItem, TailoringPlan
from app.validate.claims import ClaimReply, JudgedClaim
from app.writing import WrittenBullet

FIXTURES = Path(__file__).parents[1] / "fixtures"
runner = CliRunner()


@pytest.fixture
def owner(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A GitHub username of the test's own, with the database settings; its user is deleted
    after the test."""
    login = f"cli-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", new_key("k1"))
    get_settings.cache_clear()
    use_key_ring(None)
    yield login
    query = delete(User).where(func.lower(User.github_login) == login)
    scalars(database_url, query)
    get_settings.cache_clear()
    use_key_ring(None)


def scalars(database_url: str, query: Executable) -> list[Any]:
    """Run one statement in its own transaction, and return its first column."""

    async def go() -> list[Any]:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(query)
                return list(result.scalars()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(go())


def run_rows(database_url: str, query: Executable) -> list[tuple[Any, ...]]:
    """Run one query in its own transaction, and return its rows."""

    async def go() -> list[tuple[Any, ...]]:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                return [tuple(row) for row in await connection.execute(query)]
        finally:
            await engine.dispose()

    return asyncio.run(go())


def imported(tmp_path: Path) -> Path:
    """The fictional master resume, imported into files: the profile's path."""
    result = invoke("import", "tex", str(FIXTURES / "master-resume.tex"), "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    return tmp_path / "profile.json"


def invoke(*args: str) -> Result:
    return runner.invoke(cli, list(args))


def stored(database_url: str, login: str) -> tuple[list[int], int]:
    """The user's version numbers, and how many facts they have."""
    user = select(User.id).where(User.github_login == login).scalar_subquery()
    versions = select(ProfileVersionRow.number).where(ProfileVersionRow.user_id == user)
    [facts] = scalars(
        database_url, select(func.count()).select_from(FactRow).where(FactRow.user_id == user)
    )
    return scalars(database_url, versions), facts


def test_load_profile_saves_version_1_and_the_facts(
    tmp_path: Path, database_url: str, owner: str
) -> None:
    profile = imported(tmp_path)
    result = invoke("db", "load-profile", str(profile), "--user", owner)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"Loaded {profile} as version 1 for {owner}, with ")
    versions, facts = stored(database_url, owner)
    assert versions == [1]
    assert facts > 0
    assert f"with {facts} facts from {tmp_path / 'facts.json'}." in result.stdout


def test_the_profile_is_loaded_once(tmp_path: Path, owner: str) -> None:
    profile = imported(tmp_path)
    assert invoke("db", "load-profile", str(profile), "--user", owner).exit_code == 0
    again = invoke("db", "load-profile", str(profile), "--user", owner)
    assert again.exit_code == 1
    assert "Nothing was loaded: the profile is already loaded" in again.stderr


def test_commands_read_the_newest_version_with_db(tmp_path: Path, owner: str) -> None:
    profile = imported(tmp_path)
    assert invoke("db", "load-profile", str(profile), "--user", owner).exit_code == 0
    from_file = invoke("lint", "--profile", str(profile))
    from_db = invoke("lint", "--db", "--user", owner)
    assert from_db.exit_code == from_file.exit_code
    assert from_db.stdout == from_file.stdout
    site = tmp_path / "site"
    built = invoke("portfolio", "build", "--out", str(site), "--db", "--user", owner)
    assert built.exit_code == 0, built.output
    assert (site / "index.html").is_file()


@pytest.mark.latex
def test_render_reads_the_database(tmp_path: Path, owner: str) -> None:
    profile = imported(tmp_path)
    assert invoke("db", "load-profile", str(profile), "--user", owner).exit_code == 0
    out = tmp_path / "master.pdf"
    result = invoke("render", "--db", "--user", owner, "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_bytes().startswith(b"%PDF")


def test_import_tex_can_save_straight_to_the_database(
    tmp_path: Path, database_url: str, owner: str
) -> None:
    """FR-IMP-5: with --db, the imported data never touches a local file."""
    tex = str(FIXTURES / "master-resume.tex")
    result = invoke("import", "tex", tex, "--out", str(tmp_path), "--db", "--user", owner)
    assert result.exit_code == 0, result.output
    versions, facts = stored(database_url, owner)
    assert versions == [1]
    assert f"Saved the profile as version 1 for {owner}, with {facts} facts." in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_an_unknown_user_is_reported(owner: str) -> None:
    result = invoke("lint", "--db", "--user", owner)
    assert result.exit_code == 1
    assert f"Which user? no user '{owner}' in the database." in result.stderr


def test_a_user_without_a_profile_is_told_how_to_load_one(
    tmp_path: Path, database_url: str, owner: str
) -> None:
    scalars(database_url, insert(User).values(github_login=owner))
    result = invoke("lint", "--db", "--user", owner)
    assert result.exit_code == 1
    assert "load one with `wj db load-profile`" in result.stderr


def test_write_reads_the_profile_and_facts_from_the_database(
    owner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = invoke("db", "load-profile", str(FIXTURES / "profile.json"), "--user", owner)
    assert loaded.exit_code == 0, loaded.output
    bullet = "Cut the nightly export job from 50 to 12 minutes by batching database writes."
    judged = JudgedClaim(
        claim="Cut the nightly export job", reason="As stated.", verdict="supported"
    )
    fake = FakeMessages(reply(WrittenBullet(text=bullet)), reply(ClaimReply(claims=(judged,))))
    llm = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)
    result = invoke(
        "write", "--db", "--user", owner, "--facts", "fact_nw_queue", "--edit", "nw_export"
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{bullet}\n"
    message = fake.requests[0]["messages"][0]["content"]
    assert '"id": "fact_nw_export"' in message, "the facts the bullet was written from"
    assert '"id": "fact_nw_queue"' in message


def test_write_saves_an_approved_phrasing_as_the_next_version(
    database_url: str, owner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = invoke("db", "load-profile", str(FIXTURES / "profile.json"), "--user", owner)
    assert loaded.exit_code == 0, loaded.output
    user = select(User.id).where(User.github_login == owner).scalar_subquery()
    avoid = {"avoid": ["spearheaded"]}
    scalars(database_url, insert(Setting).values(user_id=user, key="style_notes", value=avoid))
    text = "I cut the nightly export job from 50 to 12 minutes by batching database writes."
    judged = JudgedClaim(claim="I cut the export job", reason="As stated.", verdict="supported")
    fake = FakeMessages(reply(WrittenBullet(text=text)), reply(ClaimReply(claims=(judged,))))
    llm = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)
    options = ["--db", "--user", owner, "--style", "linkedin", "--edit", "nw_export"]
    result = runner.invoke(cli, ["write", *options, "--save-swap"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Saved version 2 of your profile, with the swap on nw_export." in result.stdout
    assert "<avoid>\n- spearheaded\n</avoid>" in fake.requests[0]["messages"][0]["content"]
    second = (ProfileVersionRow.user_id == user, ProfileVersionRow.number == 2)
    [author] = scalars(database_url, select(ProfileVersionRow.author).where(*second))
    assert author == "owner"
    [data] = scalars(database_url, select(ProfileVersionRow.profile).where(*second))
    bullets = [bullet for role in Profile.model_validate(data).work for bullet in role.highlights]
    [export] = [bullet for bullet in bullets if bullet.id == "nw_export"]
    assert export.swaps == (Swap(text=text, contexts=("LinkedIn",), origin="approved"),)


def test_tailor_logs_the_application_in_the_database(
    tmp_path: Path, database_url: str, owner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-TLR-8 and FR-FID-8: the posting, the PDF and the checks are kept with it."""
    loaded = invoke("db", "load-profile", str(FIXTURES / "profile.json"), "--user", owner)
    assert loaded.exit_code == 0, loaded.output
    plan = TailoringPlan(role_type="backend", work=(PlannedItem(id="northwind"),))
    llm = LLMClient(
        Settings(llm_model_heavy="heavy-model"), FakeMessages(reply(plan)), MemoryCallLog()
    )
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)

    def render(selection: Selection) -> RenderedResume:
        return RenderedResume(
            pdf=b"%PDF-1.5 tailored", pages=1, text="", cuts=(), warnings=(), selection=selection
        )

    monkeypatch.setattr(cli_module, "render_resume", render)
    posting = FIXTURES / "postings" / "backend.json"
    out = tmp_path / "resume.pdf"
    result = invoke("tailor", str(posting), "--db", "--user", owner, "--out", str(out))
    assert result.exit_code == 0, result.output
    user = select(User.id).where(User.github_login == owner).scalar_subquery()
    [report] = scalars(
        database_url, select(ApplicationRow.verifier_report).where(ApplicationRow.user_id == user)
    )
    assert report["coverage"]["covered"][0]["term"] == "Python"
    assert report["rules"]["findings"] == []
    [pdf] = scalars(database_url, select(Artifact.data).where(Artifact.user_id == user))
    assert bytes(pdf) == b"%PDF-1.5 tailored"
    [text] = scalars(database_url, select(JobPostingRow.text).where(JobPostingRow.user_id == user))
    assert text == posting.read_text(encoding="utf-8").strip()
    listed = invoke("applications", "--db", "--user", owner)
    assert listed.exit_code == 0, listed.output
    assert "Fabrikam Logistics: Senior Backend Engineer, Payments  (Software Engineer;" in (
        listed.stdout
    )


def journal_files(tmp_path: Path) -> Path:
    """The fictional profile, with its facts as imported plus one journal fact: the profile's
    path."""
    profile = tmp_path / "profile.json"
    profile.write_text((FIXTURES / "profile.json").read_text(encoding="utf-8"), encoding="utf-8")
    imported = json.loads((FIXTURES / "facts.json").read_text(encoding="utf-8"))
    alert = Fact(
        id="e1_1",
        statement="Added an alert that pages on-call when the nightly export fails.",
        date="2026-09",
        role_id="northwind",
    )
    facts = [{**fact, "origin": "import"} for fact in imported]
    facts.append(alert.model_dump(mode="json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts), encoding="utf-8")
    return profile


def use_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake model that plans one bullet from the journal fact, writes it and finds it
    supported."""
    plan = SynthesisPlan(bullets=(PlannedBullet(under="northwind", fact_ids=("e1_1",)),))
    text = "Added an alert that pages on-call when the nightly export job fails."
    judged = JudgedClaim(claim="Added an alert", reason="As stated.", verdict="supported")
    fake = FakeMessages(
        reply(plan), reply(WrittenBullet(text=text)), reply(ClaimReply(claims=(judged,)))
    )
    settings = Settings(llm_model_heavy="heavy-model", llm_model_standard="standard-model")
    llm = LLMClient(settings, fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)


def refreshes(database_url: str, login: str) -> list[tuple[Any, ...]]:
    """The user's refreshes, oldest first: status, facts covered, outputs and their version."""
    user = select(User.id).where(User.github_login == login).scalar_subquery()
    query = (
        select(Refresh.status, Refresh.fact_count, Refresh.outputs, Refresh.output_version)
        .where(Refresh.user_id == user)
        .order_by(Refresh.requested_at)
    )
    return run_rows(database_url, query)


def test_propose_and_apply_save_the_accepted_changes_as_the_next_version(
    tmp_path: Path, database_url: str, owner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With --db, `wj propose` is a refresh, as `/refresh` starts (T-038), and once its changes
    are decided, the outputs are due at the new version."""
    loaded = invoke("db", "load-profile", str(journal_files(tmp_path)), "--user", owner)
    assert loaded.exit_code == 0, loaded.output
    use_planner(monkeypatch)
    proposed = invoke("propose", "--db", "--user", owner)
    assert proposed.exit_code == 0, proposed.output
    assert proposed.stdout.startswith("Planning changes from 1 new fact.\n")
    assert proposed.stdout.endswith("Saved the proposal. Review it with `wj apply --db`.\n")
    waiting = invoke("propose", "--db", "--user", owner)
    assert waiting.exit_code == 1
    assert "A proposal still waits for review" in waiting.stderr
    assert refreshes(database_url, owner) == [("proposed", 1, None, None)]

    applied = invoke("apply", "--db", "--user", owner, "--yes")
    assert applied.exit_code == 0, applied.output
    assert applied.stdout.endswith(
        "Saved version 2. Render the master resume with `wj render --db`.\n"
    )
    user = select(User.id).where(User.github_login == owner).scalar_subquery()
    second = (ProfileVersionRow.user_id == user, ProfileVersionRow.number == 2)
    assert scalars(database_url, select(ProfileVersionRow.author).where(*second)) == ["ai"]
    [made_by] = scalars(database_url, select(ProfileVersionRow.change_set_id).where(*second))
    change_sets = select(ChangeSetRow.id, ChangeSetRow.status).where(ChangeSetRow.user_id == user)
    assert [(made_by, "applied")] == run_rows(database_url, change_sets)
    ops = select(ChangeOpRow.status).where(ChangeOpRow.user_id == user)
    assert scalars(database_url, ops) == ["accepted"]

    assert refreshes(database_url, owner) == [("proposed", 1, "pending", 2)]

    nothing_new = invoke("propose", "--db", "--user", owner)
    assert nothing_new.stdout == "There are no new facts to propose changes from.\n"
    assert refreshes(database_url, owner) == [
        ("proposed", 1, "superseded", 2),
        ("nothing_new", 0, "pending", 2),
    ]
    nothing_waiting = invoke("apply", "--db", "--user", owner)
    assert (
        nothing_waiting.stdout == "No proposal waits for review. Make one with `wj propose --db`.\n"
    )


def test_apply_saves_nothing_unless_you_confirm(
    tmp_path: Path, database_url: str, owner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        invoke("db", "load-profile", str(journal_files(tmp_path)), "--user", owner).exit_code == 0
    )
    use_planner(monkeypatch)
    assert invoke("propose", "--db", "--user", owner).exit_code == 0
    result = runner.invoke(cli, ["apply", "--db", "--user", owner], input="y\nn\n")
    assert result.exit_code == 0, result.output
    assert result.stdout.endswith("Nothing was saved.\n")
    versions, _ = stored(database_url, owner)
    assert versions == [1]
    user = select(User.id).where(User.github_login == owner).scalar_subquery()
    assert scalars(
        database_url, select(ChangeSetRow.status).where(ChangeSetRow.user_id == user)
    ) == ["proposed"]


def test_linkedin_uses_the_database_and_remembers_what_was_marked_done(
    database_url: str, owner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = invoke("db", "load-profile", str(FIXTURES / "profile.json"), "--user", owner)
    assert loaded.exit_code == 0, loaded.output
    user = select(User.id).where(User.github_login == owner).scalar_subquery()
    notes = {"avoid": ["spearheaded"], "notes": []}
    scalars(database_url, insert(Setting).values(user_id=user, key="style_notes", value=notes))
    posting = JobPosting(title="Backend Engineer")
    application = Application(
        id="app_1",
        applied_on=date(2026, 9, 1),
        company="Example Corp",
        position="Backend Engineer",
        posting=posting,
        posting_text="A posting.",
        title_variant="Backend Engineer",
    )
    row = {"user_id": user, "id": "app_1", "applied_on": application.applied_on}
    scalars(
        database_url,
        insert(ApplicationRow).values(**row, data=application.model_dump(mode="json")),
    )
    judged = JudgedClaim(claim="Python", reason="As stated.", verdict="supported")
    texts = [
        "Software Engineer | Python",
        "I build Python services.",
        "I build services.",
        "I built tools.",
    ]
    replies = [
        m
        for t in texts
        for m in (reply(WrittenSection(text=t)), reply(ClaimReply(claims=(judged,))))
    ]
    fake = FakeMessages(*replies)
    llm = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    monkeypatch.setattr(cli_module, "_llm_client", lambda: llm)

    result = invoke("linkedin", "--db", "--user", owner)
    assert result.exit_code == 0, result.output
    assert "<avoid>\n- spearheaded\n</avoid>" in fake.requests[0]["messages"][0]["content"]
    assert 'your latest application (Example Corp, 2026-09-01) used "Backend Engineer"' in (
        result.stdout
    )
    assert result.stdout.endswith("run `wj linkedin done --db`.\n")
    done = invoke("linkedin", "done", "--db", "--user", owner)
    assert done.exit_code == 0, done.output
    assert done.stdout == "Marked 4 sections done.\n"
    keys = select(Setting.key).where(Setting.user_id == user).order_by(Setting.key)
    assert scalars(database_url, keys) == ["linkedin_done", "linkedin_pack", "style_notes"]

    again = invoke("linkedin", "--db", "--user", owner)
    assert again.exit_code == 0, again.output
    assert again.stdout == "Nothing changed since you last marked the pack done.\n"
