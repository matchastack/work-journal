from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.schema.profile import Profile
from app.schema.variant import Variant
from app.selection import MASTER_VARIANT, Selection, select
from app.validate.lint import (
    LintReport,
    NotSendable,
    check_sendable,
    format_report,
    lint_profile,
    require_sendable,
)

PROFILE = Profile.model_validate_json(
    (Path(__file__).parent / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
UNREAD_RULE = (
    'education[springfield_state].rules: rule 1 isn\'t checked: "Show the minor after the major."'
)

Json = dict[str, Any]


def edited(change: Callable[[Json], object]) -> Profile:
    """The fixture with `change` applied to its JSON, validated again."""
    data = PROFILE.model_dump(mode="json")
    change(data)
    return Profile.model_validate(data)


def bullet(data: Json, bullet_id: str) -> Json:
    entries = [*data["work"], *data["education"], *data["projects"]]
    return next(b for entry in entries for b in entry["highlights"] if b["id"] == bullet_id)


def set_text(bullet_id: str, text: str) -> Callable[[Json], object]:
    return lambda data: bullet(data, bullet_id).update(text=text)


def tailored(profile: Profile = PROFILE, role_type: str | None = "backend") -> Selection:
    return select(profile, Variant(id="tailored", name="Tailored", role_type=role_type))


def found(report: LintReport) -> list[tuple[str, str, str]]:
    return [(f.rule, f.severity, f.location) for f in report.findings]


def test_the_fictional_profile_has_no_findings() -> None:
    report = lint_profile(PROFILE)
    assert report.findings == ()
    assert report.notes == (UNREAD_RULE,)


def test_a_resume_tailored_from_the_fictional_profile_is_sendable() -> None:
    for role_type in ("backend", "data"):
        assert require_sendable(tailored(role_type=role_type), PROFILE).findings == ()


# --- R2: placeholders and missing optional fields -------------------------------------------


@pytest.mark.parametrize(
    ("text", "placeholder"),
    [
        ("Cut hosting costs by XX%.", "XX%"),
        ("Cut hosting costs by TODO.", "TODO"),
        ("Led [team size] engineers through the migration.", "[team size]"),
        ("Shipped <feature name> to every customer.", "<feature name>"),
        ("Reduced latency by ?? on the busiest route.", "??"),
    ],
)
def test_placeholder_text_is_an_error(text: str, placeholder: str) -> None:
    report = lint_profile(edited(set_text("nw_orders", text)))
    assert found(report) == [("R2", "error", "work[northwind].highlights[nw_orders]")]
    assert report.findings[0].message == f'placeholder text "{placeholder}"'


def test_comparisons_and_names_are_not_placeholders() -> None:
    def change(data: Json) -> None:
        bullet(data, "nw_orders").update(text="Kept latency < 50 ms at > 1,000 requests a second.")
        data["projects"][0]["name"] = "Todo App"

    assert lint_profile(edited(change)).findings == ()


def test_items_that_never_render_are_not_checked() -> None:
    def change(data: Json) -> None:
        bullet(data, "nw_tracker").update(text="TODO: rewrite")  # benched
        bullet(data, "bc_model").update(text="Classify [N] species.")  # planned project
        bullet(data, "nw_orders").update(visibility="private", text="TBD")

    assert lint_profile(edited(change)).findings == ()


def test_a_project_without_dates_is_a_warning() -> None:
    def change(data: Json) -> None:
        del data["projects"][0]["startDate"], data["projects"][0]["endDate"]

    profile = edited(change)
    assert found(lint_profile(profile)) == [("R2", "warning", "projects[recipe_box]")]
    report = require_sendable(tailored(profile), profile)
    assert found(report) == [("R2", "warning", "projects[recipe_box]")]


# --- R3: the gaps list and verify-before-shipping skills ------------------------------------


def test_a_skill_on_the_gaps_list_is_an_error_wherever_it_is_claimed() -> None:
    def change(data: Json) -> None:
        data["projects"][0]["keywords"].append("rust")
        data["skillPresets"][0]["lines"][0]["skills"].append("Rust")
        bullet(data, "nw_orders").update(text="Rewrote the order parser as a Rust-based service.")

    report = lint_profile(edited(change))
    assert found(report) == [
        ("R3", "error", "projects[recipe_box].keywords"),
        ("R3", "error", "skillPresets[backend].lines[Languages]"),
        ("R3", "error", "work[northwind].highlights[nw_orders]"),
    ]
    assert report.findings[0].message == "Rust is on the gaps list, so it must never be claimed"


def test_gaps_in_text_match_whole_words_with_their_case() -> None:
    def change(data: Json) -> None:
        bullet(data, "nw_orders").update(text="Led a swift migration of trusted services.")
        data["work"][1]["name"] = "Swift Logistics"  # a name, not a claim

    assert lint_profile(edited(change)).findings == ()


def test_a_verify_before_shipping_skill_needs_confirmation_for_each_resume() -> None:
    master = select(PROFILE, MASTER_VARIANT)  # lists every skill, Kotlin included
    report = check_sendable(master, PROFILE)
    assert found(report) == [("R3", "error", "skills[Languages]")]
    assert "Kotlin is marked verify-before-shipping" in report.findings[0].message
    assert check_sendable(master, PROFILE, confirmed=["kotlin"]).findings == ()


def test_the_profile_points_out_where_a_verify_before_shipping_skill_is_used() -> None:
    profile = edited(lambda data: data["skillPresets"][0]["lines"][0]["skills"].append("Kotlin"))
    report = lint_profile(profile)
    assert found(report) == [("R3", "warning", "skillPresets[backend].lines[Languages]")]
    assert report.findings[0].message == (
        "Kotlin is marked verify-before-shipping, so each resume that lists it needs your "
        "confirmation"
    )


# --- R1: the skills catalogue, and spelling (FR-PRF-6) --------------------------------------


def test_a_listed_skill_missing_from_the_catalogue_is_a_warning() -> None:
    report = lint_profile(edited(lambda data: data["projects"][0]["keywords"].append("Docker")))
    assert found(report) == [("R1", "warning", "projects[recipe_box].keywords")]
    assert report.findings[0].message.startswith("Docker isn't in the skills catalogue")


def test_aliases_and_wrong_capitals_are_spelling_warnings() -> None:
    def change(data: Json) -> None:
        bullet(data, "nw_orders").update(text="Built NodeJS and Postgresql services.")
        data["projects"][0]["keywords"] = ["TypeScript", "React", "postgreSQL"]

    report = lint_profile(edited(change))
    assert [(f.rule, f.location, f.message) for f in report.findings] == [
        ("spelling", "work[northwind].highlights[nw_orders]", 'write "NodeJS" as "Node.js"'),
        ("spelling", "work[northwind].highlights[nw_orders]", 'write "Postgresql" as "PostgreSQL"'),
        ("spelling", "projects[recipe_box].keywords", 'write "postgreSQL" as "PostgreSQL"'),
    ]


def test_ordinary_words_are_not_spelling_mistakes() -> None:
    def change(data: Json) -> None:
        data["skills"].append({"name": "REST", "category": "Backend & APIs"})
        text = "Helped the rest of the team react to incidents with event-driven python scripts."
        bullet(data, "nw_orders").update(text=text)

    assert lint_profile(edited(change)).findings == ()


# --- Consistency: duplicates and dates --------------------------------------------------------


def test_a_duplicate_bullet_is_a_warning() -> None:
    text = "build and maintain python services that process order events, from a message queue"
    report = lint_profile(edited(set_text("ct_sensors", text)))
    assert found(report) == [("duplicates", "warning", "work[contoso].highlights[ct_sensors]")]
    assert report.findings[0].message == "same text as work[northwind].highlights[nw_orders]"


def test_dates_written_in_a_second_format_are_warnings() -> None:
    def change(data: Json) -> None:
        bullet(data, "nw_orders").update(text="Launched the order service in Jan 2025.")
        bullet(data, "ct_sensors").update(text="Built a labelling tool by 03/2024.")
        bullet(data, "rb_app").update(text="Released a recipe app in Sept 2023.")

    report = lint_profile(edited(change))
    assert found(report) == [("dates", "warning", "work[contoso].highlights[ct_sensors]")]
    assert report.findings[0].message == (
        '"03/2024" is written as MM/YYYY, but most dates read like "Jan 2025" (Mon YYYY)'
    )


# --- R4: numbers shared across roles ------------------------------------------------------------


def test_the_same_metric_under_two_entries_is_a_warning() -> None:
    text = "Built a Python tool used by 300 people a month to label sensor logs."
    report = lint_profile(edited(set_text("ct_sensors", text)))
    assert found(report) == [("R4", "warning", "projects[recipe_box].highlights[rb_app]")]
    assert report.findings[0].message == (
        '"300 people" also appears in work[contoso].highlights[ct_sensors]; make sure both are '
        "right"
    )


def test_the_same_metric_twice_under_one_role_is_fine() -> None:
    def change(data: Json) -> None:
        bullet(data, "nw_orders").update(text="Cut order failures by 40% with retries.")
        bullet(data, "nw_export").update(text="Cut the export's cost by 40% by batching writes.")

    assert lint_profile(edited(change)).findings == ()


def test_numbers_in_skill_names_are_not_metrics() -> None:
    def change(data: Json) -> None:
        data["skills"].append({"name": "Python 3", "category": "Languages"})
        bullet(data, "nw_orders").update(text="Moved order services to Python 3.")
        bullet(data, "rb_app").update(text="Ported the recipe app to Python 3.")

    assert lint_profile(edited(change)).findings == ()


# --- R9: titles -----------------------------------------------------------------------------------


def test_an_unapproved_title_on_a_sendable_resume_is_an_error() -> None:
    selection = tailored()
    northwind = selection.work[0].model_copy(update={"title": "Senior Engineer"})
    selection = selection.model_copy(update={"work": (northwind, *selection.work[1:])})
    report = check_sendable(selection, PROFILE)
    assert found(report) == [("R9", "error", "work[northwind].title")]
    assert report.findings[0].message == '"Senior Engineer" isn\'t an approved title for this role'


def test_an_approved_title_that_adds_seniority_is_a_warning() -> None:
    profile = edited(lambda data: data["work"][0]["titleVariants"].append("Lead Engineer"))
    report = lint_profile(profile)
    assert found(report) == [("R9", "warning", "work[northwind].titleVariants")]
    assert report.findings[0].message == (
        '"Lead Engineer" adds "Lead" to the official title "Engineer"; keep it only if it was '
        "earned"
    )


# --- R10: education rules -------------------------------------------------------------------------


def with_education(rules: list[str], **fields: str) -> Profile:
    def change(data: Json) -> None:
        data["education"][0]["rules"] = rules
        data["education"][0].update(fields)

    return edited(change)


@pytest.mark.parametrize("rule", ["Never print GPA.", "Don't show the CGPA", "No GPA on resumes."])
def test_a_printed_gpa_breaks_a_never_print_gpa_rule(rule: str) -> None:
    profile = with_education([rule], honours="Magna Cum Laude, GPA 3.9/4.0")
    expected = [("R10", "error", "education[springfield_state].honours")]
    assert found(lint_profile(profile)) == expected
    assert found(check_sendable(tailored(profile), profile)) == expected
    assert lint_profile(profile).notes == ()


def test_a_gpa_is_fine_without_the_rule() -> None:
    profile = with_education([], honours="GPA 3.9/4.0")
    assert lint_profile(profile).findings == ()


@pytest.mark.parametrize("rule", ["Coursework: keep 2-3 courses.", "List at most 3 courses."])
def test_a_sendable_resume_lists_as_many_courses_as_the_rules_allow(rule: str) -> None:
    profile = with_education([rule])
    assert check_sendable(tailored(profile), profile).findings == ()  # the backend subset: 3
    master = select(profile, MASTER_VARIANT)  # all 5 courses
    report = check_sendable(master, profile, confirmed=["Kotlin"])
    assert found(report) == [("R10", "error", "education[springfield_state].courses")]
    assert report.findings[0].message.startswith("lists 5 courses, but the education rules allow")


def test_coursework_subsets_outside_the_rules_are_warnings() -> None:
    profile = with_education(["Keep 4 to 6 courses that match the posting."])
    assert found(lint_profile(profile)) == [
        ("R10", "warning", "education[springfield_state].courseworkSubsets[backend]"),
        ("R10", "warning", "education[springfield_state].courseworkSubsets[data]"),
    ]


def test_rules_code_cant_read_are_notes() -> None:
    profile = with_education(["Never print GPA.", "Show the minor after the major."])
    assert lint_profile(profile).notes == (
        "education[springfield_state].rules: rule 2 isn't checked: "
        '"Show the minor after the major."',
    )


# --- Blocking and output ------------------------------------------------------------------------


def test_errors_block_a_sendable_resume_and_warnings_dont() -> None:
    profile = edited(set_text("nw_orders", "Cut hosting costs by TODO."))
    with pytest.raises(NotSendable) as raised:
        require_sendable(tailored(profile), profile)
    assert str(raised.value) == 'work[northwind].highlights[nw_orders]: placeholder text "TODO"'
    assert raised.value.report.errors == raised.value.report.findings


def test_errors_come_before_warnings() -> None:
    def change(data: Json) -> None:
        data["projects"][0]["keywords"].append("Docker")
        bullet(data, "rb_app").update(text="Built a recipe app for TBD people.")

    report = lint_profile(edited(change))
    assert [f.severity for f in report.findings] == ["error", "warning"]


def test_format_report_lists_findings_notes_and_a_count() -> None:
    profile = edited(lambda data: data["projects"][0]["keywords"].append("Docker"))
    assert format_report(lint_profile(profile)).splitlines() == [
        "warning  R1          projects[recipe_box].keywords: Docker isn't in the skills "
        "catalogue; add it there, with its tier, or leave it out",
        "Not checked by code:",
        f"  {UNREAD_RULE}",
        "0 errors, 1 warning.",
    ]
    assert format_report(LintReport()) == "No problems found."
