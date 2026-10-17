"""Which open question a reminder asks (FR-REM-5), and how its answer is taken (FR-EXT-4).

Pure functions, without I/O or a clock, like `app/reminders.py`: `app/telegram/reminders.py`
reads the owner's latest profile version, the journal and the bot's reminder record, and sends
the question with the reminder.

A question's *value* is the strength of the bullets that list it in `needs`: high 3, medium 2 and
low 1, added up. Benched bullets don't count, and neither do per-role-type strengths. A question
that blocks nothing is worth 0, so it comes last.

The *candidates* are the open questions the journal holds no answer to. Each reminder asks the
most valuable candidate not yet asked in the current round, ties in the profile's order; once
every candidate has been asked, a new round starts. So a question the owner leaves unanswered
doesn't come back every week, and one added to the profile joins the round at once.

Once the owner presses "Answer the question", their next message within the entry timeout is the
answer (`awaits_answer`). It's about the role that holds the bullets needing the question, when
one role holds them all (`answer_role`); code chooses it, never the model.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.schema.profile import OpenQuestion, Profile, Strength
from app.schema.settings import Answering

STRENGTH_VALUES: dict[Strength, int] = {"high": 3, "medium": 2, "low": 1}
"""What a bullet of each strength adds to the value of a question it needs."""


@dataclass(frozen=True)
class NextQuestion:
    """The question a reminder asks, and the round once it's asked."""

    question: OpenQuestion
    asked: tuple[str, ...]
    """The IDs of the questions asked in this round, oldest first, this one last."""


def value(profile: Profile, question: OpenQuestion) -> int:
    """How much answering `question` would strengthen the profile's bullets."""
    return sum(
        STRENGTH_VALUES[bullet.strength]
        for bullet in profile.bullets_blocked_by(question.id)
        if bullet.status != "benched"
    )


def ranked(profile: Profile, answered: Collection[str]) -> list[OpenQuestion]:
    """The open questions not among `answered`, most valuable first, ties in the profile's
    order."""
    candidates = [
        question
        for question in profile.open_questions
        if question.status == "open" and question.id not in answered
    ]
    return sorted(candidates, key=lambda question: -value(profile, question))


def next_question(
    profile: Profile, *, answered: Collection[str], asked: Sequence[str]
) -> NextQuestion | None:
    """The question for the next reminder: the most valuable candidate not in `asked`, the round
    so far, or the most valuable of all to start a new round once each has been asked. None
    when there's no candidate. Questions that stopped being candidates leave the round."""
    candidates = ranked(profile, answered)
    if not candidates:
        return None
    ids = {question.id for question in candidates}
    round_so_far = tuple(question_id for question_id in asked if question_id in ids)
    waiting = [question for question in candidates if question.id not in round_so_far]
    if not waiting:
        round_so_far, waiting = (), candidates
    chosen = waiting[0]
    return NextQuestion(chosen, (*round_so_far, chosen.id))


def answer_role(profile: Profile, question_id: str) -> str | None:
    """The role an answer to the question is about: the one holding every bullet that needs it.
    None when no bullet needs it, or when they're under more than one item, or under an
    education entry or a project, so that the bot asks the owner (FR-CAP-11)."""
    holders = {
        item.id
        for item in (*profile.work, *profile.education, *profile.projects)
        for bullet in item.highlights
        if question_id in bullet.needs
    }
    roles = {role.id for role in profile.work}
    if len(holders) == 1 and holders <= roles:
        return next(iter(holders))
    return None


def awaits_answer(answering: Answering | None, at: datetime, timeout: timedelta) -> bool:
    """Whether a message sent at `at` answers the question the owner chose to answer: they
    pressed "Answer the question" less than `timeout` before. After that, the wait has lapsed,
    as a follow-up question does."""
    return answering is not None and at - answering.since < timeout
