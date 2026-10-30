"""The owner's edits on the Profile page, as change operations (FR-PRF-11, FR-PRF-4)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.profile.edits import (
    BasicsEdit,
    BulletEdit,
    Edit,
    EducationEdit,
    MissingTarget,
    ProjectEdit,
    RoleEdit,
    basics_changes,
    bullet_changes,
    education_changes,
    project_changes,
    role_changes,
)
from app.profile_ops import apply
from app.schema.changes import EditBullet, SetBulletStatus, UpdateField
from app.schema.common import Model
from app.schema.profile import Location, Profile, SocialProfile

PROFILE = Profile.model_validate_json(
    (Path(__file__).parent / "fixtures" / "profile.json").read_text(encoding="utf-8")
)
NORTHWIND, CONTOSO = PROFILE.work
SPRINGFIELD = PROFILE.education[0]
RECIPE_BOX, BIRD_CALLS = PROFILE.projects
ORDERS, EXPORT, TRACKER = NORTHWIND.highlights


def body[E: Edit](edit: type[E], current: Model, **changes: Any) -> E:
    """An edit that sends `current`'s fields, with `changes`, as the page's form does."""
    fields = set(edit.model_fields) - {"base_version"}
    sent = current.model_dump(mode="json", by_alias=False, include=fields)
    return edit.model_validate({"base_version": 1, **sent, **changes})


def test_an_edit_that_changes_nothing_makes_no_operations() -> None:
    assert basics_changes(PROFILE, body(BasicsEdit, PROFILE.basics)).ops == ()
    assert role_changes(PROFILE, "northwind", body(RoleEdit, NORTHWIND)).ops == ()
    education = body(EducationEdit, SPRINGFIELD)
    assert education_changes(PROFILE, "springfield_state", education).ops == ()
    assert project_changes(PROFILE, "bird_calls", body(ProjectEdit, BIRD_CALLS)).ops == ()
    for bullet in NORTHWIND.highlights:
        assert bullet_changes(PROFILE, bullet.id, body(BulletEdit, bullet)).ops == ()


def test_reworded_bullet_text_is_an_edit_bullet() -> None:
    edit = body(BulletEdit, EXPORT, text="Cut the nightly export from 50 to 12 minutes.")
    edited = bullet_changes(PROFILE, "nw_export", edit)
    assert edited.ops == (
        EditBullet(bullet_id="nw_export", text="Cut the nightly export from 50 to 12 minutes."),
    )
    assert edited.summary == "Edited a bullet under Engineer, Northwind Traders"


def test_benching_a_bullet_needs_a_reason() -> None:
    edit = body(BulletEdit, EXPORT, status="benched", status_reason="Older work.")
    assert bullet_changes(PROFILE, "nw_export", edit).ops == (
        SetBulletStatus(bullet_id="nw_export", status="benched", reason="Older work."),
    )
    with pytest.raises(ValidationError, match="benching needs a reason"):
        body(BulletEdit, EXPORT, status="benched", status_reason="  ")


def test_switching_to_active_clears_the_reason() -> None:
    edit = body(BulletEdit, TRACKER, status="active", status_reason="Tool usage.")
    assert bullet_changes(PROFILE, "nw_tracker", edit).ops == (
        SetBulletStatus(bullet_id="nw_tracker", status="active", reason=None),
    )
    planned = body(BulletEdit, EXPORT, status="planned", status_reason="")
    assert bullet_changes(PROFILE, "nw_export", planned).ops == (
        SetBulletStatus(bullet_id="nw_export", status="planned", reason=None),
    )


def test_a_bullets_other_fields_are_field_updates_that_apply() -> None:
    edit = body(
        BulletEdit,
        ORDERS,
        strength="medium",
        verification="yes",
        verification_note=None,
        tags=["backend", "data"],
    )
    ops = bullet_changes(PROFILE, "nw_orders", edit).ops
    assert ops == (
        UpdateField(target_id="nw_orders", field="strength", value="medium"),
        UpdateField(target_id="nw_orders", field="verification", value="yes"),
        UpdateField(target_id="nw_orders", field="verificationNote", value=None),
        UpdateField(target_id="nw_orders", field="tags", value=["backend", "data"]),
    )
    bullet = apply(PROFILE, ops).work[0].highlights[0]
    assert (bullet.strength, bullet.verification, bullet.verification_note, bullet.tags) == (
        "medium",
        "yes",
        None,
        ("backend", "data"),
    )


def test_bullet_text_and_tags_follow_the_rules() -> None:
    with pytest.raises(ValidationError):
        body(BulletEdit, EXPORT, text=" ")
    with pytest.raises(ValidationError):
        body(BulletEdit, EXPORT, text="x" * 501)
    with pytest.raises(ValidationError):
        body(BulletEdit, EXPORT, tags=["Back end"])
    with pytest.raises(ValidationError):
        body(BulletEdit, EXPORT, tags=[f"t{n}" for n in range(21)])


def test_typed_characters_become_ascii() -> None:
    edit = body(
        BulletEdit,
        EXPORT,
        text="Cut the export from 50\N{EN DASH}12 minutes\N{HORIZONTAL ELLIPSIS}",
    )
    (op,) = bullet_changes(PROFILE, "nw_export", edit).ops
    assert op == EditBullet(bullet_id="nw_export", text="Cut the export from 50-12 minutes...")


def test_blank_basics_are_cleared() -> None:
    edit = body(
        BasicsEdit,
        PROFILE.basics,
        label="  ",
        phone="",
        location={"city": " ", "region": "", "country_code": None},
        profiles=[
            {"network": "GitHub", "username": "casey-example", "url": ""},
            {"network": "LinkedIn", "username": None, "url": "https://example.com/in/casey"},
        ],
    )
    assert (edit.label, edit.phone, edit.location) == (None, None, None)
    ops = basics_changes(PROFILE, edit).ops
    assert ops == (
        UpdateField(target_id="basics", field="label", value=None),
        UpdateField(target_id="basics", field="phone", value=None),
        UpdateField(target_id="basics", field="location", value=None),
        UpdateField(
            target_id="basics",
            field="profiles",
            value=[
                {"network": "GitHub", "username": "casey-example", "url": None},
                {"network": "LinkedIn", "username": None, "url": "https://example.com/in/casey"},
            ],
        ),
    )
    basics = apply(PROFILE, ops).basics
    assert basics.location is None
    assert basics.profiles[1] == SocialProfile(
        network="LinkedIn", url="https://example.com/in/casey"
    )


def test_a_location_with_one_field_stays() -> None:
    edit = body(
        BasicsEdit, PROFILE.basics, location={"city": "", "region": "", "country_code": "US"}
    )
    assert edit.location == Location(country_code="US")
    assert basics_changes(PROFILE, edit).ops == (
        UpdateField(
            target_id="basics",
            field="location",
            value={"city": None, "region": None, "countryCode": "US"},
        ),
    )


def test_a_role_ends_after_it_starts() -> None:
    edit = body(RoleEdit, CONTOSO, end_date="", position="Research Assistant")
    edited = role_changes(PROFILE, "contoso", edit)
    assert edited.ops == (
        UpdateField(target_id="contoso", field="position", value="Research Assistant"),
        UpdateField(target_id="contoso", field="endDate", value=None),
    )
    assert edited.summary == "Edited the role Research Assistant, Contoso Research"
    with pytest.raises(ValidationError, match="end date is before the start date"):
        body(RoleEdit, CONTOSO, end_date="2023-12")
    with pytest.raises(ValidationError):
        body(RoleEdit, CONTOSO, start_date="2024-13")


def test_an_education_entry_ends_after_it_starts() -> None:
    edit = body(EducationEdit, SPRINGFIELD, honours="", area="Software Engineering")
    edited = education_changes(PROFILE, "springfield_state", edit)
    assert edited.ops == (
        UpdateField(target_id="springfield_state", field="area", value="Software Engineering"),
        UpdateField(target_id="springfield_state", field="honours", value=None),
    )
    assert edited.summary == (
        "Edited the education entry Bachelor of Science in Software Engineering, "
        "Springfield State University"
    )
    with pytest.raises(ValidationError, match="end date is before the start date"):
        body(EducationEdit, SPRINGFIELD, end_date="2019-01")


def test_a_projects_status_tech_stack_and_dates() -> None:
    edit = body(
        ProjectEdit,
        RECIPE_BOX,
        status="benched",
        status_reason="Superseded.",
        keywords=["TypeScript", "React", "SQLite"],
        tags=[],
    )
    edited = project_changes(PROFILE, "recipe_box", edit)
    assert edited.ops == (
        UpdateField(
            target_id="recipe_box", field="keywords", value=["TypeScript", "React", "SQLite"]
        ),
        UpdateField(target_id="recipe_box", field="status", value="benched"),
        UpdateField(target_id="recipe_box", field="statusReason", value="Superseded."),
        UpdateField(target_id="recipe_box", field="tags", value=[]),
    )
    assert edited.summary == "Edited the project Recipe Box"
    project = apply(PROFILE, edited.ops).projects[0]
    assert (project.status, project.status_reason) == ("benched", "Superseded.")
    activated = body(ProjectEdit, BIRD_CALLS, status="active", status_reason="Not built yet.")
    assert project_changes(PROFILE, "bird_calls", activated).ops == (
        UpdateField(target_id="bird_calls", field="status", value="active"),
        UpdateField(target_id="bird_calls", field="statusReason", value=None),
    )
    with pytest.raises(ValidationError, match="benching needs a reason"):
        body(ProjectEdit, RECIPE_BOX, status="benched", status_reason=None)
    with pytest.raises(ValidationError, match="end date is before the start date"):
        body(ProjectEdit, RECIPE_BOX, end_date="2023-01")


def test_an_id_of_another_kind_isnt_found() -> None:
    with pytest.raises(MissingTarget, match="no role springfield_state"):
        role_changes(PROFILE, "springfield_state", body(RoleEdit, NORTHWIND))
    with pytest.raises(MissingTarget, match="no education entry northwind"):
        education_changes(PROFILE, "northwind", body(EducationEdit, SPRINGFIELD))
    with pytest.raises(MissingTarget, match="no project northwind"):
        project_changes(PROFILE, "northwind", body(ProjectEdit, RECIPE_BOX))
    with pytest.raises(MissingTarget, match="no bullet northwind"):
        bullet_changes(PROFILE, "northwind", body(BulletEdit, EXPORT))


def test_every_field_is_required() -> None:
    sent = body(RoleEdit, CONTOSO).model_dump(mode="json")
    del sent["summary"]
    with pytest.raises(ValidationError, match="summary"):
        RoleEdit.model_validate(sent)
    with pytest.raises(ValidationError):
        body(RoleEdit, CONTOSO, base_version=0)
