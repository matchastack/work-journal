"""The catch-up interview: questions about the time since the profile was last updated
(FR-REM-4).

A standard-tier prompt reads the profile's roles, projects and skills, and the interview so far,
and writes the next question: about new roles, projects, achievements or skills, one at a time.
It ends the interview when there's little left worth asking, and code ends it after
`MAX_QUESTIONS` questions, skipped ones included.

Code checks each question before the bot asks it, as it does the follow-up question: one short
sentence that ends with "?", with no digits or number words, so that it can't put a figure in the
owner's mouth, and one not asked before. A question that fails the check, or a failed call to the
model, gives way to the next fixed question not yet asked.

The question goes to the owner and is kept with the answer's journal entry, but it's never
evidence: triage and extraction read the owner's answer alone, as they read any note, so nothing
the bot wrote can become a fact (FR-EXT-2, R1).
"""

from collections.abc import Sequence
from xml.sax.saxutils import escape

from app.extraction import profile_context
from app.llm.client import LLMCallError, LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.schema.common import Model
from app.schema.profile import Profile
from app.validate.quantities import find_quantities

PROMPT_NAME = "catch_up_interview"
PROMPT_VERSION = 1
MAX_QUESTIONS = 10
"""The most questions an interview asks, skipped ones included."""
QUESTION_LIMIT = 200
"""The longest question the bot asks, in characters."""
NO_PROFILE_CONTEXT = (
    "The person hasn't loaded a profile yet, so it has no roles, projects or skills."
)

FIXED_QUESTIONS: tuple[str, ...] = (
    "What has changed in your work since your resume was last updated, such as a new job, title, "
    "team or responsibilities?",
    "What projects have you worked on since then, and what was your part in each?",
    "What result from that time are you proudest of, and what did it change?",
    "Which tools, languages or methods have you started using since then?",
)
"""The questions asked, in order, when the model's own can't be: one for each topic."""


class Turn(Model):
    """One question in the interview, and the owner's answer, or None when they skipped it."""

    question: str
    answer: str | None = None


class InterviewReply(Model):
    done: bool
    """Whether the interview has covered what's worth covering."""
    question: str | None = None


def next_question(
    profile: Profile | None,
    turns: Sequence[Turn],
    client: LLMClient,
    prompt: Prompt | None = None,
) -> str | None:
    """The next question to ask, after `turns`, or None when the interview is over."""
    if len(turns) >= MAX_QUESTIONS:
        return None
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    try:
        result = client.call(
            "catch_up_interview",
            prompt,
            InterviewReply,
            variables={
                "asked": str(len(turns)),
                "limit": str(MAX_QUESTIONS),
                "interview": interview_text(turns),
            },
            context=profile_context(profile) if profile is not None else NO_PROFILE_CONTEXT,
        )
    except LLMCallError:
        return fixed_question(turns)
    reply = result.output
    if reply.done and turns:
        return None
    question = " ".join((reply.question or "").split())
    if usable(question) and question not in asked(turns):
        return question
    return fixed_question(turns)


def fixed_question(turns: Sequence[Turn]) -> str | None:
    """The first fixed question not asked yet, or None when all of them have been."""
    return next((question for question in FIXED_QUESTIONS if question not in asked(turns)), None)


def usable(question: str) -> bool:
    """Whether the model's question can be asked as it is: one sentence, ending with "?", with
    no number in digits or words."""
    return (
        0 < len(question) <= QUESTION_LIMIT
        and question.endswith("?")
        and not any(mark in question[:-1] for mark in ".!?")
        and not any(character.isdigit() for character in question)
        and not find_quantities(question)
    )


def asked(turns: Sequence[Turn]) -> set[str]:
    return {turn.question for turn in turns}


def interview_text(turns: Sequence[Turn]) -> str:
    """The interview so far, for the prompt. The answers are fenced as data."""
    if not turns:
        return "No questions asked yet."
    parts: list[str] = []
    for number, turn in enumerate(turns, start=1):
        answer = "<skipped/>" if turn.answer is None else f"<answer>{escape(turn.answer)}</answer>"
        parts.append(
            f'<turn n="{number}">\n<question>{escape(turn.question)}</question>\n{answer}\n</turn>'
        )
    return "\n".join(parts)
