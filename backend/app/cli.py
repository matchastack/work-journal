"""The `wj` command-line tool. Later tasks add commands for rendering and tailoring."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Coroutine, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import httpx2
import typer
from pydantic import TypeAdapter
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
from app.db.models import Base
from app.db.profiles import VersionError, latest_version, load_master_profile
from app.db.store import save_facts
from app.db.users import UnknownUserError, only_user, user_for_login
from app.extraction import extract_facts
from app.importers.master_resume import MasterResumeError, import_master_resume
from app.jobs import run_worker
from app.llm.client import LLMCallError, LLMClient, LLMConfigError
from app.main import create_app
from app.portfolio.build import Download, build_portfolio
from app.render.compile import CompileError
from app.render.fit import FitError
from app.render.resume import RenderError, render_resume
from app.schema.export import write_json_schemas
from app.schema.fact import Fact
from app.schema.profile import Profile
from app.selection import MASTER_VARIANT, select
from app.tailoring.posting import parse_posting
from app.telegram.api import BotApi, TelegramError
from app.telegram.linking import deep_link, new_link_token
from app.telegram.polling import poll
from app.tick import TICK_BUDGET_S, extract_now, tick
from app.validate.lint import format_report, lint_profile

LOCAL_DIR = Path(__file__).resolve().parents[2] / "local"
"""The repository's git-ignored folder for personal data (`backend/app/cli.py` is two below)."""

cli = typer.Typer(help="Work Journal command-line tool.", no_args_is_help=True)
schema_cli = typer.Typer(help="JSON Schemas for the core data models.", no_args_is_help=True)
posting_cli = typer.Typer(help="Job postings to tailor resumes to.", no_args_is_help=True)
keys_cli = typer.Typer(help="The keys that encrypt journal and fact text.", no_args_is_help=True)
portfolio_cli = typer.Typer(help="The portfolio page.", no_args_is_help=True)
import_cli = typer.Typer(help="Import existing resume data.", no_args_is_help=True)
db_cli = typer.Typer(help="Your data in the database (DATABASE_URL).", no_args_is_help=True)
telegram_cli = typer.Typer(help="The Telegram bot (TELEGRAM_BOT_TOKEN).", no_args_is_help=True)
cli.add_typer(schema_cli, name="schema")
cli.add_typer(posting_cli, name="posting")
cli.add_typer(portfolio_cli, name="portfolio")
cli.add_typer(keys_cli, name="keys")
cli.add_typer(import_cli, name="import")
cli.add_typer(db_cli, name="db")
cli.add_typer(telegram_cli, name="telegram")

DbOption = Annotated[
    bool, typer.Option("--db", help="Use the database (DATABASE_URL) instead of local files.")
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


def _telegram_http() -> httpx2.AsyncClient:
    """The HTTP client for Telegram's Bot API (tests replace it with a stand-in for Telegram)."""
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
        typer.Option(help="Where to write the PDF."),
    ] = LOCAL_DIR / "resumes" / "master.pdf",
    db: DbOption = False,
    user: UserOption = None,
) -> None:
    """Render the profile's full master document as a PDF. One-page resumes are tailored to a
    posting instead (OQ-6)."""
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
    out.write_bytes(rendered.pdf)
    pages = f"{rendered.pages} page{'' if rendered.pages == 1 else 's'}"
    typer.echo(f"Wrote {out} ({pages}).")
    for note in selection.notes:
        typer.echo(f"Note: {note}")
    for warning in rendered.warnings:
        typer.echo(f"Warning: {warning}", err=True)


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
    """Have Telegram send the bot's updates to the webhook, with TELEGRAM_WEBHOOK_SECRET."""
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
        await _bot_api(http).set_webhook(url, secret)


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
            entry_timeout = timedelta(minutes=get_settings().entry_timeout_minutes)
            return await poll(
                _bot_api(http),
                session_factory(engine),
                entry_timeout=entry_timeout,
                after_close=extract_now,
            )
    finally:
        await engine.dispose()


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
    async with _database() as session:
        version = await latest_version(session, await _user(session, login))
    if version is None:
        typer.echo("The database has no profile yet; load one with `wj db load-profile`.", err=True)
        raise typer.Exit(1)
    return version.profile


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
