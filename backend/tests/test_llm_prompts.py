from pathlib import Path

import pytest

from app.llm.prompts import PROMPTS_DIR, load_prompt

FIXTURE_PROMPTS = Path(__file__).parent / "fixtures" / "prompts"


def test_loads_a_named_version() -> None:
    prompt = load_prompt("triage", 1, root=FIXTURE_PROMPTS)
    assert prompt.id == "triage/v1"
    assert prompt.system.startswith("You sort messages")
    assert not prompt.system.endswith("\n")


def test_render_fills_the_user_template() -> None:
    prompt = load_prompt("triage", 1, root=FIXTURE_PROMPTS)
    rendered = prompt.render({"sender": "Casey", "message": "Shipped the export page."})
    assert rendered == "Message from Casey:\n\nShipped the export page."


def test_render_rejects_a_missing_variable() -> None:
    prompt = load_prompt("triage", 1, root=FIXTURE_PROMPTS)
    with pytest.raises(KeyError, match="message"):
        prompt.render({"sender": "Casey"})


def test_render_leaves_dollar_signs_in_values_alone() -> None:
    prompt = load_prompt("triage", 1, root=FIXTURE_PROMPTS)
    rendered = prompt.render({"sender": "Casey", "message": "Cut costs by $2k, $message"})
    assert rendered.endswith("Cut costs by $2k, $message")


def test_unknown_version_is_an_error() -> None:
    with pytest.raises(FileNotFoundError, match="version 2"):
        load_prompt("triage", 2, root=FIXTURE_PROMPTS)


@pytest.mark.parametrize("name", ["../triage", "Triage", "", "triage/v1"])
def test_names_are_plain_identifiers(name: str) -> None:
    with pytest.raises(ValueError, match="not a prompt name"):
        load_prompt(name, 1, root=FIXTURE_PROMPTS)


def test_every_shipped_prompt_version_loads() -> None:
    """Each folder in app/llm/prompts is `<name>/v<N>/` with both files, as the README says."""
    for folder in sorted(PROMPTS_DIR.glob("*/v*")):
        prompt = load_prompt(folder.parent.name, int(folder.name.removeprefix("v")))
        assert prompt.system
        assert prompt.user
