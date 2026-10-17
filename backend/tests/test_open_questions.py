"""Which open question a reminder asks (FR-REM-5), and how its answer is taken (FR-EXT-4). The
bot's side is tested in `db/test_telegram_reminders_db.py`."""

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.open_questions import (
    NextQuestion,
    answer_role,
    awaits_answer,
    next_question,
    ranked,
    value,
)
from app.schema.profile import OpenQuestion, Profile
from app.schema.settings import Answering

FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
"""The fixture profile. Its bullets: under northwind, nw_orders and nw_export (high) and
nw_tracker (low, benched); under contoso, ct_sensors (medium, high for data roles) and
ct_datasets (low); under the education entry, ss_minor (medium); and under projects, rb_app
(medium) and bc_model (medium, planned)."""


def bullets(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for section in ("work", "education", "projects"):
        for item in data[section]:
            yield from item["highlights"]


def profile(
    needs: Mapping[str, tuple[str, ...]], *, statuses: Mapping[str, str] | None = None
) -> Profile:
    """The fixture profile with these open questions, in this order, each needed by the bullets
    listed, and open unless `statuses` says otherwise."""
    data = json.loads(json.dumps(FIXTURE))
    data["openQuestions"] = [
        {"id": question_id, "question": f"What about {question_id}?"}
        | ({"status": statuses[question_id]} if statuses and question_id in statuses else {})
        for question_id in needs
    ]
    for bullet in bullets(data):
        bullet["needs"] = [question_id for question_id, ids in needs.items() if bullet["id"] in ids]
    return Profile.model_validate(data)


def question(of: Profile, question_id: str) -> OpenQuestion:
    return next(item for item in of.open_questions if item.id == question_id)


def ids(questions: list[OpenQuestion]) -> list[str]:
    return [item.id for item in questions]


# --- Value ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("needed_by", "expected"),
    [
        pytest.param(("nw_orders",), 3, id="a high bullet"),
        pytest.param(("ct_sensors",), 2, id="a medium bullet, whatever its role-type strengths"),
        pytest.param(("ct_datasets",), 1, id="a low bullet"),
        pytest.param(("nw_export", "ct_datasets", "rb_app"), 6, id="several, added up"),
        pytest.param(("nw_orders", "nw_tracker"), 3, id="a benched bullet doesn't count"),
        pytest.param(("bc_model",), 2, id="a planned bullet counts"),
        pytest.param((), 0, id="nothing"),
    ],
)
def test_a_questions_value_adds_up_the_strengths_of_the_bullets_that_need_it(
    needed_by: tuple[str, ...], expected: int
) -> None:
    with_question = profile({"q_one": needed_by})
    assert value(with_question, question(with_question, "q_one")) == expected


# --- Ranking -------------------------------------------------------------------------------------


def test_open_questions_without_an_answer_are_ranked_most_valuable_first() -> None:
    """Ties keep the profile's order. Answered and closed questions aren't candidates, nor are
    those the journal holds an answer to."""
    ranking = profile(
        {
            "q_low": ("ct_datasets",),
            "q_none": (),
            "q_high": ("nw_orders",),
            "q_tie": ("nw_export",),
            "q_answered": ("ct_sensors", "rb_app"),
            "q_closed": ("ct_sensors", "rb_app"),
            "q_in_journal": ("ct_sensors", "rb_app"),
        },
        statuses={"q_answered": "answered", "q_closed": "closed"},
    )
    assert ids(ranked(ranking, answered={"q_in_journal"})) == [
        "q_high",
        "q_tie",
        "q_low",
        "q_none",
    ]


# --- The next question ---------------------------------------------------------------------------

THREE = profile({"q_low": ("ct_datasets",), "q_high": ("nw_orders",), "q_mid": ("ct_sensors",)})
"""Three open questions, worth 1, 3 and 2."""


def test_each_reminder_asks_the_most_valuable_question_not_asked_in_this_round() -> None:
    asked: tuple[str, ...] = ()
    order: list[str] = []
    for _ in range(5):
        chosen = next_question(THREE, answered=(), asked=asked)
        assert chosen is not None
        order.append(chosen.question.id)
        asked = chosen.asked
    assert order == ["q_high", "q_mid", "q_low", "q_high", "q_mid"]
    assert asked == ("q_high", "q_mid"), "the second round so far"


def test_the_first_question_starts_the_round() -> None:
    assert next_question(THREE, answered=(), asked=()) == NextQuestion(
        question(THREE, "q_high"), ("q_high",)
    )


def test_a_question_answered_meanwhile_leaves_the_round() -> None:
    """q_high has an answer now, so the round is q_mid alone, and q_low is next."""
    chosen = next_question(THREE, answered={"q_high"}, asked=("q_high", "q_mid"))
    assert chosen == NextQuestion(question(THREE, "q_low"), ("q_mid", "q_low"))


def test_a_question_gone_from_the_profile_leaves_the_round() -> None:
    chosen = next_question(THREE, answered=(), asked=("q_gone", "q_high"))
    assert chosen == NextQuestion(question(THREE, "q_mid"), ("q_high", "q_mid"))


def test_a_question_added_to_the_profile_joins_the_round() -> None:
    """q_new, worth 3 like q_high, comes before q_mid."""
    added = profile(
        {
            "q_low": ("ct_datasets",),
            "q_high": ("nw_orders",),
            "q_mid": ("ct_sensors",),
            "q_new": ("nw_export",),
        }
    )
    chosen = next_question(added, answered=(), asked=("q_high",))
    assert chosen == NextQuestion(question(added, "q_new"), ("q_high", "q_new"))


def test_one_question_is_asked_each_time() -> None:
    one = profile({"q_only": ("nw_orders",)})
    assert next_question(one, answered=(), asked=("q_only",)) == NextQuestion(
        question(one, "q_only"), ("q_only",)
    )


@pytest.mark.parametrize(
    ("needs", "statuses", "answered"),
    [
        pytest.param({}, None, (), id="no open questions"),
        pytest.param({"q_one": ("nw_orders",)}, {"q_one": "answered"}, (), id="answered"),
        pytest.param({"q_one": ("nw_orders",)}, {"q_one": "closed"}, (), id="closed"),
        pytest.param({"q_one": ("nw_orders",)}, None, ("q_one",), id="answered in the journal"),
    ],
)
def test_no_question_without_a_candidate(
    needs: dict[str, tuple[str, ...]], statuses: dict[str, str] | None, answered: tuple[str, ...]
) -> None:
    without = profile(needs, statuses=statuses)
    assert next_question(without, answered=answered, asked=()) is None


# --- The answer (FR-EXT-4) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("needed_by", "expected"),
    [
        pytest.param(("nw_orders",), "northwind", id="one bullet"),
        pytest.param(("nw_orders", "nw_tracker"), "northwind", id="bullets under one role"),
        pytest.param(("nw_orders", "ct_sensors"), None, id="bullets under two roles"),
        pytest.param(("nw_orders", "rb_app"), None, id="a role's bullet and a project's"),
        pytest.param(("ss_minor",), None, id="an education entry's bullet"),
        pytest.param(("rb_app",), None, id="a project's bullet"),
        pytest.param((), None, id="no bullet"),
    ],
)
def test_an_answer_takes_the_role_that_holds_every_bullet_needing_the_question(
    needed_by: tuple[str, ...], expected: str | None
) -> None:
    """Otherwise the bot asks which role it's about (FR-CAP-11)."""
    assert answer_role(profile({"q_one": needed_by}), "q_one") == expected


def test_a_question_gone_from_the_profile_gives_no_role() -> None:
    assert answer_role(profile({"q_one": ("nw_orders",)}), "q_gone") is None


PRESSED = datetime(2026, 10, 16, 10, 30, tzinfo=UTC)


def test_the_answer_is_awaited_for_the_entry_timeout_after_the_press() -> None:
    answering = Answering(question_id="q_one", since=PRESSED)
    timeout = timedelta(minutes=30)
    assert awaits_answer(answering, PRESSED + timedelta(minutes=29, seconds=59), timeout)
    assert not awaits_answer(answering, PRESSED + timeout, timeout), "lapsed"
    assert not awaits_answer(None, PRESSED, timeout), "nothing pressed"
