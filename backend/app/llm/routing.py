"""Which model tier each task uses (requirements §10). The model IDs come from the environment."""

from typing import Literal

Tier = Literal["heavy", "standard", "light"]

Task = Literal[
    "profile_synthesis",
    "tailoring_selection",
    "pdf_import",
    "fact_extraction",
    "catch_up_interview",
    "bullet_writing",
    "claim_verification",
    "linkedin_pack",
    "follow_up_question",
    "entry_summary",
    "message_triage",
    "posting_parsing",
]

TASK_TIERS: dict[Task, Tier] = {
    # Heavy: the most demanding reasoning only.
    "profile_synthesis": "heavy",
    "tailoring_selection": "heavy",
    "pdf_import": "heavy",
    # Standard: short tasks that still need judgement.
    "fact_extraction": "standard",
    "catch_up_interview": "standard",
    "bullet_writing": "standard",
    "claim_verification": "standard",
    "linkedin_pack": "standard",
    # Light: the smallest, most frequent tasks.
    "follow_up_question": "light",
    "entry_summary": "light",
    "message_triage": "light",
    "posting_parsing": "light",
}
