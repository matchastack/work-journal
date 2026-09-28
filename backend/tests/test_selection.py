from pathlib import Path
from typing import Any

import pytest

from app.schema.profile import Profile, RoleType
from app.schema.variant import TitleChoice, Variant
from app.selection import Selection, default_variants, select

PROFILE = Profile.model_validate_json(
    (Path(__file__).parent / "fixtures" / "profile.json").read_text(encoding="utf-8")
)


def variant(**fields: Any) -> Variant:
    return Variant.model_validate({"id": "v", "name": "V", **fields})


def bullet_ids(selection: Selection) -> list[str]:
    items = [*selection.education, *selection.work, *selection.projects]
    return [bullet.id for item in items for bullet in item.bullets]


def test_benched_and_planned_items_never_appear() -> None:
    selection = select(PROFILE, variant())
    assert "nw_tracker" not in bullet_ids(selection)
    assert [project.id for project in selection.projects] == ["recipe_box"]


def test_roles_cut_for_the_role_type_are_left_out() -> None:
    assert [role.id for role in select(PROFILE, variant(role_type="backend")).work] == ["northwind"]
    assert [role.id for role in select(PROFILE, variant(role_type="data")).work] == [
        "northwind",
        "contoso",
    ]


def test_load_bearing_roles_always_appear() -> None:
    northwind = PROFILE.work[0].model_copy(update={"cut_for": ("backend",)})
    profile = PROFILE.model_copy(update={"work": (northwind, *PROFILE.work[1:])})
    assert [role.id for role in select(profile, variant(role_type="backend")).work] == ["northwind"]


def test_items_kept_for_the_role_type_rank_first() -> None:
    data = select(PROFILE, variant(role_type="data"))
    assert [(role.id, role.priority) for role in data.work] == [("northwind", 1), ("contoso", 1)]
    assert [(p.id, p.priority) for p in data.projects] == [("recipe_box", 2)]
    backend = select(PROFILE, variant(role_type="backend"))
    assert [(p.id, p.priority) for p in backend.projects] == [("recipe_box", 1)]


def test_bullet_strength_follows_the_role_type() -> None:
    def strength(role_type: str | None) -> str:
        contoso = select(PROFILE, variant(role_type=role_type)).work[-1]
        return next(b.strength for b in contoso.bullets if b.id == "ct_sensors")

    assert strength(None) == "medium"
    assert strength("data") == "high"


def test_listed_tags_filter_tagged_items_only() -> None:
    selection = select(PROFILE, variant(include_tags=("web",)))
    assert "nw_orders" not in bullet_ids(selection)
    assert "nw_export" in bullet_ids(selection)
    assert [project.id for project in selection.projects] == ["recipe_box"]
    assert [project.id for project in select(PROFILE, variant(include_tags=("ml",))).projects] == []


def test_titles_come_from_the_approved_variants() -> None:
    def title(*choices: TitleChoice) -> tuple[str, tuple[str, ...]]:
        selection = select(PROFILE, variant(titles=choices))
        return selection.work[0].title, selection.notes

    assert title() == ("Software Engineer", ())
    assert title(TitleChoice(role_id="northwind", title="Backend Engineer")) == (
        "Backend Engineer",
        (),
    )
    assert title(TitleChoice(role_id="northwind", title="Engineer")) == ("Engineer", ())
    shown, notes = title(TitleChoice(role_id="northwind", title="Senior Engineer"))
    assert shown == "Software Engineer"
    assert notes == (
        '"Senior Engineer" isn\'t an approved title for northwind, so it shows "Software Engineer"',
    )
    assert title(TitleChoice(role_id="nowhere", title="Engineer"))[1] == (
        "the title choice for nowhere names no role",
    )


def test_coursework_follows_the_role_type() -> None:
    [master] = select(PROFILE, variant()).education
    assert len(master.courses) == 5
    [backend] = select(PROFILE, variant(role_type="backend")).education
    assert backend.courses == ("Algorithms", "Operating Systems", "Distributed Systems")
    other = select(PROFILE, variant(role_type="frontend"))
    assert other.education[0].courses == ()
    assert "no coursework subset for frontend in springfield_state" in other.notes


def test_skills_come_from_the_preset_or_every_skill_by_category() -> None:
    backend = select(PROFILE, variant(role_type="backend"))
    assert [line.label for line in backend.skills] == ["Languages", "Backend & APIs", "Databases"]
    master = select(PROFILE, variant())
    assert [line.label for line in master.skills] == [
        "Languages", "Backend & APIs", "Frontend", "Databases", "Data",
    ]  # fmt: skip
    assert master.skills[0].skills == ("Python", "TypeScript", "SQL", "Kotlin")
    assert "no skills preset for frontend, so every skill is listed" in (
        select(PROFILE, variant(role_type="frontend")).notes
    )


def test_a_summary_shows_only_when_asked_for() -> None:
    assert select(PROFILE, variant(role_type="data")).summary is None
    selection = select(PROFILE, variant(role_type="data", include_summary=True))
    assert selection.summary == "Engineer who builds reliable data pipelines and tooling."
    assert selection.sections == ("summary", "education", "work", "projects", "skills")
    missing = select(PROFILE, variant(include_summary=True))
    assert (missing.summary, missing.sections[0]) == (None, "education")
    assert missing.notes == ("no summary for the master variant, so none is shown",)


def test_section_order_can_be_changed() -> None:
    order = ("skills", "work", "summary", "education")
    selection = select(
        PROFILE, variant(role_type="backend", section_order=order, include_summary=True)
    )
    assert selection.sections == order


def test_visibility_follows_the_audience_and_the_variant() -> None:
    resume = select(PROFILE, variant()).contact
    assert (resume.phone, resume.email, resume.location) == (
        "+1 555 0100",
        "casey@example.com",
        "Springfield, US",
    )
    assert select(PROFILE, variant(audience="web")).contact.phone is None
    hidden = select(PROFILE, variant(hidden=("phone", "profiles"))).contact
    assert (hidden.phone, hidden.profiles, hidden.email) == (None, (), "casey@example.com")


def test_private_and_resume_only_bullets() -> None:
    orders, export = PROFILE.work[0].highlights[:2]
    northwind = PROFILE.work[0].model_copy(
        update={
            "highlights": (
                orders.model_copy(update={"visibility": "private"}),
                export.model_copy(update={"visibility": "resume_only"}),
            )
        }
    )
    profile = PROFILE.model_copy(update={"work": (northwind,)})
    assert bullet_ids(select(profile, variant())) == ["ss_minor", "nw_export", "rb_app"]
    assert bullet_ids(select(profile, variant(audience="web"))) == ["ss_minor", "rb_app"]


def test_default_variants_are_the_master_plus_one_per_role_type() -> None:
    variants = default_variants(PROFILE)
    assert [(v.id, v.name, v.role_type, v.max_pages) for v in variants] == [
        ("master", "Master (everything)", None, None),
        ("backend", "Backend / full stack", "backend", 1),
        ("data", "Data engineering", "data", 1),
    ]
    clash = PROFILE.model_copy(update={"role_types": (RoleType(id="master", name="Master"),)})
    assert [v.id for v in default_variants(clash)] == ["master", "master_resume"]


@pytest.mark.parametrize("role_type", [None, "backend", "data"])
def test_selection_is_valid_json(role_type: str | None) -> None:
    selection = select(PROFILE, variant(role_type=role_type))
    assert Selection.model_validate_json(selection.model_dump_json()) == selection
