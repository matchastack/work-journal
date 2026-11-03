"""Structured logs: one JSON object per line, which never holds journal text (NFR-OBS-1,
NFR-PRIV-2).

The served app (`app.main`), `wj worker` and `wj tick` log this way. Each line has `time` (UTC),
`level`, `logger` and `message`, and the few extra fields in `_job_fields`. The app's own log
calls pass IDs, counts and error types, never text, and the formatter adds nothing that could
hold text:
- other extra fields are left out, since a library's extras may hold anything;
- an exception shows its traceback, as file, line, function and source line, and its type, but
  never its message, which may quote what failed to parse, and never local variables.
"""

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any, cast

from app.config import Settings


class JsonFormatter(logging.Formatter):
    """Formats a log record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry |= _job_fields(record)
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = exception_text(record.exc_info[1])
        if record.stack_info:
            entry["stack"] = record.stack_info
        return json.dumps(entry, ensure_ascii=True, default=str)


def exception_text(error: BaseException) -> str:
    """An exception's traceback, with the exceptions that led to it, as Python prints it but
    with each exception's type in place of its message."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    parts: list[str] = []
    for index, link in enumerate(reversed(chain)):
        if index:
            parts.append("\nThe above exception led to the following one:\n\n")
        parts.append("Traceback (most recent call last):\n")
        parts.extend(traceback.format_tb(link.__traceback__))
        kind = type(link)
        parts.append(f"{kind.__module__}.{kind.__qualname__} (message left out)\n")
    return "".join(parts).rstrip("\n")


def _job_fields(record: logging.LogRecord) -> dict[str, Any]:
    """What Procrastinate's worker says about a job: the event (`job_success`, `job_error` and so
    on), the job's ID, task, queue and attempts, and how long it ran. Its other fields, such as
    the job's arguments and result, are left out."""
    fields: dict[str, Any] = {}
    if isinstance(action := getattr(record, "action", None), str):
        fields["action"] = action
    if isinstance(duration := getattr(record, "duration", None), int | float):
        fields["duration_s"] = round(duration, 3)
    job: object = getattr(record, "job", None)
    if isinstance(job, dict):
        details = cast(dict[str, object], job)
        for name, key in (("job_id", "id"), ("task", "task_name"), ("queue", "queue")):
            if isinstance(value := details.get(key), int | str):
                fields[name] = value
        if isinstance(attempts := details.get("attempts"), int):
            fields["attempts"] = attempts
    return fields


SERVER_LOGGERS = ("uvicorn", "uvicorn.error")
"""Uvicorn's own loggers, which print a request's unhandled exception with its message. They're
sent to the JSON handler instead. Its access log, one line per request, stays as it is."""


def setup_logging(settings: Settings) -> None:
    """Send every log record at `LOG_LEVEL` and above to standard error as JSON, in place of the
    root logger's other handlers and Uvicorn's."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for old in root.handlers[:]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    for name in SERVER_LOGGERS:
        server = logging.getLogger(name)
        for old in server.handlers[:]:
            server.removeHandler(old)
        server.propagate = True
