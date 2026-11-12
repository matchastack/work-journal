"""The owner's reminder setting and the bot's reminder record (FR-REM-1, FR-SET-2), the
portfolio and confidential-terms settings (FR-SET-3, FR-SET-5), and the portfolio page's
publishing state (FR-PRT-3, FR-PRT-7)."""

from datetime import UTC, date, datetime, time

import pytest
from pydantic import ValidationError

from app.schema.settings import (
    ConfidentialTerms,
    PortfolioPublishing,
    PortfolioSettings,
    PublishedResume,
    ReminderSettings,
    ReminderState,
)


def test_pausing_alone_is_a_whole_setting() -> None:
    """`/pause` stores only the pause: the schedule keeps following the app's defaults."""
    reminders = ReminderSettings.model_validate({"paused": True})
    assert reminders == ReminderSettings(paused=True)
    assert (reminders.days, reminders.time, reminders.time_zone) == (None, None, None)


def test_a_schedule_is_stored_in_camel_case_with_days_in_week_order() -> None:
    reminders = ReminderSettings.model_validate(
        {"days": ["thu", "mon", "thu"], "time": "07:30", "timeZone": "Europe/London"}
    )
    assert reminders.days == ("mon", "thu")
    assert reminders.time == time(7, 30)
    assert reminders.model_dump(mode="json") == {
        "days": ["mon", "thu"],
        "time": "07:30:00",
        "timeZone": "Europe/London",
        "paused": False,
    }


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ({"days": []}, "at least 1 item"),
        ({"days": ["friday"]}, "Input should be 'mon'"),
        ({"time": "18:00+08:00"}, "must be a local time"),
        ({"time": "18:00:30"}, "in hours and minutes"),
        ({"timeZone": "Mars/Olympus_Mons"}, "not a time zone"),
        ({"timeZone": ""}, "not a time zone"),
        ({"timeZone": "Asia"}, "not a time zone"),
        ({"pausd": True}, "Extra inputs"),
    ],
)
def test_a_setting_that_cant_be_followed_is_rejected(value: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        ReminderSettings.model_validate(value)


def test_the_reminder_record_round_trips_through_json() -> None:
    state = ReminderState(
        last_slot=datetime(2026, 10, 16, 10, 0, tzinfo=UTC),
        message_id=42,
        message_slot=datetime(2026, 10, 16, 10, 0, tzinfo=UTC),
        snoozed_until=datetime(2026, 10, 17, 10, 0, tzinfo=UTC),
        nothing_weeks=(date(2026, 10, 12),),
    )
    assert ReminderState.model_validate_json(state.model_dump_json()) == state
    assert ReminderState() == ReminderState(
        last_slot=None, message_id=None, message_slot=None, snoozed_until=None, nothing_weeks=()
    )


def test_the_record_keeps_the_open_questions_asked_in_camel_case() -> None:
    """FR-REM-5. A record saved before reminders asked questions still reads, with none asked."""
    state = ReminderState(message_question="q_nw_volume", asked=("q_ct_rate", "q_nw_volume"))
    data = state.model_dump(mode="json", by_alias=True)
    assert (data["messageQuestion"], data["asked"]) == ("q_nw_volume", ["q_ct_rate", "q_nw_volume"])
    assert ReminderState.model_validate(data) == state
    old = ReminderState.model_validate({"lastSlot": "2026-10-16T10:00:00Z", "messageId": 42})
    assert (old.message_question, old.asked) == (None, ())


def test_the_record_keeps_times_with_their_time_zone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ReminderState.model_validate({"lastSlot": "2026-10-16T18:00:00"})


def test_the_portfolio_has_no_handle_until_the_owner_chooses_one() -> None:
    portfolio = PortfolioSettings()
    assert portfolio.handle is None
    assert portfolio.model_dump(mode="json") == {
        "handle": None,
        "defaultVisibility": "public",
        "openToWork": False,
        "indexing": True,
    }


@pytest.mark.parametrize(
    ("typed", "kept"),
    [("casey-example", "casey-example"), (" Casey-Example ", "casey-example"), ("c7", "c7")],
)
def test_a_handle_is_kept_in_lowercase(typed: str, kept: str) -> None:
    assert PortfolioSettings(handle=typed).handle == kept


@pytest.mark.parametrize(
    "handle",
    [
        "c",
        "-casey",
        "casey-",
        "casey--example",
        "casey example",
        "casey_example",
        "casey/x",
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
        "x" * 41,
        "",
    ],
)
def test_a_handle_that_isnt_safe_in_an_address_is_refused(handle: str) -> None:
    with pytest.raises(ValidationError, match="letters, digits and single hyphens"):
        PortfolioSettings(handle=handle)


def test_the_default_visibility_is_one_of_the_profiles() -> None:
    assert PortfolioSettings(default_visibility="resume_only").default_visibility == "resume_only"
    with pytest.raises(ValidationError, match="'public', 'resume_only' or 'private'"):
        PortfolioSettings.model_validate({"defaultVisibility": "hidden"})


def test_confidential_terms_are_kept_once_each_in_order() -> None:
    terms = ConfidentialTerms(terms=("Project Falcon", " Acme Labs ", "project falcon", "PF-9"))
    assert terms.terms == ("Project Falcon", "Acme Labs", "PF-9")


@pytest.mark.parametrize(
    ("terms", "error"),
    [
        ([""], "at least 1 character"),
        (["x" * 101], "at most 100 characters"),
        ([f"term {n}" for n in range(201)], "at most 200 items"),
    ],
)
def test_a_term_list_that_cant_be_kept_is_refused(terms: list[str], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        ConfidentialTerms.model_validate({"terms": terms})


def test_a_page_is_unpublished_until_the_owner_publishes_it() -> None:
    assert PortfolioPublishing() == PortfolioPublishing(published=False, version=None, resumes=())
    published = PortfolioPublishing(
        published=True, version=3, published_at=datetime(2026, 10, 31, 2, 0, tzinfo=UTC)
    )
    assert published.model_dump(mode="json") == {
        "published": True,
        "version": 3,
        "publishedAt": "2026-10-31T02:00:00Z",
        "resumes": [],
    }


@pytest.mark.parametrize(
    "value",
    [
        {"published": True},
        {"published": True, "version": 3},
        {"published": False, "version": 3, "publishedAt": "2026-10-31T02:00:00Z"},
    ],
)
def test_a_published_page_has_a_version_and_a_time_and_only_it_does(
    value: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="a published page has a version and a time"):
        PortfolioPublishing.model_validate(value)


def test_a_resume_names_its_application_only_when_it_is_tailored() -> None:
    master = PublishedResume(source="master", label="Resume")
    tailored = PublishedResume(source="application", application_id="app_1", label="CV")
    assert (master.key, tailored.key) == ("master", "application:app_1")
    for value in (
        {"source": "master", "applicationId": "app_1", "label": "Resume"},
        {"source": "application", "label": "Resume"},
    ):
        with pytest.raises(ValidationError, match="names its application"):
            PublishedResume.model_validate(value)


def test_the_page_offers_a_few_resumes_each_once() -> None:
    master = {"source": "master", "label": "Resume"}
    with pytest.raises(ValidationError, match="each resume can be offered once"):
        PortfolioPublishing.model_validate({"resumes": [master, master | {"label": "CV"}]})
    many = [{"source": "application", "applicationId": f"a{n}", "label": "CV"} for n in range(6)]
    with pytest.raises(ValidationError, match="at most 5 items"):
        PortfolioPublishing.model_validate({"resumes": many})
    with pytest.raises(ValidationError, match="at most 60 characters"):
        PublishedResume(source="master", label="x" * 61)
