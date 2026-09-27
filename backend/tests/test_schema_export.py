import json
from pathlib import Path

from typer.testing import CliRunner

from app.cli import cli
from app.schema.export import JSON_SCHEMA_DIALECT, json_schemas, write_json_schemas

EXPECTED_FILES = {
    "profile.schema.json",
    "fact.schema.json",
    "variant.schema.json",
    "change-op.schema.json",
    "job-posting.schema.json",
    "application.schema.json",
}


def test_every_core_model_has_a_titled_schema() -> None:
    schemas = json_schemas()
    assert {f"{name}.schema.json" for name in schemas} == EXPECTED_FILES
    for schema in schemas.values():
        assert schema["$schema"] == JSON_SCHEMA_DIALECT
        assert schema["title"]


def test_profile_schema_uses_json_resume_style_names() -> None:
    profile = json_schemas()["profile"]
    assert {"basics", "roleTypes", "work", "education", "projects", "openQuestions"} <= set(
        profile["properties"]
    )
    role = profile["$defs"]["Role"]["properties"]
    assert {"startDate", "endDate", "highlights", "titleVariants", "loadBearing"} <= set(role)


def test_change_op_schema_is_discriminated_by_op() -> None:
    change_op = json_schemas()["change-op"]
    assert change_op["discriminator"]["propertyName"] == "op"
    assert {"add_bullet", "edit_bullet", "resolve_open_question"} <= set(
        change_op["discriminator"]["mapping"]
    )


def test_write_json_schemas_creates_valid_json_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "schemas"
    paths = write_json_schemas(out_dir)
    assert {path.name for path in paths} == EXPECTED_FILES
    for path in paths:
        assert json.loads(path.read_text())["$schema"] == JSON_SCHEMA_DIALECT


def test_wj_schema_export_command(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["schema", "export", str(tmp_path)])
    assert result.exit_code == 0
    assert {path.name for path in tmp_path.iterdir()} == EXPECTED_FILES
    assert "Wrote" in result.stdout
