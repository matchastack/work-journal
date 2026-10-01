import math

import pytest

from app.render.compile import CompiledPdf
from app.render.fit import Cut, FitError, fit
from app.schema.profile import Strength
from app.selection import (
    Contact,
    SelectedBullet,
    SelectedEducation,
    SelectedProject,
    SelectedRole,
    Selection,
)


def bullet(bullet_id: str, priority: int = 2, strength: Strength = "medium") -> SelectedBullet:
    return SelectedBullet(
        id=bullet_id, text=f"Text of {bullet_id}.", strength=strength, priority=priority
    )


def role(
    role_id: str, *bullets: SelectedBullet, load_bearing: bool = False, priority: int = 2
) -> SelectedRole:
    return SelectedRole(
        id=role_id,
        organisation=f"Org {role_id}",
        title="Engineer",
        start_date="2023-01",
        bullets=bullets,
        load_bearing=load_bearing,
        priority=priority,
    )


def project(project_id: str, *bullets: SelectedBullet, priority: int = 2) -> SelectedProject:
    return SelectedProject(
        id=project_id, name=f"Project {project_id}", bullets=bullets, priority=priority
    )


def selection(
    work: tuple[SelectedRole, ...] = (),
    projects: tuple[SelectedProject, ...] = (),
    education: tuple[SelectedEducation, ...] = (),
    max_pages: int | None = 1,
) -> Selection:
    return Selection(
        variant_id="backend",
        audience="resume",
        contact=Contact(name="Casey Morgan"),
        sections=("education", "work", "projects", "skills"),
        work=work,
        projects=projects,
        education=education,
        max_pages=max_pages,
        template="default",
    )


class Pages:
    """A stand-in for LaTeX: a page holds `per_page` bullets. Counts the renders."""

    def __init__(self, per_page: int) -> None:
        self.per_page = per_page
        self.renders = 0

    def __call__(self, chosen: Selection) -> CompiledPdf:
        self.renders += 1
        count = sum(
            len(item.bullets) for item in (*chosen.work, *chosen.projects, *chosen.education)
        )
        pages = max(1, math.ceil(count / self.per_page))
        return CompiledPdf(pdf=b"%PDF", pages=pages, text="text", bitmap_fonts=False)


def cut_ids(cuts: tuple[Cut, ...]) -> list[str]:
    return [cut.id for cut in cuts]


def test_a_selection_that_fits_is_rendered_once() -> None:
    pages = Pages(per_page=5)
    fitted = fit(selection(work=(role("a", bullet("a1"), bullet("a2")),)), pages)
    assert (fitted.cuts, fitted.pdf.pages, pages.renders) == ((), 1, 1)


def test_without_a_page_limit_nothing_is_cut() -> None:
    chosen = selection(work=(role("a", *(bullet(f"a{i}") for i in range(9))),), max_pages=None)
    assert fit(chosen, Pages(per_page=2)).cuts == ()


def test_the_lowest_value_bullets_are_cut_first() -> None:
    chosen = selection(
        work=(
            role(
                "a",
                bullet("keep", priority=1, strength="high"),
                bullet("weak", priority=2, strength="low"),
                bullet("last", priority=2, strength="medium"),
                bullet("first", priority=3, strength="high"),
            ),
        ),
    )
    fitted = fit(chosen, Pages(per_page=1))
    assert cut_ids(fitted.cuts) == ["first", "weak", "last"]
    assert [b.id for b in fitted.selection.work[0].bullets] == ["keep"]


def test_ties_go_to_the_lower_priority_item_then_the_later_bullet() -> None:
    chosen = selection(
        work=(role("main", bullet("m1"), bullet("m2"), priority=1),),
        projects=(project("side", bullet("s1"), bullet("s2"), priority=3),),
    )
    assert cut_ids(fit(chosen, Pages(per_page=2)).cuts) == ["s2", "s1", "side"]


def test_only_the_fewest_cuts_are_made_with_few_renders() -> None:
    chosen = selection(work=(role("a", *(bullet(f"a{i}") for i in range(30))),))
    pages = Pages(per_page=24)
    fitted = fit(chosen, pages)
    assert len(fitted.cuts) == 6
    assert fitted.pdf.pages == 1
    assert pages.renders <= 7  # a binary search, not one render per cut


def test_a_role_with_no_bullets_left_is_dropped() -> None:
    chosen = selection(
        work=(role("main", bullet("m1", priority=1)), role("old", bullet("o1", priority=3))),
    )
    fitted = fit(chosen, Pages(per_page=1))
    assert [(cut.kind, cut.id) for cut in fitted.cuts] == [("bullet", "o1"), ("role", "old")]
    assert [item.id for item in fitted.selection.work] == ["main"]


def test_a_load_bearing_role_keeps_its_best_bullet() -> None:
    chosen = selection(
        work=(
            role(
                "current",
                bullet("c1", strength="low"),
                bullet("c2", strength="high"),
                load_bearing=True,
            ),
            role("other", bullet("o1")),
        ),
    )
    fitted = fit(chosen, Pages(per_page=1))
    assert [item.id for item in fitted.selection.work] == ["current"]
    assert [b.id for b in fitted.selection.work[0].bullets] == ["c2"]


def test_education_stays_when_its_bullets_are_cut() -> None:
    school = SelectedEducation(
        id="uni",
        institution="Springfield State University",
        study_type="Bachelor of Science",
        area="Computer Science",
        start_date="2020-08",
        bullets=(bullet("u1", priority=3),),
    )
    chosen = selection(work=(role("a", bullet("a1", priority=1)),), education=(school,))
    fitted = fit(chosen, Pages(per_page=1))
    assert cut_ids(fitted.cuts) == ["u1"]
    assert fitted.selection.education[0].bullets == ()


def test_what_must_stay_can_overflow_the_limit() -> None:
    chosen = selection(
        work=(
            role("one", bullet("x1"), load_bearing=True),
            role("two", bullet("y1"), load_bearing=True),
        ),
    )
    with pytest.raises(FitError, match="takes 2 pages even after cutting every bullet it can"):
        fit(chosen, Pages(per_page=1))
