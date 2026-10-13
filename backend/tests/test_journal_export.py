"""The journal as Markdown (FR-JRN-7), without the database."""

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.journal.document import (
    JournalEntryView,
    JournalFact,
    JournalMessageView,
    MessageEdit,
    RoleOption,
)
from app.journal.export import JournalExport, to_markdown, when
from app.schema.fact import ChangeValue, Fact, Metric

SINGAPORE = ZoneInfo("Asia/Singapore")
EXPORT_FACT = JournalFact(
    id="e2_1",
    statement="Cut the nightly export from about 50 to 12 minutes.",
    metrics=(
        Metric(
            subject="export duration",
            value=ChangeValue(before=50, after=12),
            unit="minutes",
            qualifier="approximately",
        ),
    ),
    ownership="owned",
    tools=("PostgreSQL",),
    outcome="Reports are ready by 9am.",
    date="2026-10",
    role_id="northwind",
    entry_id="e2",
)


def entry(opened_at: datetime, *texts: str, **fields: object) -> JournalEntryView:
    messages = tuple(
        JournalMessageView(
            id=uuid.uuid4(),
            sent_at=opened_at,
            edited_at=None,
            text=text,
            edits=(MessageEdit(text="earlier", replaced_at=opened_at),),
        )
        for text in texts
    )
    return JournalEntryView.model_validate(
        {
            "id": uuid.uuid4(),
            "openedAt": opened_at,
            "closedAt": opened_at,
            "status": "processed",
            "triage": "work",
            "roleId": None,
            "newRole": None,
            "messages": messages,
            "facts": (),
        }
        | fields
    )


def test_the_markdown_groups_entries_by_month_in_the_owners_time_zone() -> None:
    """20:30 UTC on 30 September is already 1 October in Singapore."""
    export = JournalExport(
        exported_at=datetime(2026, 10, 12, 6, 3, tzinfo=UTC),
        entries=(
            entry(datetime(2026, 9, 28, 10, 0, tzinfo=UTC), "planning week", triage="other"),
            entry(
                datetime(2026, 9, 30, 20, 30, tzinfo=UTC),
                "cut the nightly export to ~12 min",
                "line one\nline two",
                roleId="northwind",
                facts=(EXPORT_FACT, JournalFact(id="e2_2", statement="Wrote the runbook.")),
            ),
            entry(
                datetime(2026, 10, 2, 1, 0, tzinfo=UTC),
                "on call this week",
                status="waiting_for_role",
                triage=None,
            ),
            entry(
                datetime(2026, 10, 3, 1, 0, tzinfo=UTC),
                "new job!",
                status="open",
                triage=None,
                newRole="Data Engineer, Example Co",
            ),
        ),
        roles=(RoleOption(id="northwind", label="Engineer, Northwind Traders (2025-now)"),),
        projects=(),
        imported_facts=(Fact(id="nw_orders", statement="Built the orders API.", origin="import"),),
    )
    assert to_markdown(export, SINGAPORE) == (
        "# Work journal\n"
        "\n"
        "Exported Mon 12 Oct 2026, 14:03 (Asia/Singapore): 4 entries and 2 facts from them.\n"
        "\n"
        "## September 2026\n"
        "\n"
        "### Mon 28 Sep 2026, 18:00\n"
        "\n"
        "Not about work, so it has no facts.\n"
        "\n"
        "planning week\n"
        "\n"
        "## October 2026\n"
        "\n"
        "### Thu 1 Oct 2026, 04:30\n"
        "\n"
        "Role: Engineer, Northwind Traders (2025-now)\n"
        "\n"
        "cut the nightly export to ~12 min\n"
        "\n"
        "line one\nline two\n"
        "\n"
        "Facts:\n"
        "\n"
        "- Cut the nightly export from about 50 to 12 minutes.\n"
        "  (accomplishment; owned; 2026-10; export duration: approximately 50 -> 12 minutes; "
        "tools: PostgreSQL; outcome: Reports are ready by 9am.)\n"
        "- Wrote the runbook.\n"
        "  (accomplishment)\n"
        "\n"
        "### Fri 2 Oct 2026, 09:00\n"
        "\n"
        "Waiting for you to choose its role in Telegram.\n"
        "\n"
        "on call this week\n"
        "\n"
        "### Sat 3 Oct 2026, 09:00\n"
        "\n"
        "New role: Data Engineer, Example Co\n"
        "\n"
        "Still open: your next message joins it.\n"
        "\n"
        "new job!\n"
        "\n"
        "## Facts from your imported resume\n"
        "\n"
        "- Built the orders API.\n"
        "  (accomplishment)\n"
    )


def test_an_empty_journal_says_so() -> None:
    export = JournalExport(
        exported_at=datetime(2026, 10, 12, 6, 3, tzinfo=UTC),
        entries=(),
        roles=(),
        projects=(),
        imported_facts=(),
    )
    assert to_markdown(export, ZoneInfo("UTC")) == (
        "# Work journal\n"
        "\n"
        "Exported Mon 12 Oct 2026, 06:03 (UTC): 0 entries and 0 facts from them.\n"
    )


def test_times_are_written_in_ascii_in_the_time_zone() -> None:
    moment = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    assert when(moment, ZoneInfo("America/Los_Angeles")) == "Wed 31 Dec 2025, 16:05"
    assert when(moment, SINGAPORE).isascii()
