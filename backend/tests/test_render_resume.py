import re
from pathlib import Path

import pytest

import app.render.resume as resume_module
from app.render.compile import CompiledPdf
from app.render.resume import (
    RenderError,
    StoredRender,
    contact_items,
    render_key,
    render_resume,
)
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
    assert [role.id for role in rendered.selection.work] == ["northwind"], "what the PDF shows"


def test_the_tex_is_the_source_of_the_fitted_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    compiled: list[str] = []

    def compile_latex(source: str, **_: object) -> CompiledPdf:
        """One page per three bullets, so the tiny template's selection needs cuts."""
        compiled.append(source)
        pages = 1 + source.count("\n- ") // 3
        return CompiledPdf(pdf=b"%PDF", pages=pages, text=source, bitmap_fonts=False)

    monkeypatch.setattr(resume_module, "compile_latex", compile_latex)
    rendered = render_resume(selection(template="tiny", max_pages=1), TEMPLATES)
    assert rendered.cuts
    assert rendered.tex == rendered.text  # the fake PDF's text is the source it was given
    assert rendered.tex in compiled
    for cut in rendered.cuts:
        assert cut.text not in rendered.tex


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
    with pytest.raises(RenderError, match=message):
        render_key(selection(template=template), TEMPLATES)


# --- Renders cached by content hash ---------------------------------------------------------------


def with_bullet(chosen: Selection, bullet_id: str, **changes: object) -> Selection:
    """`chosen` with one of its roles' bullets changed."""
    work = tuple(
        role.model_copy(
            update={
                "bullets": tuple(
                    bullet.model_copy(update=changes) if bullet.id == bullet_id else bullet
                    for bullet in role.bullets
                )
            }
        )
        for role in chosen.work
    )
    return chosen.model_copy(update={"work": work})


def fake_compile(source: str, **_: object) -> CompiledPdf:
    """One page per three bullets, so the tiny template's selection needs cuts."""
    pages = 1 + source.count("\n- ") // 3
    return CompiledPdf(pdf=b"%PDF", pages=pages, text=source, bitmap_fonts=False)


def no_compile(source: str, **_: object) -> CompiledPdf:
    raise AssertionError("a stored render isn't compiled again")


def test_the_key_is_the_hash_of_what_the_render_is_made_from(tmp_path: Path) -> None:
    master = selection()
    key = render_key(master)
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert render_key(select(PROFILE, MASTER_VARIANT)) == key, "the same source, the same key"
    assert render_key(with_bullet(master, "nw_export", text="Cut the export job.")) != key
    assert render_key(master.model_copy(update={"template": "tiny"}), TEMPLATES) != key

    tiny = master.model_copy(update={"template": "tiny"})
    (tmp_path / "tiny").mkdir()
    (tmp_path / "tiny" / "resume.tex").write_bytes((TEMPLATES / "tiny" / "resume.tex").read_bytes())
    assert render_key(tiny, tmp_path) == render_key(tiny, TEMPLATES)
    (tmp_path / "tiny" / "resume.cls").write_text("% a class file", encoding="utf-8")
    assert render_key(tiny, tmp_path) != render_key(tiny, TEMPLATES), "other files count too"


def test_with_a_page_limit_the_order_of_cuts_counts_too() -> None:
    """Which bullets fitting cuts depends on their priority, which the source doesn't show."""
    unlimited = selection(template="tiny", max_pages=None)
    limited = selection(template="tiny", max_pages=1)
    assert render_key(unlimited, TEMPLATES) != render_key(limited, TEMPLATES)
    lower = "nw_orders"
    assert render_key(with_bullet(unlimited, lower, priority=3), TEMPLATES) == render_key(
        unlimited, TEMPLATES
    ), "nothing is cut without a limit"
    assert render_key(with_bullet(limited, lower, priority=3), TEMPLATES) != render_key(
        limited, TEMPLATES
    )


def test_a_render_knows_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resume_module, "compile_latex", fake_compile)
    chosen = selection(template="tiny", max_pages=1)
    rendered = render_resume(chosen, TEMPLATES)
    assert (rendered.key, rendered.reused) == (render_key(chosen, TEMPLATES), False)


def test_a_stored_render_is_rebuilt_without_compiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stored PDF, and the same cuts made to the selection, so the source, the cut report
    and what the PDF shows match the first render."""
    chosen = selection(template="tiny", max_pages=1)
    monkeypatch.setattr(resume_module, "compile_latex", fake_compile)
    first = render_resume(chosen, TEMPLATES)
    bullets = sum(cut.kind == "bullet" for cut in first.cuts)
    assert bullets and len(first.cuts) > bullets, "a role or project went with its bullets"

    monkeypatch.setattr(resume_module, "compile_latex", no_compile)
    pdf = (FIXTURES / "pdfs" / "backend-resume.pdf").read_bytes()
    again = render_resume(chosen, TEMPLATES, stored=StoredRender(pdf=pdf, bullet_cuts=bullets))
    assert again.reused
    assert (again.key, again.tex, again.cuts, again.selection) == (
        first.key,
        first.tex,
        first.cuts,
        first.selection,
    )
    assert (again.pdf, again.pages, again.warnings) == (pdf, 1, ())
    assert "Casey Morgan" in again.text


def test_a_stored_render_without_cuts_is_the_whole_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resume_module, "compile_latex", no_compile)
    pdf = (FIXTURES / "pdfs" / "backend-resume.pdf").read_bytes()
    chosen = selection()
    rendered = render_resume(chosen, stored=StoredRender(pdf=pdf, bullet_cuts=0))
    assert (rendered.cuts, rendered.selection, rendered.key) == ((), chosen, render_key(chosen))
    assert "\\documentclass" in rendered.tex
