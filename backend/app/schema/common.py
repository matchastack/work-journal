"""Base model and field types shared by every schema.

Stored JSON uses camelCase field names, following JSON Resume (`startDate`, `highlights`).
Python code uses snake_case; the aliases map between the two.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    """Base for all schema models: immutable, strict about unknown fields, camelCase in JSON."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


Id = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=64)]
"""Stable identifier, e.g. `st_backend` or `q_st_scale`. Operations address items by ID."""

Tag = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=32)]
"""Short lowercase label, e.g. a role type such as `backend` or `ml`."""

YearMonth = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
"""A month, stored as `YYYY-MM`. Templates decide how to display it."""

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]

Visibility = Literal["public", "resume_only", "private"]
"""Where a value may appear: everywhere, only in resumes you send, or nowhere."""

Priority = Annotated[int, Field(ge=1, le=3)]
"""1 = keep whenever possible, 3 = first to cut when fitting a page."""

ItemStatus = Literal["active", "benched", "planned"]
"""Benched items are kept for the record but never rendered; planned items don't exist yet."""


class Note(Model):
    """A remark attached to an item, e.g. a warning about what must not be claimed."""

    kind: Literal["note", "warning", "upgrade"] = "note"
    text: NonEmptyStr
