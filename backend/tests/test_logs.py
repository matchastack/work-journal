"""Structured JSON logs (T-052, NFR-OBS-1): one object per line, with no journal text. The
served app, the worker and the tick set them up, with Sentry."""

import json
import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from typer.testing import CliRunner

from app import cli as cli_module
from app import main
from app.cli import cli
from app.config import Settings
from app.logs import JsonFormatter, exception_text, setup_logging

NOTE = "Cut the nightly export from 50 to 12 minutes for Acme Example"
"""A fictional journal note, which no log line may hold."""


def record(message: str = "closed %d quiet journal entries", *args: object, **extra: Any) -> str:
    """A log record formatted as the app's logs format it."""
    logger = logging.getLogger("app.jobs")
    made = logger.makeRecord(
        "app.jobs", logging.INFO, __file__, 1, message, args, None, extra=extra
    )
    return JsonFormatter().format(made)


def failing_record() -> logging.LogRecord:
    def parse(note: str) -> None:
        raise ValueError(f"can't read {note!r}")

    def extract(note: str) -> None:
        try:
            parse(note)
        except ValueError as error:
            raise RuntimeError(f"extraction failed for {note!r}") from error

    with pytest.raises(RuntimeError) as raised:
        extract(NOTE)
    exc_info = (raised.type, raised.value, raised.tb)
    return logging.getLogger("app.jobs").makeRecord(
        "app.jobs", logging.ERROR, __file__, 1, "job %s failed", ("j-1",), exc_info
    )


@pytest.fixture
def root_logging() -> Iterator[logging.Logger]:
    """The root logger, put back as it was afterwards."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield root
    root.handlers[:] = handlers
    root.setLevel(level)


def test_a_record_is_one_line_of_json() -> None:
    line = record("closed %d quiet journal entries", 3)
    assert "\n" not in line
    entry = json.loads(line)
    assert entry == {
        "time": entry["time"],
        "level": "INFO",
        "logger": "app.jobs",
        "message": "closed 3 quiet journal entries",
    }
    assert entry["time"].endswith("Z")


def test_only_the_jobs_known_fields_are_kept() -> None:
    job = {
        "id": 42,
        "task_name": "extract_entry",
        "queue": "default",
        "attempts": 1,
        "task_kwargs": {"note": NOTE},
        "call_string": f"extract_entry(note={NOTE!r})",
    }
    line = record("job done", action="job_success", job=job, duration=1.23456, result=NOTE)
    entry = json.loads(line)
    assert {key: entry[key] for key in entry if key not in ("time", "message")} == {
        "level": "INFO",
        "logger": "app.jobs",
        "action": "job_success",
        "duration_s": 1.235,
        "job_id": 42,
        "task": "extract_entry",
        "queue": "default",
        "attempts": 1,
    }
    assert NOTE not in line


def test_other_extra_fields_are_left_out() -> None:
    entry = json.loads(record("saved", text=NOTE, user={"name": "Casey Example"}))
    assert set(entry) == {"time", "level", "logger", "message"}


def test_an_exception_shows_its_traceback_but_not_its_message_or_locals() -> None:
    line = JsonFormatter().format(failing_record())
    entry = json.loads(line)
    assert entry["message"] == "job j-1 failed"
    trace = entry["exception"]
    assert trace.count("Traceback (most recent call last):") == 2
    assert "in parse" in trace
    assert "in extract" in trace
    assert 'raise ValueError(f"can\'t read {note!r}")' in trace
    assert "builtins.ValueError (message left out)" in trace
    assert "The above exception led to the following one:" in trace
    assert trace.endswith("builtins.RuntimeError (message left out)")
    assert NOTE not in line
    assert "Acme" not in line


def test_an_exception_that_loops_back_to_itself_is_shown_once() -> None:
    error = ValueError(NOTE)
    error.__context__ = error
    assert exception_text(error) == (
        "Traceback (most recent call last):\nbuiltins.ValueError (message left out)"
    )


def test_logs_go_to_standard_error_as_json_at_the_set_level(
    root_logging: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    root_logging.addHandler(logging.StreamHandler(sys.stdout))
    setup_logging(Settings(log_level="WARNING"))
    assert len(root_logging.handlers) == 1
    assert root_logging.level == logging.WARNING
    logging.getLogger("app.test").info("hidden")
    logging.getLogger("app.test").warning("shown %s", "here")
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["message"] == "shown here"


def test_only_the_served_app_logs_as_json_and_reports_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Settings]] = []
    monkeypatch.setattr(main, "setup_logging", lambda settings: calls.append(("logs", settings)))

    def start_sentry(settings: Settings, *, component: str) -> bool:
        calls.append((f"sentry:{component}", settings))
        return False

    monkeypatch.setattr(main, "start_sentry", start_sentry)
    settings = Settings(database_url=None)
    with TestClient(main.create_app(settings)):
        pass
    assert calls == []
    with TestClient(main.create_app(settings, observe=True)):
        pass
    assert calls == [("logs", settings), ("sentry:api", settings)]


@pytest.mark.parametrize("command", ["worker", "tick"])
def test_the_worker_and_the_tick_log_as_json_and_report_errors(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    settings = Settings(database_url=SecretStr("postgresql://nobody@localhost/nothing"))
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "setup_logging", lambda _: calls.append("logs"))

    def start_sentry(started: Settings, *, component: str) -> bool:
        assert started is settings
        calls.append(f"sentry:{component}")
        return False

    async def run(*args: object, **kwargs: object) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr(cli_module, "start_sentry", start_sentry)
    monkeypatch.setattr(cli_module, "run_worker", run)
    monkeypatch.setattr(cli_module, "tick", run)
    result = CliRunner().invoke(cli, [command])
    assert result.exit_code == 0, result.output
    assert calls == ["logs", f"sentry:{command}", command]
