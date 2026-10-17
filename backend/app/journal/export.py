"""The journal exported as Markdown and as JSON (FR-JRN-7).

Both hold every entry and every fact. The JSON holds everything the Journal page shows, with each
message's earlier text, and the facts from the imported resume, which belong to no entry. The
Markdown is for reading: the entries by month, each message as last edited and the facts saved
from it, then the imported facts, with times in the owner's time zone. Both come from one read
of the journal (`load_journal`), so 1,000 entries take well under the 10 s FR-JRN-7 allows.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FactRow
from app.journal.document import Journal, JournalEntryView, load_journal
from app.schema.fact import Fact
from app.validate.numbers import describe_metric

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
NOTES = {
    "open": "Still open: your next message joins it.",
    "waiting_for_role": "Waiting for you to choose its role in Telegram.",
    "extracting": "Its facts are on the way.",
    "failed": "The bot couldn't read it, so it has no facts.",
}
"""What the Markdown says about an entry that isn't processed."""


class JournalExport(Journal):
    exported_at: datetime
    imported_facts: tuple[Fact, ...]
    """Facts from the imported master resume (FR-IMP-4), which belong to no entry."""


async def export_journal(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime
) -> JournalExport:
    """The user's whole journal and every fact, as of `now`."""
    journal = await load_journal(session, user_id)
    imported = await session.scalars(
        select(FactRow.data)
        .where(FactRow.user_id == user_id, FactRow.origin == "import")
        .order_by(FactRow.id)
    )
    return JournalExport(
        entries=journal.entries,
        roles=journal.roles,
        projects=journal.projects,
        exported_at=now,
        imported_facts=tuple(Fact.model_validate_json(data) for data in imported),
    )


def to_markdown(export: JournalExport, zone: ZoneInfo) -> str:
    """The export as a Markdown document, with times in `zone`."""
    roles = {role.id: role.label for role in export.roles}
    facts = sum(len(entry.facts) for entry in export.entries)
    lines = [
        "# Work journal",
        "",
        f"Exported {when(export.exported_at, zone)} ({zone.key}): "
        f"{_count(len(export.entries), 'entry', 'entries')} "
        f"and {_count(facts, 'fact', 'facts')} from them.",
        "",
    ]
    month: tuple[int, int] | None = None
    for entry in export.entries:
        opened = entry.opened_at.astimezone(zone)
        if (opened.year, opened.month) != month:
            month = (opened.year, opened.month)
            lines += [f"## {MONTHS[opened.month - 1]} {opened.year}", ""]
        lines += [f"### {when(entry.opened_at, zone)}", ""]
        for paragraph in (*_about(entry, roles), *(message.text for message in entry.messages)):
            lines += [paragraph, ""]
        if entry.facts:
            lines += ["Facts:", "", *_facts(entry.facts), ""]
    if export.imported_facts:
        lines += ["## Facts from your imported resume", "", *_facts(export.imported_facts), ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def when(moment: datetime, zone: ZoneInfo) -> str:
    """E.g. `Sun 4 Oct 2026, 17:00`. Written by code, so it stays ASCII (FR-WRT-6)."""
    local = moment.astimezone(zone)
    month = MONTHS[local.month - 1][:3]
    return f"{DAYS[local.weekday()]} {local.day} {month} {local.year}, {local:%H:%M}"


def _about(entry: JournalEntryView, roles: Mapping[str, str]) -> list[str]:
    lines: list[str] = []
    if entry.question is not None:
        lines.append(f"Catch-up question: {entry.question}")
    if entry.role_id is not None:
        lines.append(f"Role: {roles.get(entry.role_id, entry.role_id)}")
    elif entry.new_role is not None:
        lines.append(f"New role: {entry.new_role}")
    if entry.status != "processed":
        lines.append(NOTES[entry.status])
    elif entry.triage == "other":
        lines.append("Not about work, so it has no facts.")
    return lines


def _facts(facts: Sequence[Fact]) -> list[str]:
    lines: list[str] = []
    for fact in facts:
        lines.append(f"- {fact.statement}")
        details = [fact.kind.replace("_", " ")]
        if fact.ownership:
            details.append(fact.ownership)
        if fact.date:
            details.append(fact.date)
        details += [describe_metric(metric).strip("'") for metric in fact.metrics]
        if fact.tools:
            details.append("tools: " + ", ".join(fact.tools))
        if fact.outcome:
            details.append(f"outcome: {fact.outcome}")
        lines.append(f"  ({'; '.join(details)})")
    return lines


def _count(number: int, one: str, many: str) -> str:
    return f"{number:,} {one if number == 1 else many}"
