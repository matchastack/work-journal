from typing import Any

import pytest
from pydantic import ValidationError

from app.schema.changes import (
    AddBullet,
    AddRole,
    AddSkill,
    AddSwap,
    EditBullet,
    ResolveOpenQuestion,
    SetBulletStatus,
    UpdateField,
    change_op_adapter,
)
from app.schema.variant import TitleChoice, Variant


def test_variant_defaults_to_a_one_page_resume() -> None:
    variant = Variant(id="backend", name="Backend", role_type="backend")
    assert variant.audience == "resume"
    assert variant.max_pages == 1
    assert variant.section_order == ("education", "work", "projects", "skills")
    assert not variant.include_summary


def test_variant_rejects_repeated_sections() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        Variant(id="v", name="V", section_order=("work", "skills", "work"))


def test_master_variant_has_no_page_limit() -> None:
    variant = Variant(id="master", name="Master", max_pages=None)
    assert variant.max_pages is None
    assert variant.role_type is None


def test_variant_chooses_titles_and_hides_contact_details() -> None:
    variant = Variant.model_validate(
        {
            "id": "v",
            "name": "V",
            "titles": [{"roleId": "northwind", "title": "Backend Engineer"}],
            "hidden": ["phone"],
        }
    )
    assert variant.titles == (TitleChoice(role_id="northwind", title="Backend Engineer"),)
    assert variant.hidden == ("phone",)
    with pytest.raises(ValidationError):
        Variant.model_validate({"id": "v", "name": "V", "hidden": ["salary"]})


def test_variant_rejects_two_titles_for_one_role() -> None:
    choices = (TitleChoice(role_id="a", title="X"), TitleChoice(role_id="a", title="Y"))
    with pytest.raises(ValidationError, match="title twice"):
        Variant(id="v", name="V", titles=choices)


OPS: list[tuple[dict[str, Any], type]] = [
    (
        {
            "op": "add_bullet",
            "parentId": "northwind",
            "bullet": {"id": "nw_new", "text": "Added retries to the order consumer."},
            "factIds": ["f1"],
        },
        AddBullet,
    ),
    ({"op": "edit_bullet", "bulletId": "nw_orders", "text": "New wording."}, EditBullet),
    (
        {
            "op": "set_bullet_status",
            "bulletId": "nw_orders",
            "status": "benched",
            "reason": "Weak.",
        },
        SetBulletStatus,
    ),
    ({"op": "add_swap", "bulletId": "nw_orders", "swap": {"text": "Alt wording."}}, AddSwap),
    (
        {
            "op": "add_role",
            "role": {"id": "r2", "name": "Org", "position": "Engineer", "startDate": "2026-01"},
        },
        AddRole,
    ),
    (
        {"op": "update_field", "targetId": "northwind", "field": "endDate", "value": "2026-08"},
        UpdateField,
    ),
    ({"op": "add_skill", "skill": {"name": "Go", "category": "Languages"}}, AddSkill),
    (
        {"op": "resolve_open_question", "questionId": "q_nw_volume", "factIds": ["f9"]},
        ResolveOpenQuestion,
    ),
]


@pytest.mark.parametrize(("data", "expected_type"), OPS)
def test_change_ops_are_told_apart_and_round_trip(
    data: dict[str, Any], expected_type: type
) -> None:
    op = change_op_adapter.validate_python(data)
    assert isinstance(op, expected_type)
    assert change_op_adapter.validate_json(change_op_adapter.dump_json(op)) == op


def test_unknown_op_is_rejected() -> None:
    with pytest.raises(ValidationError):
        change_op_adapter.validate_python({"op": "delete_everything"})


def test_benching_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="needs a reason"):
        SetBulletStatus(bullet_id="nw_orders", status="benched")


def test_nested_items_are_validated() -> None:
    with pytest.raises(ValidationError):
        change_op_adapter.validate_python(
            {"op": "add_role", "role": {"id": "r2", "name": "Org", "position": "Engineer"}}
        )
