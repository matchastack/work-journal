"""Golden tests: selections from the fictional profile, as reviewed JSON. The master document,
and a one-page selection for each role type, as tailoring to a posting in that role type makes.

After an intended change to selection, rewrite the files with `UPDATE_GOLDEN=1 uv run pytest
tests/test_selection_golden.py` and review the diff before committing it.
"""

import json
import os
from pathlib import Path

import pytest

from app.schema.profile import Profile
from app.schema.variant import Variant
from app.selection import MASTER_VARIANT, select

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))


VARIANTS = [
    MASTER_VARIANT,
    *(Variant(id=role.id, name=role.name, role_type=role.id) for role in PROFILE.role_types),
]


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda variant: variant.id)
def test_selection_matches_its_golden_file(variant: Variant) -> None:
    actual = select(PROFILE, variant).model_dump(mode="json")
    path = FIXTURES / "golden" / f"selection-{variant.id}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == actual
