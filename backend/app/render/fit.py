"""Fit a selection to its page limit by cutting its lowest-value bullets (FR-RES-7, R6, R7).

The template's fonts and margins never change: only content is cut. Bullets are cut in a fixed
order, lowest value first:
1. the bullet's priority, 3 first;
2. its strength for the variant's role type, low first;
3. its role's or project's priority, 3 first;
4. its place in the document, last first.

A role or project whose bullets are all cut is dropped. A load-bearing role keeps its most valuable
bullet (R7), and education entries always stay. Page counts only fall as content is cut, so a
binary search finds the fewest cuts that fit with a handful of compiles.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.render.compile import CompiledPdf
from app.schema.common import Id
from app.selection import SelectedBullet, SelectedProject, SelectedRole, Selection

_STRENGTH_RANK = {"low": 0, "medium": 1, "high": 2}
ItemSection = Literal["work", "projects", "education"]
_SECTIONS: tuple[ItemSection, ...] = ("work", "projects", "education")


@dataclass(frozen=True)
class Cut:
    """Content left out to fit the page limit: a bullet, or a role or project with none left."""

    kind: Literal["bullet", "role", "project"]
    id: Id
    text: str


@dataclass(frozen=True)
class Fitted:
    selection: Selection
    pdf: CompiledPdf
    cuts: tuple[Cut, ...]


class FitError(Exception):
    """The selection can't fit its page limit without cutting what must stay."""


@dataclass(frozen=True)
class BulletRef:
    """A bullet by position: the section, the item within it, and the bullet within that."""

    section: ItemSection
    item: int
    bullet: int


def fit(selection: Selection, render: Callable[[Selection], CompiledPdf]) -> Fitted:
    """Render `selection`, cutting the fewest lowest-value bullets needed to fit its page limit."""
    rendered = {0: render(selection)}
    limit = selection.max_pages
    if limit is None or rendered[0].pages <= limit:
        return Fitted(selection=selection, pdf=rendered[0], cuts=())
    order = cut_order(selection)

    def pages_after(count: int) -> int:
        if count not in rendered:
            rendered[count] = render(apply_cuts(selection, order[:count])[0])
        return rendered[count].pages

    low, high = 1, len(order)
    while low < high:
        middle = (low + high) // 2
        if pages_after(middle) <= limit:
            high = middle
        else:
            low = middle + 1
    if not order or pages_after(low) > limit:
        pages = rendered[max(rendered)].pages
        raise FitError(
            f"{selection.variant_id} takes {pages} pages even after cutting every bullet it can; "
            f"the limit is {limit}"
        )
    fitted, cuts = apply_cuts(selection, order[:low])
    return Fitted(selection=fitted, pdf=rendered[low], cuts=cuts)


def cut_order(selection: Selection) -> list[BulletRef]:
    """The bullets that may be cut, lowest value first."""
    ranked: list[tuple[tuple[int, int, int, int], BulletRef]] = []
    position = 0
    for section in _SECTIONS:
        for item_index, (priority, bullets, keep_one) in enumerate(_items(selection, section)):
            keys = [
                (
                    -bullet.priority,
                    _STRENGTH_RANK[bullet.strength],
                    -priority,
                    -(position + bullet_index),
                )
                for bullet_index, bullet in enumerate(bullets)
            ]
            kept = max(range(len(keys)), key=lambda i: keys[i]) if keep_one and keys else None
            ranked += [
                (key, BulletRef(section, item_index, bullet_index))
                for bullet_index, key in enumerate(keys)
                if bullet_index != kept
            ]
            position += len(bullets)
    ranked.sort(key=lambda entry: entry[0])
    return [candidate for _, candidate in ranked]


def apply_cuts(
    selection: Selection, candidates: list[BulletRef]
) -> tuple[Selection, tuple[Cut, ...]]:
    """The selection without the given bullets, and a report of what was cut, in order."""
    removed: set[tuple[ItemSection, int, int]] = {(c.section, c.item, c.bullet) for c in candidates}
    cuts = [_bullet_cut(selection, c) for c in candidates]
    work: list[SelectedRole] = []
    for index, role in enumerate(selection.work):
        bullets = _kept(role.bullets, "work", index, removed)
        if bullets or not role.bullets or role.load_bearing:
            work.append(role.model_copy(update={"bullets": bullets}))
        else:
            cuts.append(Cut(kind="role", id=role.id, text=role.organisation))
    projects: list[SelectedProject] = []
    for index, project in enumerate(selection.projects):
        bullets = _kept(project.bullets, "projects", index, removed)
        if bullets or not project.bullets:
            projects.append(project.model_copy(update={"bullets": bullets}))
        else:
            cuts.append(Cut(kind="project", id=project.id, text=project.name))
    education = [
        school.model_copy(update={"bullets": _kept(school.bullets, "education", index, removed)})
        for index, school in enumerate(selection.education)
    ]
    fitted = selection.model_copy(
        update={"work": tuple(work), "projects": tuple(projects), "education": tuple(education)}
    )
    return fitted, tuple(cuts)


def _items(
    selection: Selection, section: ItemSection
) -> list[tuple[int, tuple[SelectedBullet, ...], bool]]:
    """Each item's priority, bullets, and whether it must keep one bullet."""
    if section == "work":
        return [(role.priority, role.bullets, role.load_bearing) for role in selection.work]
    if section == "projects":
        return [(project.priority, project.bullets, False) for project in selection.projects]
    return [(1, school.bullets, False) for school in selection.education]


def _kept(
    bullets: tuple[SelectedBullet, ...],
    section: ItemSection,
    item: int,
    removed: set[tuple[ItemSection, int, int]],
) -> tuple[SelectedBullet, ...]:
    return tuple(
        bullet for index, bullet in enumerate(bullets) if (section, item, index) not in removed
    )


def _bullet_cut(selection: Selection, candidate: BulletRef) -> Cut:
    bullets = _items(selection, candidate.section)[candidate.item][1]
    bullet = bullets[candidate.bullet]
    return Cut(kind="bullet", id=bullet.id, text=bullet.text)
