"""The follow-up question: what a journal note leaves out (FR-CAP-6).

A light-tier prompt reads a closed entry's text and names the one gap most worth asking about:
the numbers that would measure its result, what it changed, or the owner's part in it. It names
none when the note already says enough, is too slight to ask about, or isn't about work. With a
gap, it also writes a short question about the note's own subject.

Code checks the question before the bot asks it: one short sentence that ends with "?" and has
no digits, so that it can't put a figure in the owner's mouth. A question that fails the check
gives way to a fixed one for the gap. The question goes to the owner, never into the profile, and
only the owner's answer joins the entry.
"""

from typing import Literal

from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.schema.common import Model

PROMPT_NAME = "follow_up_question"
PROMPT_VERSION = 1
QUESTION_LIMIT = 160
"""The longest question the bot asks, in characters."""

Gap = Literal["metrics", "impact", "ownership"]

FIXED_QUESTIONS: dict[Gap, str] = {
    "metrics": "Can you put a number on it, such as time saved, people reached or how much faster?",
    "impact": "What did it change, and for whom?",
    "ownership": "What was your part in it: did you lead it, build it, or help?",
}
"""The question for each gap when the model's own fails the check."""


class FollowUpReply(Model):
    missing: Gap | None
    """The gap most worth asking about, or None when no question is needed."""
    question: str | None = None


def follow_up_question(note: str, client: LLMClient, prompt: Prompt | None = None) -> str | None:
    """The one question to ask about `note`, the text of a journal entry, or None when nothing's
    worth asking."""
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    result = client.call("follow_up_question", prompt, FollowUpReply, variables={"note": note})
    reply = result.output
    if reply.missing is None:
        return None
    question = " ".join((reply.question or "").split())
    return question if usable(question) else FIXED_QUESTIONS[reply.missing]


def usable(question: str) -> bool:
    """Whether the model's question can be asked as it is."""
    return (
        0 < len(question) <= QUESTION_LIMIT
        and question.endswith("?")
        and not any(character.isdigit() for character in question)
    )
