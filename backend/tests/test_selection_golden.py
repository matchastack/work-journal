"""Golden tests: each default variant's selection from the fictional profile, as reviewed JSON.

After an intended change to selection, rewrite the files with `UPDATE_GOLDEN=1 uv run pytest
tests/test_selection_golden.py` and review the diff before committing it.
"""

import json
import os
from pathlib import Path

import pytest

from app.schema.profile import Profile
from app.schema.variant import Variant
from app.selection import default_variants, select

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("variant", default_variants(PROFILE), ids=lambda variant: variant.id)
def test_selection_matches_its_golden_file(variant: Variant) -> None:
    actual = select(PROFILE, variant).model_dump(mode="json")
    path = FIXTURES / "golden" / f"selection-{variant.id}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == actual
