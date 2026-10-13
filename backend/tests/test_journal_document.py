"""The journal document's parts that don't need the database (T-041)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.db.models import JournalEntry
from app.journal.document import Citation, EntryStatus, citations, entry_status
from app.schema.profile import Profile

PROFILE = Profile.model_validate_json(
    (Path(__file__).parent / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
AT = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("state", "status"),
    [
        ({}, "open"),
        ({"closed_at": AT}, "waiting_for_role"),
        ({"closed_at": AT, "role_asked_at": AT}, "waiting_for_role"),
        ({"closed_at": AT, "role_id": "northwind"}, "extracting"),
        ({"closed_at": AT, "new_role": "Data Engineer, Example Co"}, "extracting"),
        ({"closed_at": AT, "role_id": "northwind", "processed_at": AT}, "processed"),
        ({"closed_at": AT, "processed_at": AT}, "processed"),
        ({"closed_at": AT, "role_id": "northwind", "failed_at": AT}, "failed"),
    ],
)
def test_an_entrys_status_follows_capture(state: dict[str, Any], status: EntryStatus) -> None:
    """FR-CAP-4, FR-CAP-11 and FR-CAP-5. An empty entry is processed without a role."""
    assert entry_status(JournalEntry(opened_at=AT, last_message_at=AT, **state)) == status


def test_citations_list_each_bullet_once_per_fact() -> None:
    data = PROFILE.model_dump(by_alias=True)
    data["work"][0]["highlights"][0]["sources"] = ["f_1", "f_2", "f_1"]
    data["projects"][0]["highlights"][0]["sources"] = ["f_1"]
    cited = citations(Profile.model_validate(data))
    bullets = {fact_id: [c.bullet_id for c in found] for fact_id, found in cited.items()}
    assert bullets == {
        "f_1": ["nw_orders", data["projects"][0]["highlights"][0]["id"]],
        "f_2": ["nw_orders"],
        "fact_nw_export": ["nw_export"],
    }
    assert isinstance(cited["f_2"][0], Citation)
