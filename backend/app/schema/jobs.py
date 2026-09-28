"""Job postings and applications: what a tailored resume was made for, and what went into it."""

from datetime import date

from pydantic import Field

from app.schema.common import Id, Model, NonEmptyStr


class JobPosting(Model):
    """A job posting broken into the parts tailoring matches against. Spellings are kept exact."""

    title: NonEmptyStr
    company: str | None = None
    seniority: str | None = None
    must_have: tuple[str, ...] = ()
    nice_to_have: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()
    key_terms: tuple[str, ...] = ()
    url: str | None = None


class SwapUse(Model):
    """A swap that went into a tailored resume."""

    bullet_id: Id
    text: NonEmptyStr


class Application(Model):
    """A tailored resume made for one job posting (FR-TLR-8)."""

    id: Id
    applied_on: date
    company: NonEmptyStr
    position: NonEmptyStr
    posting: JobPosting
    posting_text: NonEmptyStr
    """The posting as pasted, so the application can be reproduced."""
    variant_id: Id | None = None
    title_variant: NonEmptyStr
    """The role title used on this resume."""
    bullet_ids: tuple[Id, ...] = ()
    swaps_used: tuple[SwapUse, ...] = ()
    pdf_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
