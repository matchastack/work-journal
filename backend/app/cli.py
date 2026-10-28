"""The `wj` command-line tool. Later tasks add commands for rendering and tailoring."""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import httpx2
import typer
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import get_settings
from app.db.crypto import (
    DecryptionError,
    EncryptionKeyError,
    key_ring,
    new_key,
    rotate_keys,
)
from app.db.engine import create_engine, session_factory
from app.db.models import Base, User
from app.db.profiles import (
    SavedChangeSet,
    Version,
    VersionError,
    close_change_set,
    decide_op,
    get_change_set,
    latest_version,
    list_change_sets,
    load_master_profile,
    save_version,
)
from app.db.settings import (
    get_linkedin_done,
    get_linkedin_pack,
    get_style_notes,
    save_linkedin_done,
    save_linkedin_pack,
)
from app.db.store import (
    list_applications,
    list_facts,
    save_application,
    save_facts,
    save_job_posting,
    store_artifact,
)
from app.db.users import UnknownUserError, only_user, user_for_login
from app.evals.dataset import FICTIONAL_DIR, SUITES, Dataset, DatasetError, load_dataset
from app.evals.report import evaluation_report
from app.evals.run import CaseResult, Evaluation, SuiteResult, run_suite
from app.extraction import extract_facts
from app.github.api import GitHubApi, GitHubApiError, check_repository_name
from app.github.digest import Digest, build_digest, describe_left_out
from app.github.extraction import RepoTarget, extract_repo_facts
from app.github.reader import ExpectedOwner, read_history
from app.importers.master_resume import MasterResumeError, import_master_resume
from app.jobs import run_worker
from app.linkedin import LinkedInDone, LinkedInPack, PackSection, mark_done, write_pack
from app.llm.client import LLMCallError, LLMClient, LLMConfigError
from app.llm.usage import CallLog, CallLogs, JsonlCallLog, MemoryCallLog
from app.main import create_app
from app.portfolio.build import Download, build_portfolio
from app.profile_ops import ProfileOpError
from app.profile_ops import apply as apply_operations
from app.refresh import (
    Blocked,
    Proposals,
    RefreshInputs,
    begin_proposals,
    change_set_decided,
    fail_refresh,
    record_proposals,
    start_refresh,
)
from app.render.compile import CompileError
from app.render.fit import FitError
from app.render.resume import RenderError, render_resume
from app.review import describe, needs, report_lines
from app.schema.changes import AddSwap, ChangeOp, ChangeSet, ChangeSetStatus, OpStatus, ReviewedOp
from app.schema.export import write_json_schemas
from app.schema.fact import Fact
from app.schema.jobs import Application, JobPosting
from app.schema.profile import Profile
from app.schema.verification import VerifierReport
from app.selection import MASTER_VARIANT, select
from app.synthesis import Synthesis, new_facts, synthesize
from app.tailoring.applications import LoggedApplication, ResumeChecks, record
from app.tailoring.ats import report_lines as ats_lines
from app.tailoring.coverage import report_lines as coverage_lines
from app.tailoring.posting import parse_posting
from app.tailoring.tailor import posting_spellings, tailor, use_phrasings
from app.telegram.api import BotApi, TelegramError
from app.telegram.journal import COMMANDS
from app.telegram.linking import deep_link, new_link_token
from app.telegram.polling import poll
from app.tick import TICK_BUDGET_S, extract_now, refresh_now, tick
from app.validate.lint import LintReport, check_sendable, format_report, lint_profile
from app.writing import Style, apply_swap, locate_bullet, swap_op, write_bullet

LOCAL_DIR = Path(__file__).resolve().parents[2] / "local"
"""The repository's git-ignored folder for personal data (`backend/app/cli.py` is two below)."""
LINKEDIN_PACK_FILE = LOCAL_DIR / "linkedin" / "pack.json"
LINKEDIN_DONE_FILE = LOCAL_DIR / "linkedin" / "done.json"

cli = typer.Typer(help="Work Journal command-line tool.", no_args_is_help=True)
schema_cli = typer.Typer(help="JSON Schemas for the core data models.", no_args_is_help=True)
posting_cli = typer.Typer(help="Job postings to tailor resumes to.", no_args_is_help=True)
keys_cli = typer.Typer(help="The keys that encrypt journal and fact text.", no_args_is_help=True)
portfolio_cli = typer.Typer(help="The portfolio page.", no_args_is_help=True)
import_cli = typer.Typer(help="Import existing resume data.", no_args_is_help=True)
db_cli = typer.Typer(help="Your data in the database (DATABASE_URL).", no_args_is_help=True)
telegram_cli = typer.Typer(help="The Telegram bot (TELEGRAM_BOT_TOKEN).", no_args_is_help=True)
github_cli = typer.Typer(
    help="Your GitHub repositories, for project facts (GITHUB_REPOS_TOKEN).", no_args_is_help=True
)
linkedin_cli = typer.Typer()
cli.add_typer(schema_cli, name="schema")
cli.add_typer(posting_cli, name="posting")
cli.add_typer(portfolio_cli, name="portfolio")
cli.add_typer(keys_cli, name="keys")
cli.add_typer(import_cli, name="import")
cli.add_typer(db_cli, name="db")
cli.add_typer(telegram_cli, name="telegram")
cli.add_typer(github_cli, name="github")
cli.add_typer(linkedin_cli, name="linkedin")

DbOption = Annotated[
    bool, typer.Option("--db", help="Use the database (DATABASE_URL) instead of local files.")
]
RepositoryArgument = Annotated[str, typer.Argument(help="The repository, as owner/name.")]
SinceOption = Annotated[
    str | None,
    typer.Option(help="Read from this month on, as YYYY-MM. Default: the whole history."),
]
GitHubProfileOption = Annotated[
    Path | None,
    typer.Option(
        "--profile",
        help="The profile that names your GitHub username, in basics.profiles. "
        "Default: local/profile.json.",
        dir_okay=False,
    ),
]
UserOption = Annotated[
    str | None,
    typer.Option(
        help="With --db: the owner's GitHub username. Not needed while the database has one user."
    ),
]
_FACTS = TypeAdapter(list[Fact])


def _llm_client() -> LLMClient:
    """The Claude client the commands use (tests replace it with a fake)."""
    return LLMClient.from_settings()


def _eval_client(log: CallLog) -> LLMClient:
    """The Claude client `wj eval` uses, which logs its calls to `log` and to the JSONL file
    (tests replace it with a fake)."""
    settings = get_settings()
    if settings.anthropic_api_key is None or settings.llm_model_standard is None:
        typer.echo("Set ANTHROPIC_API_KEY and LLM_MODEL_STANDARD to run the evaluation.", err=True)
        raise typer.Exit(1)
    return LLMClient.from_settings(settings, CallLogs(log, JsonlCallLog(settings.llm_call_log)))


def _telegram_http() -> httpx2.AsyncClient:
    """The HTTP client for Telegram's Bot API (tests replace it with a stand-in for Telegram)."""
    return httpx2.AsyncClient()


def _github_http() -> httpx2.AsyncClient:
    """The HTTP client for GitHub's REST API (tests replace it with a stand-in for GitHub)."""
    return httpx2.AsyncClient()


@cli.callback()
def main() -> None:
    """Work Journal command-line tool."""


@cli.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@cli.command()
def extract(
    note: Annotated[
        Path, typer.Argument(help="The journal note, as plain text.", exists=True, dir_okay=False)
    ],
    profile: Annotated[
        Path, typer.Option(help="The profile the facts link to.", exists=True, dir_okay=False)
    ] = LOCAL_DIR / "profile.json",
    written: Annotated[
        datetime | None,
        typer.Option(
            "--date", formats=["%Y-%m-%d"], help="The day the note was written. Default: today."
        ),
    ] = None,
) -> None:
    """Extract the facts in a journal note and print them as JSON.

    Calls the standard-tier model. A fact with a number the note doesn't give is left out.
    """
    try:
        extraction = extract_facts(
            note.read_text(encoding="utf-8"),
            Profile.model_validate_json(profile.read_text(encoding="utf-8")),
            _llm_client(),
            written=written.date() if written else date.today(),
        )
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The note couldn't be read: {error}", err=True)
        raise typer.Exit(1) from None
    facts = [fact.model_dump(mode="json", exclude_none=True) for fact in extraction.facts]
    typer.echo(json.dumps(facts, indent=2, ensure_ascii=False))
    for dropped in extraction.dropped:
        reasons = "; ".join(dropped.reasons)
        typer.echo(f'Left out "{dropped.statement}": {reasons}', err=True)
    for message in extraction.notes:
        typer.echo(f"Note: {message}", err=True)


class WritingStyle(StrEnum):
    RESUME = "resume"
    LINKEDIN = "linkedin"
    POSTING = "posting"


_STYLES: dict[WritingStyle, Style] = {
    WritingStyle.RESUME: "resume",
    WritingStyle.LINKEDIN: "linkedin",
    WritingStyle.POSTING: "posting",
}


@cli.command()
def write(
    facts: Annotated[
        str,
        typer.Option(
            help="The IDs of the facts to write from, separated by commas. With --edit, the "
            "facts the bullet was written from are added, so these are only the new ones."
        ),
    ] = "",
    under: Annotated[
        str | None,
        typer.Option(
            help="The ID of the role, education entry or project the bullet goes under. "
            "Default: the one the facts, or the bullet to edit, belong to."
        ),
    ] = None,
    edit: Annotated[
        str | None,
        typer.Option(
            help="The ID of a bullet to write a new version of. The facts it was written from "
            "are added."
        ),
    ] = None,
    style: Annotated[
        WritingStyle,
        typer.Option(
            help="resume: a resume bullet. linkedin: first-person text for LinkedIn. posting: a "
            "resume bullet in a job posting's words (needs --posting)."
        ),
    ] = WritingStyle.RESUME,
    posting: Annotated[
        Path | None,
        typer.Option(
            help="With --style posting: the posting, as JSON from `wj posting parse`.",
            exists=True,
            dir_okay=False,
        ),
    ] = None,
    swap: Annotated[
        int | None,
        typer.Option(
            help="With --edit: work the bullet's swap with this number (1 for its first) into "
            "the bullet, instead of writing it from facts.",
            min=1,
        ),
    ] = None,
    save_swap: Annotated[
        bool,
        typer.Option(
            "--save-swap",
            help="With --edit: offer to save the new phrasing as a swap on the bullet, so it can "
            "be used again.",
        ),
    ] = False,
    profile: Annotated[
        Path | None,
        typer.Option(help="The profile. Default: local/profile.json.", exists=True, dir_okay=False),
    ] = None,
    facts_file: Annotated[
        list[Path] | None,
        typer.Option(
            "--facts-file",
            help="A JSON list of facts, such as `wj extract` prints. Repeat for more. "
            "Default: local/facts.json.",
            exists=True,
            dir_okay=False,
        ),
    ] = None,
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Write facts as a resume bullet, LinkedIn text or a bullet in a posting's words, check it
    with the verifier, and print it.

    Calls the standard-tier model to write the text and to check its claims. Nothing is saved,
    unless you say yes to --save-swap. With --db, your words to avoid apply. Exits with status 1
    when the text is flagged: it failed the checks, even when written again.
    """
    if db and facts_file:
        raise typer.BadParameter("use either --facts-file or --db", param_hint="--facts-file")
    if (style == WritingStyle.POSTING) != (posting is not None):
        raise typer.BadParameter(
            "--style posting needs a posting, and only it takes one", param_hint="--posting"
        )
    if edit is None and (swap is not None or save_swap):
        hint = "--swap" if swap is not None else "--save-swap"
        raise typer.BadParameter("goes with --edit", param_hint=hint)
    if swap is not None and (save_swap or style == WritingStyle.LINKEDIN):
        raise typer.BadParameter(
            "a swap is worked into a resume bullet: use --style resume or posting, without "
            "--save-swap",
            param_hint="--swap",
        )
    parsed = _read_posting(posting) if posting else None
    avoid: Sequence[str] = ()
    if db:
        if profile is not None:
            raise typer.BadParameter("use either --profile or --db", param_hint="--profile")
        loaded, pool, avoid = asyncio.run(_writing_inputs(user))
    else:
        loaded = _read_profile(profile, db, user)
        pool = _read_facts(facts_file or [LOCAL_DIR / "facts.json"])
    by_id = {fact.id: fact for fact in pool}
    ids = list(dict.fromkeys(part.strip() for part in facts.split(",") if part.strip()))
    if unknown := [fact_id for fact_id in ids if fact_id not in by_id]:
        raise typer.BadParameter(f"there's no fact {unknown[0]!r}", param_hint="--facts")
    chosen = [by_id[fact_id] for fact_id in ids]
    bullet = None
    if edit is not None:
        try:
            parent, bullet = locate_bullet(loaded, edit)
        except ValueError as error:
            raise typer.BadParameter(str(error), param_hint="--edit") from None
        under = under or parent
        for source in bullet.sources:
            if source not in by_id:
                typer.echo(f"Note: {edit} cites fact {source}, which wasn't found.", err=True)
            elif source not in ids:
                chosen.insert(0, by_id[source])
    if not chosen:
        raise typer.BadParameter("name at least one fact", param_hint="--facts")
    entry_id = under or _item_of(chosen)
    client = _llm_client()
    try:
        if swap is not None and edit is not None and bullet is not None:
            if swap > len(bullet.swaps):
                raise typer.BadParameter(
                    f"{edit} has {_count(len(bullet.swaps), 'swap')}", param_hint="--swap"
                )
            report = apply_swap(
                chosen,
                loaded,
                client,
                bullet_id=edit,
                swap=bullet.swaps[swap - 1],
                posting=parsed,
                avoid=avoid,
            )
        else:
            report = write_bullet(
                chosen,
                loaded,
                client,
                entry_id=entry_id,
                edit=edit,
                style=_STYLES[style],
                posting=parsed,
                avoid=avoid,
            )
    except ValueError as error:
        hint = "--swap" if swap is not None else "--under"
        raise typer.BadParameter(str(error), param_hint=hint) from None
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The text couldn't be written: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(report.text)
    _echo_report(report)
    if save_swap and edit is not None:
        op = swap_op(edit, report, style=_STYLES[style], posting=parsed)
        _save_swap(op, passed=report.passed, path=profile, db=db, user=user)
    if not report.passed:
        raise typer.Exit(1)


def _read_posting(path: Path) -> JobPosting:
    try:
        return JobPosting.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError:
        raise typer.BadParameter(
            f"{path} isn't a parsed posting; make one with `wj posting parse`",
            param_hint="--posting",
        ) from None


def _save_swap(op: AddSwap, *, passed: bool, path: Path | None, db: bool, user: str | None) -> None:
    """Ask whether to save the phrasing as a swap on its bullet, and save it."""
    question = (
        f"Save this phrasing as a swap on {op.bullet_id}?"
        if passed
        else f"It was flagged. Save it as a swap on {op.bullet_id} anyway?"
    )
    if not typer.confirm(question, default=passed):
        typer.echo("Nothing was saved.")
        return
    _save_swaps([op], path=path, db=db, user=user)


def _save_swaps(ops: Sequence[AddSwap], *, path: Path | None, db: bool, user: str | None) -> None:
    """Save the swaps: with --db as the profile's next version, and otherwise to the profile's
    file."""
    bullets = ", ".join(op.bullet_id for op in ops)
    noun = "swap" if len(ops) == 1 else "swaps"
    if db:
        number = asyncio.run(_save_swap_version(user, ops))
        typer.echo(f"Saved version {number} of your profile, with the {noun} on {bullets}.")
        return
    path = path or LOCAL_DIR / "profile.json"
    updated = _with_ops(_read_profile(path, False, None), ops)
    path.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Saved the {noun} on {bullets} to {path}.")


async def _save_swap_version(login: str | None, ops: Sequence[AddSwap]) -> int:
    async with _database() as session:
        user_id = await _user(session, login)
        version = await _latest(session, user_id)
        updated = _with_ops(version.profile, ops)
        saved = await save_version(session, user_id, updated, author="owner")
    return saved.number


def _with_ops(profile: Profile, ops: Sequence[ChangeOp]) -> Profile:
    try:
        return apply_operations(profile, ops)
    except ProfileOpError as error:
        typer.echo(f"The change can't be applied, so nothing changed: {error}", err=True)
        raise typer.Exit(1) from None


def _item_of(facts: Sequence[Fact]) -> str:
    """The one role or project the facts belong to."""
    items = {fact.role_id or fact.project_id for fact in facts}
    if len(items) != 1 or None in items:
        raise typer.BadParameter(
            "the facts don't all belong to one role or project in the profile; say which item "
            "the bullet goes under",
            param_hint="--under",
        )
    return str(items.pop())


def _echo_report(report: VerifierReport) -> None:
    """What the verifier found, on standard error: the findings, then the verdict."""
    for line in report_lines(report):
        typer.echo(line, err=True)


ChangeSetOption = Annotated[
    Path, typer.Option("--change-set", help="Without --db: the change set's file.")
]
FactsFilesOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--facts-file",
        help="Without --db: a JSON list of facts, such as `wj extract` prints. Repeat for more. "
        "Default: local/facts.json.",
        exists=True,
        dir_okay=False,
    ),
]
ProfileOption = Annotated[
    Path | None,
    typer.Option(
        help="Without --db: the profile. Default: local/profile.json.", exists=True, dir_okay=False
    ),
]


@cli.command()
def propose(
    profile: ProfileOption = None,
    facts_file: FactsFilesOption = None,
    change_set: ChangeSetOption = LOCAL_DIR / "change-set.json",
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Propose changes to the profile from the journal facts it doesn't use yet.

    Calls the heavy-tier model to plan the changes, and the standard-tier model to write and
    check each bullet. Nothing changes until you review the proposal with `wj apply`. With --db,
    it's a refresh, as `/refresh` starts: it covers the facts added or edited since the last one.
    """
    if db and (profile or facts_file):
        raise typer.BadParameter("use either --db or files", param_hint="--profile, --facts-file")
    if db:
        _refresh(user)
        return
    loaded = _read_profile(profile, db, user)
    pool = _read_facts(facts_file or [LOCAL_DIR / "facts.json"])
    previous = _read_change_set(change_set) if change_set.is_file() else None
    if previous is not None and previous.status == "proposed":
        typer.echo(f"{change_set} still waits for review: run `wj apply` first.", err=True)
        raise typer.Exit(1)
    new = new_facts(loaded, pool, [previous] if previous else [])
    synthesis = _synthesize(loaded, new, pool, base_version=None)
    if synthesis.change_set is not None:
        change_set.parent.mkdir(parents=True, exist_ok=True)
        change_set.write_text(synthesis.change_set.model_dump_json(indent=2) + "\n", "utf-8")
        typer.echo(f"Wrote {change_set}. Review it with `wj apply`.")


@cli.command("apply")
def apply_changes(
    profile: ProfileOption = None,
    facts_file: FactsFilesOption = None,
    change_set: ChangeSetOption = LOCAL_DIR / "change-set.json",
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Accept every change that passed the checks and reject the flagged ones, "
            "without asking.",
        ),
    ] = False,
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Review proposed changes one by one, and apply the ones you accept.

    With --db, the accepted changes become the profile's next version; without it, they're
    written to the profile's file. A change the checks flagged is accepted only if you say so.
    Then `wj render` renders the updated master resume.
    """
    if db and (profile or facts_file):
        raise typer.BadParameter("use either --db or files", param_hint="--profile, --facts-file")
    if db:
        user_id, version, saved, pool = asyncio.run(_review_inputs(user))
        if saved is None:
            typer.echo("No proposal waits for review. Make one with `wj propose --db`.")
            return
        base = saved.change_set.base_version
        if base is not None and base != version.number:
            typer.echo(f"Note: it was proposed for version {base}; the newest is {version.number}.")
        decided, updated = _review(saved.change_set, version.profile, pool, yes=yes)
        target = f"version {version.number + 1} of your profile"
        if updated is not None and not yes and not typer.confirm(f"Save the changes as {target}?"):
            typer.echo("Nothing was saved.")
            return
        number = asyncio.run(_save_review(user_id, saved.id, decided, updated, version.number))
        if number is None:
            typer.echo("You rejected every change, so the profile stays as it was.")
        else:
            typer.echo(f"Saved version {number}. Render the master resume with `wj render --db`.")
        return
    if not change_set.is_file():
        typer.echo(f"There's no proposal at {change_set}; make one with `wj propose`.", err=True)
        raise typer.Exit(1)
    proposal = _read_change_set(change_set)
    if proposal.status != "proposed":
        typer.echo(f"The proposal in {change_set} was already {proposal.status}.", err=True)
        raise typer.Exit(1)
    path = profile or LOCAL_DIR / "profile.json"
    loaded = _read_profile(profile, db, user)
    default_facts = LOCAL_DIR / "facts.json"
    pool = _read_facts(facts_file or ([default_facts] if default_facts.is_file() else []))
    decided, updated = _review(proposal, loaded, pool, yes=yes)
    if updated is not None and not yes and not typer.confirm(f"Write the changes to {path}?"):
        typer.echo("Nothing was written.")
        return
    if updated is not None:
        path.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    change_set.write_text(decided.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if updated is None:
        typer.echo("You rejected every change, so the profile stays as it was.")
    else:
        typer.echo(f"Wrote {path}. Render the master resume with `wj render`.")


def _refresh(login: str | None) -> None:
    """A refresh, run here (`app/refresh.py`): propose changes from the facts since the last
    one, and save them for review. When there's nothing to propose, the outputs fall due."""
    inputs = asyncio.run(_start_refresh(login))
    refresh_id = inputs.refresh_id
    try:
        synthesis = Synthesis(change_set=None)
        if inputs.facts:
            synthesis = _synthesize(
                inputs.version.profile,
                inputs.facts,
                inputs.sources,
                base_version=inputs.version.number,
                avoid=inputs.avoid,
            )
    except BaseException:
        asyncio.run(_fail_refresh(refresh_id))
        raise
    proposals = asyncio.run(_record_refresh(inputs, synthesis))
    if not inputs.facts:
        typer.echo("There are no new facts to propose changes from.")
    elif proposals.changes:
        typer.echo("Saved the proposal. Review it with `wj apply --db`.")


def _synthesize(
    profile: Profile,
    new: Sequence[Fact],
    pool: Sequence[Fact],
    *,
    base_version: int | None,
    avoid: Sequence[str] = (),
) -> Synthesis:
    """Propose changes from the new facts, and print them."""
    if not new:
        typer.echo("There are no new facts to propose changes from.")
        raise typer.Exit(0)
    typer.echo(f"Planning changes from {_count(len(new), 'new fact')}.")
    try:
        synthesis = synthesize(
            profile, new, _llm_client(), sources=pool, base_version=base_version, avoid=avoid
        )
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The changes couldn't be proposed: {error}", err=True)
        raise typer.Exit(1) from None
    if synthesis.change_set is None:
        typer.echo("Nothing to propose.")
    else:
        _echo_change_set(synthesis.change_set, profile, pool)
    for note in synthesis.notes:
        typer.echo(f"Note: {note}")
    if synthesis.unused:
        unused = ", ".join(synthesis.unused)
        typer.echo(f"Not used this time: {unused}. They stay in your journal.")
    return synthesis


def _echo_change_set(change_set: ChangeSet, profile: Profile, facts: Sequence[Fact]) -> None:
    typer.echo(change_set.summary or "Proposed changes:")
    for number, reviewed in enumerate(change_set.ops, start=1):
        typer.echo("")
        _echo_op(number, reviewed, profile, facts)


def _echo_op(number: int, reviewed: ReviewedOp, profile: Profile, facts: Sequence[Fact]) -> None:
    """One operation: what it does, the facts it rests on, and what the verifier found."""
    first, *rest = describe(reviewed.op, profile)
    typer.echo(f"{number}. {first}")
    for line in rest:
        typer.echo(f"   {line}")
    statements = {fact.id: fact.statement for fact in facts}
    for fact_id in reviewed.op.fact_ids:
        statement = statements.get(fact_id)
        typer.echo(f"   From {fact_id}: {statement}" if statement else f"   From {fact_id}")
    if reviewed.op.rationale:
        typer.echo(f"   Note: {reviewed.op.rationale}")
    if reviewed.verifier_report is not None:
        for line in report_lines(VerifierReport.model_validate(reviewed.verifier_report)):
            typer.echo(f"   {line}")


def _review(
    proposal: ChangeSet, profile: Profile, facts: Sequence[Fact], *, yes: bool
) -> tuple[ChangeSet, Profile | None]:
    """Ask about each change, unless `yes`, and apply the accepted ones to `profile`. Returns the
    decided change set, and the new profile, or None when every change was rejected."""
    typer.echo(proposal.summary or "Proposed changes:")
    ops = [reviewed.op for reviewed in proposal.ops]
    decided: list[ReviewedOp] = []
    for number, reviewed in enumerate(proposal.ops, start=1):
        typer.echo("")
        _echo_op(number, reviewed, profile, facts)
        needed = needs(reviewed.op, ops)
        if needed is not None and needed < len(decided) and decided[needed].status == "rejected":
            typer.echo(f"   Rejected: it needs change {needed + 1}, which you rejected.")
            reason = f"It needs change {needed + 1}, which was rejected."
            decided.append(reviewed.model_copy(update={"status": "rejected", "reason": reason}))
            continue
        report = reviewed.verifier_report
        passed = report is None or VerifierReport.model_validate(report).passed
        reason = None
        if yes:
            accepted = passed
            typer.echo("   Accepted." if passed else "   Rejected: the checks flagged it.")
            if not passed:
                reason = "The checks flagged it."
        else:
            accepted = typer.confirm("   Accept this change?", default=passed)
            if not accepted:
                why = typer.prompt("   Why not? (optional)", default="", show_default=False)
                reason = why.strip() or None
        status: OpStatus = "accepted" if accepted else "rejected"
        decided.append(reviewed.model_copy(update={"status": status, "reason": reason}))
    accepted_ops = [reviewed.op for reviewed in decided if reviewed.status == "accepted"]
    outcome: ChangeSetStatus = "applied" if accepted_ops else "rejected"
    result = proposal.model_copy(update={"ops": tuple(decided), "status": outcome})
    typer.echo("")
    typer.echo(
        f"Accepted {len(accepted_ops)} of {_count(len(decided), 'change')}."
        if accepted_ops
        else "Rejected every change."
    )
    if not accepted_ops:
        return result, None
    try:
        return result, apply_operations(profile, accepted_ops)
    except ProfileOpError as error:
        typer.echo(f"The accepted changes can't be applied, so nothing changed: {error}", err=True)
        raise typer.Exit(1) from None


def _read_change_set(path: Path) -> ChangeSet:
    return ChangeSet.model_validate_json(path.read_text(encoding="utf-8"))


_BLOCKED = {
    "no_profile": "The database has no profile yet; load one with `wj db load-profile`.",
    "running": "A refresh is running already; its proposal will wait for `wj apply --db`.",
    "waiting": "A proposal still waits for review: run `wj apply --db` first.",
}


async def _start_refresh(login: str | None) -> RefreshInputs:
    """Start a refresh for the user, and read what it proposes changes from, at once, so that
    the bot's job leaves it to this command. Exits when one can't start."""
    async with _database() as session:
        started = await start_refresh(session, await _user(session, login))
        inputs = None
        if not isinstance(started, Blocked):
            inputs = await begin_proposals(session, started, now=datetime.now(UTC))
    if isinstance(started, Blocked):
        typer.echo(_BLOCKED[started.reason], err=True)
        raise typer.Exit(1)
    assert inputs is not None, "a refresh just started is running, with a profile"
    return inputs


async def _record_refresh(inputs: RefreshInputs, synthesis: Synthesis) -> Proposals:
    async with _database() as session:
        proposals = await record_proposals(session, inputs, synthesis, now=datetime.now(UTC))
    if proposals is None:
        typer.echo("The refresh stopped before its proposal was saved.", err=True)
        raise typer.Exit(1)
    return proposals


async def _fail_refresh(refresh_id: uuid.UUID) -> None:
    async with _database() as session:
        await fail_refresh(session, refresh_id, now=datetime.now(UTC))


async def _review_inputs(
    login: str | None,
) -> tuple[uuid.UUID, Version, SavedChangeSet | None, list[Fact]]:
    """The user, their newest profile version, the oldest proposal waiting for review, and their
    facts."""
    async with _database() as session:
        user_id = await _user(session, login)
        version = await _latest(session, user_id)
        waiting = await list_change_sets(session, user_id, status="proposed")
        facts = await list_facts(session, user_id) if waiting else []
    return user_id, version, waiting[0] if waiting else None, facts


async def _save_review(
    user_id: uuid.UUID,
    change_set_id: uuid.UUID,
    decided: ChangeSet,
    updated: Profile | None,
    reviewed_version: int,
) -> int | None:
    """Record the decisions, and save the new profile as the next version, unless the proposal
    or the profile changed during the review. Returns the new version's number, if any."""
    async with _database() as session:
        current = await get_change_set(session, user_id, change_set_id)
        latest = await latest_version(session, user_id)
        if (
            current.change_set.status != "proposed"
            or latest is None
            or (latest.number != reviewed_version)
        ):
            typer.echo(
                "The proposal or the profile changed during your review, so nothing was saved. "
                "Run `wj apply --db` again.",
                err=True,
            )
            raise typer.Exit(1)
        for position, reviewed in enumerate(decided.ops):
            await decide_op(
                session, user_id, change_set_id, position, reviewed.status, reason=reviewed.reason
            )
        saved = None
        if updated is not None:
            saved = await save_version(
                session, user_id, updated, author="ai", change_set_id=change_set_id
            )
        await close_change_set(session, user_id, change_set_id, decided.status)
        await change_set_decided(session, user_id, change_set_id, now=datetime.now(UTC))
    return None if saved is None else saved.number


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


class SuiteName(StrEnum):
    EXTRACTION = "extraction"
    WRITING = "writing"
    ADVERSARIAL = "adversarial"
    REPO = "repo"
    ALL = "all"


@cli.command("eval")
def evaluate(
    suite: Annotated[
        SuiteName,
        typer.Argument(
            help="extraction (notes to facts), writing (facts to bullets), adversarial (text "
            "the verifier must judge right), repo (repository history to facts) or all."
        ),
    ],
    private: Annotated[
        bool,
        typer.Option(
            "--private",
            help="Run your own cases in local/evals/ instead of the committed fictional ones.",
        ),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option(help="Also write the report to this file, such as evals/baseline.md."),
    ] = None,
) -> None:
    """Run an evaluation suite with the real models, and report its altered numbers, inflated
    claims, fact recall and cost.

    Run it before changing a prompt or the model routing, and give the numbers in the PR. Calls
    the standard-tier model. Exits with status 1 when the results don't meet the evaluation bar:
    0 altered numbers, 0 inflated claims and at least 90% of facts recovered.
    """
    folder = LOCAL_DIR / "evals" if private else FICTIONAL_DIR
    try:
        datasets = [
            dataset
            for name in SUITES
            if suite in (SuiteName.ALL, name)
            and (dataset := load_dataset(folder, name)) is not None
        ]
    except DatasetError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    if not datasets:
        typer.echo(
            f"{folder} has no cases for {suite}; backend/evals/README.md describes the format.",
            err=True,
        )
        raise typer.Exit(1)
    log = MemoryCallLog()
    client = _eval_client(log)
    suites: list[SuiteResult] = []
    try:
        for dataset in datasets:
            suites.append(run_suite(dataset, client, log, progress=_progress(dataset)))
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    evaluation = Evaluation(suites=tuple(suites))
    cases = "Your private cases (`local/evals/`)" if private else "The fictional cases"
    report = evaluation_report(evaluation, dataset=cases, on=date.today())
    typer.echo(report, nl=False)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        typer.echo(f"Wrote {out}", err=True)
    if not evaluation.meets_bar:
        raise typer.Exit(1)


def _progress(dataset: Dataset) -> Callable[[CaseResult], None]:
    """Prints each case's result on standard error as it finishes."""

    def show(case: CaseResult) -> None:
        result = "error" if case.error else "pass" if case.passed else "fail"
        typer.echo(f"{dataset.suite}: {case.case_id}: {result}", err=True)

    return show


@cli.command()
def worker(
    concurrency: Annotated[int, typer.Option(min=1, help="How many jobs to run at once.")] = 1,
) -> None:
    """Run the background worker: queued jobs and scheduled tasks, until Ctrl-C."""
    settings = get_settings()
    if settings.database_url is None:
        typer.echo("Set DATABASE_URL to the database that holds the jobs.", err=True)
        raise typer.Exit(1)
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run_worker(concurrency))


@cli.command("tick")
def tick_once(
    budget: Annotated[
        float, typer.Option(min=0, help="Seconds to start queued jobs for, as the app's tick does.")
    ] = TICK_BUDGET_S,
) -> None:
    """Run one tick of background work: due scheduled tasks, stalled jobs, then queued jobs."""
    settings = get_settings()
    if settings.database_url is None:
        typer.echo("Set DATABASE_URL to the database that holds the jobs.", err=True)
        raise typer.Exit(1)
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    retried = asyncio.run(tick(budget_s=budget))
    typer.echo(f"Tick done; {retried or 0} stalled job(s) retried.")


@cli.command()
def openapi(
    out: Annotated[
        Path | None, typer.Argument(help="File to write. Without one, prints the schema.")
    ] = None,
) -> None:
    """Write the API's OpenAPI schema, which `frontend/` turns into its API types."""
    schema = json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n"
    if out is None:
        typer.echo(schema, nl=False)
        return
    out.write_text(schema, encoding="utf-8")
    typer.echo(f"Wrote {out}")


@cli.command()
def lint(
    profile: Annotated[
        Path | None,
        typer.Option(
            help="The profile to check. Default: local/profile.json.", exists=True, dir_okay=False
        ),
    ] = None,
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Check the profile against the resume rules and for inconsistencies.

    Exits with status 1 when there are errors, which would block a sendable resume.
    """
    report = lint_profile(_read_profile(profile, db, user))
    typer.echo(format_report(report))
    if report.errors:
        raise typer.Exit(1)


@cli.command()
def render(
    profile: Annotated[
        Path | None,
        typer.Option(
            help="The profile to render. Default: local/profile.json.", exists=True, dir_okay=False
        ),
    ] = None,
    out: Annotated[
        Path,
        typer.Option(help="Where to write the PDF. Its LaTeX source goes beside it, as .tex."),
    ] = LOCAL_DIR / "resumes" / "master.pdf",
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Render the profile's full master document as a PDF, with the LaTeX source it was compiled
    from. One-page resumes are tailored to a posting instead (OQ-6)."""
    selection = select(_read_profile(profile, db, user), MASTER_VARIANT)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        rendered = render_resume(selection)
    except CompileError as error:
        log = out.with_suffix(".log")
        log.write_text(error.log, encoding="utf-8")
        typer.echo(f"{error}. The full log is in {log}.", err=True)
        raise typer.Exit(1) from None
    except (RenderError, FitError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    tex = out.with_suffix(".tex")
    out.write_bytes(rendered.pdf)
    tex.write_text(rendered.tex, encoding="utf-8")
    pages = f"{rendered.pages} page{'' if rendered.pages == 1 else 's'}"
    typer.echo(f"Wrote {out} ({pages}) and its source, {tex}.")
    for note in selection.notes:
        typer.echo(f"Note: {note}")
    for warning in rendered.warnings:
        typer.echo(f"Warning: {warning}", err=True)


@cli.command("tailor")
def tailor_resume(
    posting: Annotated[
        Path,
        typer.Argument(
            help="The job posting: its text as pasted, or JSON from `wj posting parse`.",
            exists=True,
            dir_okay=False,
        ),
    ],
    out: Annotated[
        Path | None,
        typer.Option(
            help="Where to write the PDF; its LaTeX source goes beside it, as .tex. Default: "
            "local/resumes/<company>-<title>.pdf."
        ),
    ] = None,
    tex_only: Annotated[
        bool,
        typer.Option("--tex-only", help="Write only the LaTeX source, not the PDF."),
    ] = False,
    pdf_only: Annotated[
        bool,
        typer.Option("--pdf-only", help="Write only the PDF, not its LaTeX source."),
    ] = False,
    confirm: Annotated[
        list[str] | None,
        typer.Option(
            "--confirm",
            help="A skill marked verify-before-shipping that you can defend for this resume. "
            "Repeat for more.",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Use every new phrasing that passed the checks, and save it as a swap, without "
            "asking.",
        ),
    ] = False,
    profile: ProfileOption = None,
    facts_file: FactsFilesOption = None,
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Tailor a one-page resume to a job posting from your profile, and write it as a PDF and
    its LaTeX source.

    Calls the light-tier model to parse a posting given as text, the heavy-tier model to choose
    what the resume shows, and the standard-tier model to work in swaps and write new phrasings,
    each checked by the verifier. You approve each new phrasing: approved ones are used and
    saved as swaps on their bullets. The resume is fitted to one page and checked against the
    resume rules. Needs TeX Live, even with --tex-only, since fitting the resume to one page
    compiles it. With --db, your words to avoid apply.

    A resume that breaks a rule is still written and logged, so the work isn't lost, but the
    errors are printed last and kept with the application, and the command exits with status 1:
    fix them before you send it.
    """
    if db and (profile or facts_file):
        raise typer.BadParameter("use either --db or files", param_hint="--profile, --facts-file")
    if tex_only and pdf_only:
        raise typer.BadParameter("use at most one of them", param_hint="--tex-only, --pdf-only")
    if out and out.suffix == ".tex" and not tex_only:
        raise typer.BadParameter(
            "the PDF and its .tex source would be the same file; name a .pdf, or add --tex-only",
            param_hint="--out",
        )
    client = _llm_client()
    parsed, posting_text = _posting_to_tailor(posting, client)
    avoid: Sequence[str] = ()
    if db:
        loaded, pool, avoid = asyncio.run(_writing_inputs(user))
    else:
        loaded = _read_profile(profile, db, user)
        default_facts = LOCAL_DIR / "facts.json"
        pool = _read_facts(facts_file or ([default_facts] if default_facts.is_file() else []))
    confirmed = confirm or []
    at = f" at {parsed.company}" if parsed.company else ""
    typer.echo(f"Tailoring your resume to {parsed.title}{at}.")
    try:
        tailored = tailor(loaded, parsed, client, facts=pool, confirmed=confirmed, avoid=avoid)
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The resume couldn't be tailored: {error}", err=True)
        raise typer.Exit(1) from None
    for swap in tailored.swaps:
        typer.echo(f"Worked a swap into {swap.bullet_id}: {swap.text}")
    for note in tailored.notes:
        typer.echo(f"Note: {note}")
    accepted: list[str] = []
    for proposal in tailored.proposals:
        typer.echo("")
        typer.echo(f"A new phrasing of {proposal.bullet_id}, in the posting's words:")
        typer.echo(f"   was: {proposal.original}")
        typer.echo(f"   now: {proposal.text}")
        if yes or typer.confirm("   Use it, and save it as a swap?", default=True):
            accepted.append(proposal.bullet_id)
    if accepted:
        ops = [
            swap_op(p.bullet_id, p.report, style="posting", posting=parsed)
            for p in tailored.proposals
            if p.bullet_id in accepted
        ]
        _save_swaps(ops, path=profile, db=db, user=user)
    selection = use_phrasings(tailored, accepted)
    report = check_sendable(
        selection, loaded, confirmed, posting_spellings=posting_spellings(parsed)
    )
    if report.ok:
        for finding in report.warnings:
            typer.echo(f"Warning: {finding.location}: {finding.message}", err=True)
    out = out or LOCAL_DIR / "resumes" / f"{_slug(parsed)}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        rendered = render_resume(selection)
    except CompileError as error:
        log = out.with_suffix(".log")
        log.write_text(error.log, encoding="utf-8")
        typer.echo(f"{error}. The full log is in {log}.", err=True)
        raise typer.Exit(1) from None
    except (RenderError, FitError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    pdf = None if tex_only else out
    tex = None if pdf_only else out.with_suffix(".tex")
    if pdf:
        pdf.write_bytes(rendered.pdf)
    if tex:
        tex.write_text(rendered.tex, encoding="utf-8")
    if pdf and tex:
        typer.echo(f"Wrote {pdf} (1 page) and its source, {tex}.")
    else:
        typer.echo(f"Wrote {pdf or tex} (1 page).")
    if rendered.cuts:
        cut = ", ".join(cut.id for cut in rendered.cuts)
        typer.echo(f"Cut to fit one page: {cut}.")
    for warning in rendered.warnings:
        typer.echo(f"Warning: {warning}", err=True)
    application, checks = record(
        tailored,
        rendered.selection,
        accepted=accepted,
        rules=report,
        posting_text=posting_text,
        pdf=rendered.pdf,
        text=rendered.text,
        applied_on=_today(),
        suffix=uuid.uuid4().hex[:6],
    )
    for line in coverage_lines(checks.coverage):
        typer.echo(line)
    if checks.ats is not None:
        for line in ats_lines(checks.ats):
            typer.echo(line)
    if db:
        asyncio.run(_log_application(user, application, checks, rendered.pdf))
    else:
        logged = LoggedApplication(
            application=application,
            checks=checks,
            pdf=str(pdf) if pdf else None,
            tex=str(tex) if tex else None,
        )
        path = LOCAL_DIR / "applications" / f"{application.id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(logged.model_dump_json(indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Logged it as application {application.id}; `wj applications` lists them.")
    if not report.ok:
        _echo_rule_errors(report, pdf or out.with_suffix(".tex"))
        raise typer.Exit(1)


def _echo_rule_errors(report: LintReport, out: Path) -> None:
    """The resume rules the written resume breaks, as the last thing `wj tailor` prints."""
    typer.echo("", err=True)
    typer.echo(f"{out} breaks the resume rules below; fix them before you send it.", err=True)
    typer.echo(format_report(report), err=True)
    if any(finding.rule == "R3" for finding in report.errors):
        typer.echo(
            "For a skill marked verify-before-shipping, run again with --confirm <skill> only if "
            "you can defend it in an interview; otherwise take it out of the bullets that name it.",
            err=True,
        )


@cli.command("applications")
def list_application_log(db: DbOption = False, user: UserOption = None) -> None:
    """List the applications `wj tailor` logged, newest first: the date, company, role, the title
    the resume gave your current role, and what it showed."""
    if db:
        applications = asyncio.run(_applications(user))
    elif user is not None:
        raise typer.BadParameter("goes with --db", param_hint="--user")
    else:
        applications = _local_applications()
    if not applications:
        typer.echo("No applications yet. `wj tailor <posting>` logs one with each resume.")
        return
    for application in applications:
        shown = _count(len(application.bullet_ids), "bullet")
        if application.swaps_used:
            shown += f", {_count(len(application.swaps_used), 'swap')}"
        typer.echo(
            f"{application.applied_on}  {application.company}: {application.position}  "
            f"({application.title_variant}; {shown})  {application.id}"
        )


def _today() -> date:
    """The date an application is logged on (tests replace it)."""
    return date.today()


async def _log_application(
    login: str | None, application: Application, checks: ResumeChecks, pdf: bytes
) -> None:
    """Store the application, with its posting, its PDF and its checks (FR-TLR-8, FR-FID-8)."""
    async with _database() as session:
        user_id = await _user(session, login)
        posting_id = await save_job_posting(
            session, user_id, application.posting_text, application.posting
        )
        await store_artifact(session, user_id, pdf, "application/pdf")
        await save_application(
            session,
            user_id,
            application,
            posting_id=posting_id,
            verifier_report=checks.model_dump(mode="json"),
        )


async def _applications(login: str | None) -> list[Application]:
    async with _database() as session:
        user_id = await _user(session, login)
        return await list_applications(session, user_id)


def _local_applications() -> list[Application]:
    """The applications logged in local/applications/, newest first."""
    folder = LOCAL_DIR / "applications"
    paths = sorted(folder.glob("*.json"), key=lambda path: path.stat().st_mtime)
    logged: list[Application] = []
    for path in paths:
        try:
            logged.append(
                LoggedApplication.model_validate_json(path.read_text(encoding="utf-8")).application
            )
        except ValidationError:
            typer.echo(f"Skipped {path}: it isn't a logged application.", err=True)
    return sorted(logged, key=lambda application: application.applied_on, reverse=True)


def _posting_to_tailor(path: Path, client: LLMClient) -> tuple[JobPosting, str]:
    """The posting in `path`, and the file's text: JSON as `wj posting parse` prints it, or text
    to parse."""
    text = path.read_text(encoding="utf-8")
    try:
        return JobPosting.model_validate_json(text), text
    except ValidationError:
        pass
    try:
        parsed = parse_posting(text, client)
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The posting couldn't be parsed: {error}", err=True)
        raise typer.Exit(1) from None
    if parsed.dropped:
        dropped = ", ".join(f'"{term}"' for term in parsed.dropped)
        typer.echo(f"Left out terms the posting doesn't contain: {dropped}", err=True)
    return parsed.posting, text


def _slug(posting: JobPosting) -> str:
    """A file name for a resume tailored to `posting`, e.g. `fabrikam-backend-engineer`."""
    words = " ".join(part for part in (posting.company, posting.title) if part)
    return re.sub(r"[^a-z0-9]+", "-", words.casefold()).strip("-") or "tailored"


@schema_cli.command("export")
def export_schemas(
    out_dir: Annotated[Path, typer.Argument(help="Directory to write the schema files to.")],
) -> None:
    """Write one JSON Schema file per core model (profile, fact, variant, ...)."""
    for path in write_json_schemas(out_dir):
        typer.echo(f"Wrote {path}")


@posting_cli.command("parse")
def parse_posting_file(
    source: Annotated[
        Path, typer.Argument(help="The posting, as plain text.", exists=True, dir_okay=False)
    ],
) -> None:
    """Parse a job posting into its title, skills, responsibilities and key terms, as JSON.

    Calls the light-tier model. Skills and terms keep the posting's exact spellings.
    """
    try:
        parsed = parse_posting(source.read_text(encoding="utf-8"), _llm_client())
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The posting couldn't be parsed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(parsed.posting.model_dump_json(indent=2, exclude_none=True))
    if parsed.dropped:
        dropped = ", ".join(f'"{term}"' for term in parsed.dropped)
        typer.echo(f"Left out terms the posting doesn't contain: {dropped}", err=True)


@portfolio_cli.command("build")
def build_portfolio_page(
    out: Annotated[Path, typer.Option(help="The folder to write the page to.")],
    profile: Annotated[
        Path | None,
        typer.Option(
            help="The profile to publish. Default: local/profile.json.", exists=True, dir_okay=False
        ),
    ] = None,
    site_url: Annotated[
        str | None, typer.Option(help="The page's public address, for link previews.")
    ] = None,
    resume: Annotated[
        list[str] | None,
        typer.Option(help='A resume to offer, as "Label=path/to/resume.pdf". Repeat for more.'),
    ] = None,
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Build the portfolio page as static HTML from the profile's public content."""
    downloads: list[Download] = []
    for entry in resume or []:
        label, separator, path = entry.partition("=")
        if not separator or not label.strip() or not Path(path).is_file():
            raise typer.BadParameter(
                f"{entry!r} isn't Label=path/to/resume.pdf, with an existing file",
                param_hint="--resume",
            )
        downloads.append(Download(label=label.strip(), source=Path(path)))
    loaded = _read_profile(profile, db, user)
    page = build_portfolio(
        loaded, out, year=date.today().year, site_url=site_url, downloads=downloads
    )
    typer.echo(f"Wrote {page}")


@linkedin_cli.callback(invoke_without_command=True)
def linkedin(
    ctx: typer.Context,
    profile: ProfileOption = None,
    everything: Annotated[
        bool,
        typer.Option("--all", help="Write every section again, and print them all."),
    ] = False,
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Write the LinkedIn pack from your profile, and print the sections that changed since you
    last marked it done.

    Calls the standard-tier model to write each section whose content changed, and to check its
    claims; the other sections are carried over. With --db, your style notes apply and a current
    role's title is checked against your latest application. Once you've pasted the sections
    into LinkedIn, run `wj linkedin done`. Exits with status 1 when a section is flagged.
    """
    if ctx.invoked_subcommand is not None:
        return
    if db:
        if profile is not None:
            raise typer.BadParameter("use either --profile or --db", param_hint="--profile")
        user_id, version, done, latest, avoid, preferences = asyncio.run(_linkedin_inputs(user))
        loaded, number = version.profile, version.number
    else:
        loaded, number = _read_profile(profile, db, user), None
        user_id, latest, avoid, preferences = None, None, (), ()
        done = _read_model(LINKEDIN_DONE_FILE, LinkedInDone)
    try:
        pack = write_pack(
            loaded,
            _llm_client(),
            done=done,
            latest_application=latest,
            avoid=avoid,
            preferences=preferences,
            everything=everything,
            profile_version=number,
        )
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The pack couldn't be written: {error}", err=True)
        raise typer.Exit(1) from None
    if user_id is not None:
        asyncio.run(_save_linkedin_pack(user_id, pack))
    else:
        LINKEDIN_PACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LINKEDIN_PACK_FILE.write_text(pack.model_dump_json(indent=2) + "\n", encoding="utf-8")
    shown = pack.sections if everything else pack.changed
    for section in shown:
        _echo_section(section)
    unchanged = len(pack.sections) - len(pack.changed)
    if not pack.changed:
        typer.echo("Nothing changed since you last marked the pack done.")
    elif unchanged and not everything:
        typer.echo(f"{_count(unchanged, 'section')} unchanged, not shown (--all shows them).")
    for label in pack.removed:
        typer.echo(f"Take this off LinkedIn, as your public profile no longer has it: {label}")
    if pack.changed:
        typer.echo(
            f"Once you've pasted them into LinkedIn, run `wj linkedin done{' --db' if db else ''}`."
        )
    if any(section.flagged for section in shown):
        raise typer.Exit(1)


@linkedin_cli.command("done")
def linkedin_done(db: DbOption = False, user: UserOption = None) -> None:
    """Mark the last pack done, once you've pasted it into LinkedIn.

    From then on, `wj linkedin` prints only the sections that change. A flagged section isn't
    marked done, so it's written again next time.
    """
    if db:
        marked = asyncio.run(_mark_linkedin_done(user))
        if marked is None:
            typer.echo("There's no pack to mark done; write one with `wj linkedin --db`.", err=True)
            raise typer.Exit(1)
        pack, flagged, newest = marked
        if pack.profile_version is not None and pack.profile_version != newest:
            typer.echo(
                f"Note: the pack was written from version {pack.profile_version} of your "
                f"profile; the newest is {newest}. `wj linkedin --db` writes it again."
            )
    else:
        if user is not None:
            raise typer.BadParameter("goes with --db", param_hint="--user")
        pack = _read_model(LINKEDIN_PACK_FILE, LinkedInPack)
        if pack is None:
            typer.echo("There's no pack to mark done; write one with `wj linkedin`.", err=True)
            raise typer.Exit(1)
        previous = _read_model(LINKEDIN_DONE_FILE, LinkedInDone)
        done, flagged = mark_done(pack, previous, now=datetime.now().astimezone())
        LINKEDIN_DONE_FILE.write_text(done.model_dump_json(indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Marked {_count(len(pack.sections) - len(flagged), 'section')} done.")
    if flagged:
        labels = ", ".join(section.label for section in flagged)
        typer.echo(f"Not marked, as the checks flagged them: {labels}. They're written again.")


def _echo_section(section: PackSection) -> None:
    """One section: a heading with its length, the text, and what the verifier found."""
    typer.echo(f"== {section.label} ({section.length:,}/{section.limit:,} characters) ==")
    typer.echo(section.text)
    for note in section.notes:
        typer.echo(f"Note: {note}")
    if section.report is None:
        typer.echo("Unchanged since you last marked the pack done.", err=True)
    else:
        _echo_report(section.report)
    typer.echo()


async def _linkedin_inputs(
    login: str | None,
) -> tuple[
    uuid.UUID, Version, LinkedInDone | None, Application | None, tuple[str, ...], tuple[str, ...]
]:
    """The user, their newest profile version, the pack last marked done, their latest
    application, and their words to avoid and style preferences."""
    async with _database() as session:
        user_id = await _user(session, login)
        version = await _latest(session, user_id)
        done = await get_linkedin_done(session, user_id)
        applications = await list_applications(session, user_id)
        notes = await get_style_notes(session, user_id)
    preferences = tuple(note.text for note in notes.notes)
    latest = applications[0] if applications else None
    return user_id, version, done, latest, notes.avoid, preferences


async def _save_linkedin_pack(user_id: uuid.UUID, pack: LinkedInPack) -> None:
    async with _database() as session:
        await save_linkedin_pack(session, user_id, pack)


async def _mark_linkedin_done(
    login: str | None,
) -> tuple[LinkedInPack, tuple[PackSection, ...], int] | None:
    """Mark the user's last pack done. Returns it, its flagged sections, which stay unmarked,
    and the number of the newest profile version; None when there's no pack."""
    async with _database() as session:
        user_id = await _user(session, login)
        pack = await get_linkedin_pack(session, user_id)
        if pack is None:
            return None
        newest = await _latest(session, user_id)
        previous = await get_linkedin_done(session, user_id)
        done, flagged = mark_done(pack, previous, now=datetime.now().astimezone())
        await save_linkedin_done(session, user_id, done)
    return pack, flagged, newest.number


def _read_model[M: BaseModel](path: Path, model: type[M]) -> M | None:
    """The model saved in `path`, or None when there's no such file."""
    if not path.is_file():
        return None
    return model.model_validate_json(path.read_text(encoding="utf-8"))


@keys_cli.command("new")
def new_encryption_key(
    key_id: Annotated[str, typer.Option("--id", help="A short name for the key, e.g. k2.")] = "k1",
) -> None:
    """Print a new key for DATA_ENCRYPTION_KEY. Store it only there."""
    try:
        typer.echo(new_key(key_id))
    except EncryptionKeyError as error:
        raise typer.BadParameter(str(error), param_hint="--id") from None


@keys_cli.command("rotate")
def rotate_encryption_keys() -> None:
    """Re-encrypt stored text with the first key in DATA_ENCRYPTION_KEY."""
    url = get_settings().database_url
    if url is None:
        typer.echo("Set DATABASE_URL to the database to rotate.", err=True)
        raise typer.Exit(1)
    try:
        current = key_ring().current_id
        counts = asyncio.run(_rotate(url.get_secret_value()))
    except (EncryptionKeyError, DecryptionError) as error:
        typer.echo(f"Nothing was changed: {error}", err=True)
        raise typer.Exit(1) from None
    for column, count in counts.items():
        typer.echo(f"{column}: re-encrypted {count} value{'' if count == 1 else 's'}")
    if not counts:
        typer.echo("There are no encrypted columns yet.")
    typer.echo(f"Everything is under key {current!r}. Older keys can leave DATA_ENCRYPTION_KEY.")


async def _rotate(url: str) -> dict[str, int]:
    engine = create_engine(url)
    try:
        async with session_factory(engine)() as session, session.begin():
            return await rotate_keys(session, Base.metadata)
    finally:
        await engine.dispose()


@import_cli.command("tex")
def import_tex(
    source: Annotated[
        Path,
        typer.Argument(help="The master resume (.tex).", exists=True, dir_okay=False),
    ],
    out_dir: Annotated[
        Path,
        typer.Option("--out", help="Folder for profile.json, facts.json and import-report.txt."),
    ] = LOCAL_DIR,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite the files of an earlier import.")
    ] = False,
    db: Annotated[
        bool,
        typer.Option(
            "--db", help="Save the profile and facts in the database (DATABASE_URL) instead."
        ),
    ] = False,
    user: UserOption = None,
) -> None:
    """Import a master resume written in LaTeX into files in the git-ignored `local/` folder,
    or with --db into the database.

    Nothing is sent to an LLM. The report lists what to check and what wasn't imported.
    """
    outputs = {name: out_dir / name for name in ("profile.json", "facts.json", "import-report.txt")}
    if not db and not force and (existing := [path for path in outputs.values() if path.exists()]):
        typer.echo(f"{existing[0]} already exists; use --force to overwrite it.", err=True)
        raise typer.Exit(1)
    try:
        result = import_master_resume(source.read_text(encoding="utf-8"))
    except MasterResumeError as error:
        typer.echo(f"Can't import {source}: {error}", err=True)
        raise typer.Exit(1) from error

    if db:
        typer.echo(result.report.render())
        login = asyncio.run(_load(result.profile, result.facts, user, create=user is not None))
        typer.echo(f"Saved the profile as version 1 for {login}, with {len(result.facts)} facts.")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs["profile.json"].write_text(
        result.profile.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    facts = [fact.model_dump(mode="json") for fact in result.facts]
    outputs["facts.json"].write_text(
        json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = result.report.render()
    outputs["import-report.txt"].write_text(report, encoding="utf-8")
    typer.echo(report)
    for path in outputs.values():
        typer.echo(f"Wrote {path}")


@db_cli.command("load-profile")
def load_profile(
    user: Annotated[
        str, typer.Option(help="Your GitHub username: the account that signs in to the app.")
    ],
    profile: Annotated[
        Path, typer.Argument(help="The imported profile.", exists=True, dir_okay=False)
    ] = LOCAL_DIR / "profile.json",
) -> None:
    """Load the profile `wj import tex` wrote, and the facts.json beside it, as version 1.

    This puts your resume into the database. The user is created if it doesn't exist yet, and
    your first GitHub sign-in claims it. After this, commands read the database with --db.
    """
    loaded = Profile.model_validate_json(profile.read_text(encoding="utf-8"))
    facts_file = profile.with_name("facts.json")
    facts = (
        _FACTS.validate_json(facts_file.read_text(encoding="utf-8")) if facts_file.is_file() else []
    )
    login = asyncio.run(_load(loaded, facts, user, create=True))
    found = f", with {len(facts)} facts from {facts_file}" if facts_file.is_file() else ""
    typer.echo(f"Loaded {profile} as version 1 for {login}{found}.")


async def _load(profile: Profile, facts: Sequence[Fact], login: str | None, *, create: bool) -> str:
    """Save the imported profile as version 1, and its facts. Returns the user's name."""
    try:
        async with _database() as session:
            user_id = await _user(session, login, create=create)
            await load_master_profile(session, user_id, profile)
            await save_facts(session, user_id, facts)
    except VersionError as error:
        typer.echo(f"Nothing was loaded: {error}.", err=True)
        raise typer.Exit(1) from None
    except EncryptionKeyError as error:
        typer.echo(f"Nothing was loaded: {error}.", err=True)
        raise typer.Exit(1) from None
    return login or "the database's user"


@telegram_cli.command("set-webhook")
def set_telegram_webhook(
    url: Annotated[
        str | None,
        typer.Option(help="Where Telegram sends updates. Default: APP_URL/telegram/webhook."),
    ] = None,
) -> None:
    """Have Telegram send the bot's updates to the webhook, with TELEGRAM_WEBHOOK_SECRET, and
    show the bot's commands when you type "/"."""
    settings = get_settings()
    secret = settings.telegram_webhook_secret
    if secret is None:
        typer.echo("Set TELEGRAM_WEBHOOK_SECRET to the same value the app has.", err=True)
        raise typer.Exit(1)
    target = url or f"{settings.app_url}/telegram/webhook"
    if not target.startswith("https://"):
        message = f"Telegram only sends updates to an https:// address, not {target}"
        raise typer.BadParameter(message, param_hint="--url")
    _run_telegram(_set_webhook(target, secret.get_secret_value()))
    typer.echo(f"Telegram now sends the bot's updates to {target}")


@telegram_cli.command("poll")
def poll_telegram() -> None:
    """Journal the bot's updates on this computer, without the webhook, until Ctrl-C.

    It turns the webhook off first, because Telegram offers updates one way at a time. Turn it
    back on afterwards with `wj telegram set-webhook`.
    """
    url = get_settings().database_url
    if url is None:
        typer.echo("Set DATABASE_URL to the database the journal goes into.", err=True)
        raise typer.Exit(1)
    typer.echo("Journaling the bot's updates here. Stop with Ctrl-C.")
    try:
        _run_telegram(_poll(url.get_secret_value()))
    except KeyboardInterrupt:
        typer.echo("Stopped. Run `wj telegram set-webhook` to turn the webhook back on.")


@telegram_cli.command("link")
def link_telegram(
    user: Annotated[
        str | None,
        typer.Option(help="Your GitHub username. Not needed while the database has one user."),
    ] = None,
) -> None:
    """Print a one-time link that links your Telegram chat to your journal, for 15 minutes."""
    if get_settings().database_url is None:
        typer.echo("Set DATABASE_URL to the database your journal is in.", err=True)
        raise typer.Exit(1)
    url = _run_telegram(_new_link(user))
    typer.echo("Open this link where you use Telegram, within 15 minutes:")
    typer.echo(url)


def _run_telegram[T](work: Coroutine[Any, Any, T]) -> T:
    try:
        return asyncio.run(work)
    except TelegramError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None


def _bot_api(http: httpx2.AsyncClient) -> BotApi:
    token = get_settings().telegram_bot_token
    if token is None:
        typer.echo("Set TELEGRAM_BOT_TOKEN to the bot's token from @BotFather.", err=True)
        raise typer.Exit(1)
    return BotApi(token, http)


async def _set_webhook(url: str, secret: str) -> None:
    async with _telegram_http() as http:
        api = _bot_api(http)
        await api.set_webhook(url, secret)
        await api.set_my_commands(COMMANDS)


async def _new_link(login: str | None) -> str:
    async with _telegram_http() as http:
        bot = await _bot_api(http).get_me()
    async with _database() as session:
        token, _ = await new_link_token(session, await _user(session, login))
    return deep_link(bot.username, token)


async def _poll(database_url: str) -> int:
    engine = create_engine(database_url)
    try:
        async with _telegram_http() as http:
            settings = get_settings()
            return await poll(
                _bot_api(http),
                session_factory(engine),
                entry_timeout=timedelta(minutes=settings.entry_timeout_minutes),
                app_url=settings.app_url,
                run_jobs=extract_now,
                run_refresh=refresh_now,
            )
    finally:
        await engine.dispose()


@github_cli.command("digest")
def github_digest(
    repository: RepositoryArgument,
    since: SinceOption = None,
    profile: GitHubProfileOption = None,
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Print exactly what the model would read from a repository's history (FR-GH-3).

    It calls no model and stores nothing. GITHUB_REPOS_TOKEN must be your own token.
    """
    name = _repository_name(repository)
    start = _month_start(since)
    if db and profile is not None:
        raise typer.BadParameter("use either --profile or --db", param_hint="--profile")
    # With --db, the user is enough: the digest doesn't need the profile.
    owner = _github_owner(None if db else _read_profile(profile, False, user), db, user)
    digest = _run_github(_read_digest(name, owner, start))
    _echo_digest(digest)


class OwnershipCap(StrEnum):
    OWNED = "owned"
    CONTRIBUTED = "contributed"
    ASSISTED = "assisted"


@github_cli.command("extract")
def github_extract(
    repository: RepositoryArgument,
    project: Annotated[
        str | None, typer.Option(help="The project in your profile the facts belong to, by ID.")
    ] = None,
    new_project: Annotated[
        str | None,
        typer.Option(help="Or the name of a new project, which a refresh may add to the profile."),
    ] = None,
    since: SinceOption = None,
    ownership: Annotated[
        OwnershipCap | None,
        typer.Option(
            help="The most the facts may claim of your part. Default: owned in your own "
            "repository, contributed elsewhere."
        ),
    ] = None,
    profile: GitHubProfileOption = None,
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Extract the facts in a repository's history and print them as JSON (FR-GH-6).

    Calls the standard-tier model once for each part of the digest. It stores nothing. A part
    the model declines is skipped, named at the end, and makes the exit status 1.
    """
    name = _repository_name(repository)
    start = _month_start(since)
    owned_profile = _read_profile(profile, db, user)
    try:
        target = RepoTarget(project, new_project, None if ownership is None else ownership.value)
        project_name = target.project_name(owned_profile)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--project") from None
    for item in owned_profile.projects:
        if item.id == project and item.status == "benched":
            typer.echo(f"Note: {project_name} is benched, so its bullets never render.", err=True)
    owner = _github_owner(owned_profile, db, user)
    digest = _run_github(_read_digest(name, owner, start))
    typer.echo(f"Reading {_count(len(digest.windows), 'part')} of {digest.repository}.", err=True)
    try:
        extraction = extract_repo_facts(digest, owned_profile, _llm_client(), target=target)
    except LLMConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    except LLMCallError as error:
        typer.echo(f"The history couldn't be read: {error}", err=True)
        raise typer.Exit(1) from None
    facts = [fact.model_dump(mode="json", exclude_none=True) for fact in extraction.facts]
    typer.echo(json.dumps(facts, indent=2, ensure_ascii=False))
    for dropped in extraction.dropped:
        typer.echo(f'Left out "{dropped.statement}": {"; ".join(dropped.reasons)}', err=True)
    for message in extraction.notes:
        typer.echo(f"Note: {message}", err=True)
    for failed in extraction.failed:
        part = f"Part {failed.index + 1} of {len(digest.windows)}"
        typer.echo(f"{part} couldn't be read, so its facts are missing: {failed.reason}", err=True)
    if extraction.failed:
        raise typer.Exit(1)


def _repository_name(repository: str) -> str:
    try:
        return check_repository_name(repository)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="REPOSITORY") from None


def _month_start(month: str | None) -> datetime | None:
    if month is None:
        return None
    try:
        return datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    except ValueError:
        raise typer.BadParameter("must be a month, as YYYY-MM", param_hint="--since") from None


def _github_owner(profile: Profile | None, db: bool, login: str | None) -> ExpectedOwner:
    """Whose history is read: with --db, the user's GitHub account; otherwise the GitHub
    username in the profile's basics.profiles."""
    if db or profile is None:
        return asyncio.run(_database_owner(login))
    username = _profile_github_username(profile)
    if username is None:
        typer.echo(
            "Add your GitHub username to the profile's basics.profiles, so the token can be "
            "checked as yours, or use --db.",
            err=True,
        )
        raise typer.Exit(1)
    return ExpectedOwner(login=username)


def _profile_github_username(profile: Profile) -> str | None:
    for social in profile.basics.profiles:
        if social.network.casefold() != "github":
            continue
        if social.username:
            return social.username
        if social.url and (match := re.search(r"github\.com/([A-Za-z0-9-]+)/?$", social.url)):
            return match[1]
    return None


async def _database_owner(login: str | None) -> ExpectedOwner:
    async with _database() as session:
        account = await session.get(User, await _user(session, login))
    assert account is not None, "_user returns a user that exists"
    return ExpectedOwner(github_id=account.github_id, login=account.github_login)


async def _read_digest(name: str, owner: ExpectedOwner, since: datetime | None) -> Digest:
    async with _github_http() as http:
        api = GitHubApi.from_settings(get_settings(), http)
        return build_digest(await read_history(api, name, owner=owner, since=since))


def _run_github[T](work: Coroutine[Any, Any, T]) -> T:
    try:
        return asyncio.run(work)
    except GitHubApiError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None


def _echo_digest(digest: Digest) -> None:
    whose = "your repository" if digest.owned else "someone else's repository"
    typer.echo(f"{digest.repository}: {whose}, {'private' if digest.private else 'public'}.")
    read = f"Read {_count(digest.pulls_read, 'merged pull request')} and "
    read += f"{_count(digest.commits_read, 'commit')}"
    kept = _count(len(digest.items), "item")
    typer.echo(f"{read}, and kept {kept} in {_count(len(digest.windows), 'part')}.")
    if left_out := describe_left_out(digest.left_out):
        typer.echo(f"Left out: {left_out}.")
    if digest.more_pulls:
        typer.echo("GitHub's search gave only the first 1,000 merged pull requests.")
    for index in range(len(digest.windows)):
        typer.echo("")
        typer.echo(digest.render_window(index))


def _read_profile(path: Path | None, db: bool, user: str | None) -> Profile:
    """The profile from `path` (by default local/profile.json), or with --db the newest version
    in the database."""
    if db:
        if path is not None:
            raise typer.BadParameter("use either --profile or --db", param_hint="--profile")
        return asyncio.run(_newest_profile(user))
    if user is not None:
        raise typer.BadParameter("goes with --db", param_hint="--user")
    path = path or LOCAL_DIR / "profile.json"
    if not path.is_file():
        raise typer.BadParameter(
            f"{path} does not exist; import your resume with `wj import tex`, or use --db",
            param_hint="--profile",
        )
    return Profile.model_validate_json(path.read_text(encoding="utf-8"))


async def _newest_profile(login: str | None) -> Profile:
    """The newest profile version in the database."""
    async with _database() as session:
        user_id = await _user(session, login)
        version = await _latest(session, user_id)
    return version.profile


async def _writing_inputs(login: str | None) -> tuple[Profile, list[Fact], tuple[str, ...]]:
    """The newest profile version in the database, every fact, and the words to avoid."""
    async with _database() as session:
        user_id = await _user(session, login)
        version = await _latest(session, user_id)
        stored = await list_facts(session, user_id)
        notes = await get_style_notes(session, user_id)
    return version.profile, stored, notes.avoid


async def _latest(session: AsyncSession, user_id: uuid.UUID) -> Version:
    """The user's newest profile version. Exits when they have none."""
    version = await latest_version(session, user_id)
    if version is None:
        typer.echo("The database has no profile yet; load one with `wj db load-profile`.", err=True)
        raise typer.Exit(1)
    return version


def _read_facts(paths: Sequence[Path]) -> list[Fact]:
    """The facts in JSON files, such as the import's facts.json or what `wj extract` prints."""
    facts: dict[str, Fact] = {}
    for path in paths:
        if not path.is_file():
            raise typer.BadParameter(
                f"{path} does not exist; import your resume with `wj import tex`, or use --db",
                param_hint="--facts-file",
            )
        for fact in _FACTS.validate_json(path.read_text(encoding="utf-8")):
            if fact.id in facts:
                raise typer.BadParameter(
                    f"two facts have the ID {fact.id!r}; give them different IDs",
                    param_hint="--facts-file",
                )
            facts[fact.id] = fact
    return list(facts.values())


async def _user(session: AsyncSession, login: str | None, *, create: bool = False) -> uuid.UUID:
    try:
        if login is None:
            return await only_user(session)
        return await user_for_login(session, login, create=create)
    except UnknownUserError as error:
        typer.echo(f"Which user? {error}.", err=True)
        raise typer.Exit(1) from None


@asynccontextmanager
async def _database() -> AsyncGenerator[AsyncSession]:
    """A session on DATABASE_URL, committed when the block ends without an error."""
    url = get_settings().database_url
    if url is None:
        typer.echo("Set DATABASE_URL to use the database.", err=True)
        raise typer.Exit(1)
    engine = create_engine(url.get_secret_value())
    try:
        async with session_factory(engine)() as session, session.begin():
            yield session
    finally:
        await engine.dispose()
