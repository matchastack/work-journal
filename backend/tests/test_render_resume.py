from pathlib import Path

import pytest

from app.render.resume import RenderError, contact_items, render_resume
from app.schema.profile import Profile, SocialProfile
from app.schema.variant import Variant
from app.selection import MASTER_VARIANT, Contact, Selection, select

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
VARIANTS = {
    "master": MASTER_VARIANT,
    **{
        role.id: Variant(id=role.id, name=role.name, role_type=role.id)
        for role in PROFILE.role_types
    },
}
TEMPLATES = FIXTURES / "templates"


def selection(variant: str = "master", **changes: object) -> Selection:
    return select(PROFILE, VARIANTS[variant]).model_copy(update=changes)


def test_contact_items_in_resume_order() -> None:
    contact = Contact(
        name="Casey Morgan",
        email="casey@example.com",
        phone="+1 555 0100",
        location="Springfield, US",
        url="https://www.example.com/",
        profiles=(
            SocialProfile(network="GitHub", url="https://github.com/casey-example"),
            SocialProfile(network="Mastodon", username="@casey"),
        ),
    )
    assert contact_items(contact) == [
        ("+1 555 0100", None),
        ("casey@example.com", "mailto:casey@example.com"),
        ("Springfield, US", None),
        ("example.com", "https://www.example.com/"),
        ("github.com/casey-example", "https://github.com/casey-example"),
        ("Mastodon: @casey", None),
    ]


@pytest.mark.latex
@pytest.mark.parametrize("variant", VARIANTS.values(), ids=lambda variant: variant.id)
def test_the_default_template_renders_each_default_variant(variant: Variant) -> None:
    chosen = select(PROFILE, variant)
    rendered = render_resume(chosen)
    assert (rendered.pages, rendered.cuts, rendered.warnings) == (1, (), ())
    assert rendered.pdf.startswith(b"%PDF")
    assert "Casey Morgan" in rendered.text
    for role in chosen.work:
        assert role.organisation in rendered.text
        for bullet in role.bullets:
            assert bullet.text[:30] in " ".join(rendered.text.split())


@pytest.mark.latex
def test_the_default_template_is_the_owners_layout() -> None:
    text = " ".join(render_resume(selection()).text.split())
    headings = ["Education", "Work Experience", "Projects", "Technical Skills"]
    assert [text.find(heading) for heading in headings] == sorted(
        text.find(heading) for heading in headings
    )
    for line in (
        "+1 555 0100 | casey@example.com | Springfield, US | example.com | github.com/casey",
        "Springfield State University Aug 2020 - May 2024",
        "Bachelor of Science in Computer Science, Magna Cum Laude",
        "Relevant coursework: Algorithms, Operating Systems",
        "Software Engineer, Northwind Traders Aug 2025 - Present",
        "Recipe Box (TypeScript, React, PostgreSQL) Jun 2023 - Sep 2023",
        "Languages: Python, TypeScript, SQL, Kotlin",
    ):
        assert line in text


@pytest.mark.latex
def test_special_characters_print_as_written() -> None:
    text = "Cut R&D costs 40% ($2k) for team #3: {braces}, a\\b, x<y>z|w, 3\N{MULTIPLICATION SIGN}"
    chosen = selection()
    role = chosen.work[0]
    bullets = (role.bullets[0].model_copy(update={"text": text}),)
    rendered = render_resume(
        chosen.model_copy(update={"work": (role.model_copy(update={"bullets": bullets}),)})
    )
    squashed = "".join(rendered.text.split())
    for fragment in (
        "R&D",
        "40%",
        "($2k)",
        "#3",
        "{braces}",
        "a\\b",
        "x<y>z|w",
        "3\N{MULTIPLICATION SIGN}",
    ):
        assert fragment in squashed
    assert rendered.warnings == ()


@pytest.mark.latex
def test_a_resume_over_its_page_limit_is_cut_to_fit() -> None:
    rendered = render_resume(selection(template="tiny", max_pages=1), TEMPLATES)
    assert rendered.pages == 1
    assert [(cut.kind, cut.id) for cut in rendered.cuts] == [
        ("bullet", "ct_datasets"),
        ("bullet", "rb_app"),
        ("bullet", "ct_sensors"),
        ("role", "contoso"),
        ("project", "recipe_box"),
    ]
    assert "Northwind Traders" in rendered.text  # the load-bearing role stays


@pytest.mark.latex
def test_without_a_page_limit_nothing_is_cut() -> None:
    rendered = render_resume(selection(template="tiny", max_pages=None), TEMPLATES)
    assert rendered.pages == 2
    assert rendered.cuts == ()


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("missing", "no template 'missing'"),
        ("broken", "the broken template is broken"),
        ("prints_none", "the prints_none template failed: the template printed a missing value"),
    ],
)
def test_template_problems_are_render_errors(template: str, message: str) -> None:
    with pytest.raises(RenderError, match=message):
        render_resume(selection(template=template), TEMPLATES)
