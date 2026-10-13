from datetime import date
from pathlib import Path

import pytest

from app.evals.dataset import (
    FICTIONAL_DIR,
    SUITES,
    AdversarialCase,
    DatasetError,
    ExtractionCase,
    Suite,
    WritingCase,
    load_dataset,
    writing_item,
)

FIXTURES = Path(__file__).parent / "fixtures"
HEADER = f"""
profile = "{FIXTURES / "profile.json"}"
fact_files = ["{FIXTURES / "facts.json"}"]
"""
EXTRACTION_HEADER = f"""
profile = "{FIXTURES / "profile.json"}"
written = 2026-09-14
"""
NOTE = "finally got the nightly export down from ~50 min to 12"


def write(folder: Path, suite: Suite, text: str) -> Path:
    path = folder / f"{suite}.toml"
    path.write_text(text, encoding="utf-8")
    return path


def failure(folder: Path, suite: Suite, text: str) -> str:
    write(folder, suite, text)
    with pytest.raises(DatasetError) as raised:
        load_dataset(folder, suite)
    return str(raised.value)


@pytest.mark.parametrize("suite", SUITES)
def test_the_fictional_dataset_loads(suite: Suite) -> None:
    dataset = load_dataset(FICTIONAL_DIR, suite)
    assert dataset is not None
    assert len(dataset.cases) >= 8
    assert dataset.profile.basics.name


def test_the_fictional_dataset_has_every_kind_of_case() -> None:
    extraction = load_dataset(FICTIONAL_DIR, "extraction")
    adversarial = load_dataset(FICTIONAL_DIR, "adversarial")
    assert extraction is not None and adversarial is not None
    extraction_cases = [case for case in extraction.cases if isinstance(case, ExtractionCase)]
    assert any(not case.expected for case in extraction_cases), "a note with no facts"
    assert any(case.never for case in extraction_cases), "a note that tries to trick the model"
    adversarial_cases = [case for case in adversarial.cases if isinstance(case, AdversarialCase)]
    assert any(case.catch for case in adversarial_cases), "known-bad text"
    assert any(not case.catch for case in adversarial_cases), "faithful text"


def test_a_folder_without_the_suite_has_no_dataset(tmp_path: Path) -> None:
    assert load_dataset(tmp_path, "writing") is None


def test_extraction_cases_take_the_files_date_unless_they_give_one(tmp_path: Path) -> None:
    write(
        tmp_path,
        "extraction",
        EXTRACTION_HEADER
        + f"""
[[cases]]
id = "export"
note = "{NOTE}"

[[cases.expected]]
terms = ["export"]
numbers = [50, 12]
role = "northwind"

[[cases]]
id = "older"
note = "{NOTE}"
written = 2026-08-01
""",
    )
    dataset = load_dataset(tmp_path, "extraction")
    assert dataset is not None
    assert [case.written for case in dataset.cases if isinstance(case, ExtractionCase)] == [
        date(2026, 9, 14),
        date(2026, 8, 1),
    ]


def test_facts_come_from_fact_files_and_the_file_itself(tmp_path: Path) -> None:
    write(
        tmp_path,
        "writing",
        HEADER
        + """
[[facts]]
id = "fact_nw_alerts"
statement = "Added an alert that pages the on-call engineer when the nightly export fails."
roleId = "northwind"

[[cases]]
id = "alerts"
facts = ["fact_nw_alerts"]
""",
    )
    dataset = load_dataset(tmp_path, "writing")
    assert dataset is not None
    assert {"fact_nw_export", "fact_rb_app", "fact_nw_alerts"} <= set(dataset.facts)


def test_paths_are_relative_to_the_file(tmp_path: Path) -> None:
    folder = tmp_path / "local" / "evals"
    folder.mkdir(parents=True)
    (tmp_path / "local" / "profile.json").write_bytes((FIXTURES / "profile.json").read_bytes())
    write(
        folder,
        "adversarial",
        """
profile = "../profile.json"
facts = [{ id = "fact_1", statement = "Wrote the export job.", roleId = "northwind" }]

[[cases]]
id = "faithful"
text = "Wrote the export job."
facts = ["fact_1"]
""",
    )
    dataset = load_dataset(folder, "adversarial")
    assert dataset is not None
    assert dataset.profile.work[0].id == "northwind"


@pytest.mark.parametrize(
    ("case", "item"),
    [
        ('facts = ["fact_nw_export"]', "northwind"),
        ('facts = ["fact_rb_app"]', "recipe_box"),
        ('facts = ["fact_nw_export"]\nunder = "contoso"', "contoso"),
        ('facts = ["fact_rb_app"]\nedit = "nw_export"', "northwind"),
    ],
)
def test_a_bullet_goes_under_the_item_named_or_else_its_bullets_or_facts(
    tmp_path: Path, case: str, item: str
) -> None:
    write(tmp_path, "writing", HEADER + f'\n[[cases]]\nid = "case"\n{case}\n')
    dataset = load_dataset(tmp_path, "writing")
    assert dataset is not None
    assert isinstance(dataset.cases[0], WritingCase)
    assert writing_item(dataset.cases[0], dataset) == item


def test_facts_from_two_items_need_an_item_named(tmp_path: Path) -> None:
    case = '[[cases]]\nid = "mixed"\nfacts = ["fact_nw_export", "fact_rb_app"]\n'
    message = failure(tmp_path, "writing", HEADER + case)
    assert "case 'mixed'" in message
    assert "say which with `under`" in message


@pytest.mark.parametrize(
    ("suite", "case", "problem"),
    [
        ("writing", 'facts = ["fact_unknown"]', "there's no fact 'fact_unknown'"),
        ("writing", 'facts = ["fact_nw_export"]\nunder = "nowhere"', "there's no item 'nowhere'"),
        ("writing", 'facts = ["fact_nw_export"]\nedit = "nw_gone"', "no bullet 'nw_gone'"),
        ("adversarial", 'text = "Cut it."\nfacts = ["fact_x"]', "there's no fact 'fact_x'"),
        ("extraction", 'note = "n"\nrole = "globex"', "there's no role 'globex'"),
        (
            "extraction",
            'note = "n"\n[[cases.expected]]\nproject = "nope"',
            "there's no project 'nope'",
        ),
    ],
)
def test_an_unknown_id_is_an_error(tmp_path: Path, suite: Suite, case: str, problem: str) -> None:
    header = HEADER if suite != "extraction" else EXTRACTION_HEADER
    message = failure(tmp_path, suite, header + f'\n[[cases]]\nid = "bad"\n{case}\n')
    assert f"{tmp_path / f'{suite}.toml'}, case 'bad'" in message
    assert problem in message


def test_case_ids_are_unique(tmp_path: Path) -> None:
    case = '\n[[cases]]\nid = "twice"\nfacts = ["fact_nw_export"]\n'
    assert "another case has this ID" in failure(tmp_path, "writing", HEADER + case * 2)


def test_two_facts_with_one_id_are_an_error(tmp_path: Path) -> None:
    fact = '\n[[facts]]\nid = "fact_nw_export"\nstatement = "Again."\n'
    message = failure(tmp_path, "writing", HEADER + "cases = []\n" + fact)
    assert "two facts have the ID 'fact_nw_export'" in message


def test_a_file_that_isnt_toml_is_an_error(tmp_path: Path) -> None:
    assert "isn't valid TOML" in failure(tmp_path, "writing", "cases = [")


def test_format_errors_name_the_field_and_never_quote_the_case(tmp_path: Path) -> None:
    text = (
        EXTRACTION_HEADER
        + """
[[cases]]
id = "typo"
note = "a private note about my day"
expectd = []

[[cases]]
id = "pattern"
note = "another private note"
never = ['(unclosed']
"""
    )
    message = failure(tmp_path, "extraction", text)
    assert "doesn't match the format" in message
    assert "cases.0.expectd: Extra inputs are not permitted" in message
    assert "cases.1.never.0: Value error, not a regular expression" in message
    assert "private note" not in message


def test_a_missing_profile_is_an_error(tmp_path: Path) -> None:
    text = 'profile = "missing.json"\ncases = []\n'
    assert f"the profile {tmp_path / 'missing.json'} doesn't exist" in failure(
        tmp_path, "adversarial", text
    )


def test_case_ids_cant_break_the_reports_tables(tmp_path: Path) -> None:
    case = '\n[[cases]]\nid = "a | b"\nfacts = ["fact_nw_export"]\n'
    assert "cases.0.id" in failure(tmp_path, "writing", HEADER + case)
