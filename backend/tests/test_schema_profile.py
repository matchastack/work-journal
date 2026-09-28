import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.schema.profile import Bullet, Education, Profile, Role

FIXTURE = Path(__file__).parent / "fixtures" / "profile.json"


def fixture_data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def test_fixture_profile_validates() -> None:
    profile = Profile.model_validate(fixture_data())
    assert profile.basics.name == "Casey Morgan"
    assert [role.id for role in profile.work] == ["northwind", "contoso"]
    assert profile.work[0].load_bearing
    assert profile.work[0].end_date is None


def test_round_trip_through_json_is_lossless() -> None:
    profile = Profile.model_validate(fixture_data())
    reloaded = Profile.model_validate_json(profile.model_dump_json())
    assert reloaded == profile


def test_dump_uses_json_resume_style_names() -> None:
    dumped = Profile.model_validate(fixture_data()).model_dump(exclude_defaults=True)
    role = dumped["work"][0]
    assert {"startDate", "titleVariants", "loadBearing", "highlights"} <= role.keys()
    assert "studyType" in dumped["education"][0]
    assert "openQuestions" in dumped


def test_defaults_fill_in_optional_metadata() -> None:
    bullet = Bullet(id="b1", text="Shipped the thing.")
    assert bullet.strength == "medium"
    assert bullet.verification == "no"
    assert bullet.status == "active"
    assert bullet.priority == 2
    assert bullet.visibility == "public"


def test_phone_is_hidden_from_public_page_by_default() -> None:
    profile = Profile.model_validate(fixture_data())
    assert profile.basics.phone_visibility == "resume_only"
    assert profile.basics.email_visibility == "public"


def test_bullets_blocked_by_an_open_question() -> None:
    profile = Profile.model_validate(fixture_data())
    assert [bullet.id for bullet in profile.bullets_blocked_by("q_nw_volume")] == ["nw_orders"]
    assert profile.bullets_blocked_by("q_project_dates") == ()


def test_all_bullets_covers_roles_education_and_projects() -> None:
    profile = Profile.model_validate(fixture_data())
    ids = [bullet.id for bullet in profile.all_bullets()]
    assert ids[0] == "nw_orders"
    assert {"ss_minor", "rb_app", "bc_model"} <= set(ids)


def test_duplicate_ids_are_rejected() -> None:
    data = fixture_data()
    data["projects"][0]["highlights"][0]["id"] = "nw_orders"
    with pytest.raises(ValidationError, match="unique across the profile"):
        Profile.model_validate(data)


def test_benched_bullet_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="needs a status reason"):
        Bullet(id="b1", text="Used a ticket tracker.", status="benched")


def test_role_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="ends before it starts"):
        Role(id="r1", name="Org", position="Engineer", start_date="2025-06", end_date="2025-01")


def test_coursework_subset_must_use_listed_courses() -> None:
    with pytest.raises(ValidationError, match="not in the full list"):
        Education.model_validate(
            {
                "id": "e1",
                "institution": "Uni",
                "studyType": "BSc",
                "area": "CS",
                "startDate": "2020-08",
                "courses": ["Algorithms"],
                "courseworkSubsets": [{"roleType": "backend", "courses": ["Compilers"]}],
            }
        )


def test_skill_cannot_be_claimed_and_listed_as_a_gap() -> None:
    data = fixture_data()
    data["skillGaps"].append("python")
    with pytest.raises(ValidationError, match="both in the catalogue and gaps"):
        Profile.model_validate(data)


def test_presets_must_use_defined_role_types() -> None:
    data = fixture_data()
    data["skillPresets"][0]["roleType"] = "ml"
    with pytest.raises(ValidationError, match="undefined role types"):
        Profile.model_validate(data)


def test_unknown_fields_are_rejected() -> None:
    data = fixture_data()
    data["work"][0]["salary"] = "secret"
    with pytest.raises(ValidationError):
        Profile.model_validate(data)
