from typing import get_args

from app.llm.routing import TASK_TIERS, Task


def test_every_task_has_a_tier() -> None:
    assert set(TASK_TIERS) == set(get_args(Task))


def test_tiers_follow_the_requirements() -> None:
    """Requirements §10: the heavier models only where a task needs them."""
    by_tier: dict[str, set[str]] = {}
    for task, tier in TASK_TIERS.items():
        by_tier.setdefault(tier, set()).add(task)
    assert by_tier["heavy"] == {"profile_synthesis", "tailoring_selection", "pdf_import"}
    assert by_tier["standard"] == {
        "fact_extraction",
        "catch_up_interview",
        "bullet_writing",
        "claim_verification",
        "linkedin_pack",
    }
    assert by_tier["light"] == {
        "follow_up_question",
        "entry_summary",
        "message_triage",
        "posting_parsing",
    }
