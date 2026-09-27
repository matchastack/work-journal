"""JSON Schemas for the core models, e.g. for other tools or for checking stored data."""

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.schema.changes import ChangeOp
from app.schema.fact import Fact
from app.schema.jobs import Application, JobPosting
from app.schema.profile import Profile
from app.schema.variant import Variant

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

SCHEMA_TYPES: dict[str, tuple[str, Any]] = {
    "profile": ("Profile", Profile),
    "fact": ("Fact", Fact),
    "variant": ("Variant", Variant),
    "change-op": ("ChangeOp", ChangeOp),
    "job-posting": ("JobPosting", JobPosting),
    "application": ("Application", Application),
}
"""File name stem -> (schema title, model or type)."""


def json_schemas() -> dict[str, dict[str, Any]]:
    """Build the JSON Schema for every core model, keyed by file name stem."""
    schemas: dict[str, dict[str, Any]] = {}
    for name, (title, schema_type) in SCHEMA_TYPES.items():
        schema = TypeAdapter(schema_type).json_schema()
        schemas[name] = {"$schema": JSON_SCHEMA_DIALECT, **schema, "title": title}
    return schemas


def write_json_schemas(out_dir: Path) -> list[Path]:
    """Write `<name>.schema.json` files into `out_dir` and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, schema in json_schemas().items():
        path = out_dir / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2) + "\n")
        paths.append(path)
    return paths
