"""Variants: rule sets that turn the master profile into one output (resume, web page, LinkedIn)."""

from typing import Literal, Self

from pydantic import Field, model_validator

from app.schema.common import Id, Model, NonEmptyStr, Tag

Section = Literal["summary", "education", "work", "projects", "skills"]
Audience = Literal["resume", "web", "linkedin"]
ContactField = Literal["email", "phone", "location", "url", "profiles"]


class TitleChoice(Model):
    """The title to show for one role: its official title or one of its approved variants."""

    role_id: Id
    title: NonEmptyStr


class Variant(Model):
    id: Id
    name: NonEmptyStr
    audience: Audience = "resume"
    """Which visibility levels show: resumes include `resume_only` fields, the web page doesn't."""
    role_type: Tag | None = None
    """Drives keep/cut rules, coursework, skills preset and summary. None = master document."""
    include_tags: tuple[Tag, ...] = ()
    section_order: tuple[Section, ...] = ("education", "work", "projects", "skills")
    include_summary: bool = False
    max_pages: int | None = Field(default=1, ge=1)
    """None means no limit (e.g. the full master document)."""
    template: NonEmptyStr = "default"
    titles: tuple[TitleChoice, ...] = ()
    """Titles to show for particular roles. Other roles show their first approved variant."""
    hidden: tuple[ContactField, ...] = ()
    """Contact details to leave out, e.g. the phone when a job portal already has it."""

    @model_validator(mode="after")
    def listed_once(self) -> Self:
        if len(set(self.section_order)) != len(self.section_order):
            raise ValueError(f"variant {self.id!r} lists a section more than once")
        role_ids = [choice.role_id for choice in self.titles]
        if len(set(role_ids)) != len(role_ids):
            raise ValueError(f"variant {self.id!r} chooses a title twice for the same role")
        return self
