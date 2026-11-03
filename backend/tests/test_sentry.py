"""Error reports to Sentry (T-052, NFR-OBS-1), with journal text scrubbed (NFR-PRIV-2)."""

import json
import logging
from collections.abc import Iterator
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import pytest
import sentry_sdk
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app import main
from app.config import Settings
from app.sentry import (
    drop_transaction,
    flush_reports,
    scrub_breadcrumb,
    scrub_event,
    start_sentry,
)

if TYPE_CHECKING:
    from sentry_sdk.types import Event

NOTE = "Cut the nightly export from 50 to 12 minutes for Acme Example"
"""A fictional journal note, which no report may hold."""
DSN = "https://public-key@sentry.example.com/42"
BOT_SECRET = "webhook-secret-123"


class Captured(Transport):
    """Keeps the events the SDK would send, instead of sending them."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if (event := item.get_event()) is not None:
                self.events.append(cast(dict[str, Any], event))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def transport() -> Iterator[Captured]:
    """A transport that keeps the events, with Sentry turned off again afterwards."""
    transport = Captured()
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)
    sentry_sdk.get_global_scope().remove_tag("component")
    sentry_sdk.get_isolation_scope().clear_breadcrumbs()


@pytest.fixture
def captured(transport: Captured) -> Captured:
    """Sentry, on with a transport that keeps the events."""
    assert start_sentry(
        Settings(sentry_dsn=SecretStr(DSN)), component="worker", transport=transport
    )
    return transport


def dumped(value: object) -> str:
    return json.dumps(value, default=str)


def a_full_event() -> dict[str, Any]:
    """An event with everything the SDK and its integrations might put in it."""
    frame = {
        "filename": "app/jobs.py",
        "abs_path": "/var/task/app/jobs.py",
        "module": "app.jobs",
        "function": "extract_entry",
        "lineno": 120,
        "in_app": True,
        "context_line": "    await run_extraction(entry_id)",
        "pre_context": ["async def extract_entry(entry_id):"],
        "post_context": [""],
        "vars": {"note": NOTE},
    }
    return {
        "event_id": "a" * 32,
        "level": "error",
        "logger": "app.jobs",
        "platform": "python",
        "server_name": "host-1",
        "environment": "production",
        "tags": {"component": "worker"},
        "transaction": "/telegram/webhook",
        "message": NOTE,
        "logentry": {
            "message": "couldn't read %s",
            "params": [NOTE],
            "formatted": f"couldn't read {NOTE}",
        },
        "exception": {
            "values": [
                {
                    "type": "ValidationError",
                    "module": "pydantic_core",
                    "value": f"1 validation error: input_value={NOTE!r}",
                    "mechanism": {"type": "logging", "handled": True, "data": {"note": NOTE}},
                    "stacktrace": {"frames": [frame]},
                }
            ]
        },
        "threads": {"values": [{"id": 1, "current": True, "stacktrace": {"frames": [frame]}}]},
        "request": {
            "method": "POST",
            "url": f"https://journal.example.com/telegram/webhook?text={NOTE}#{NOTE}",
            "query_string": f"text={NOTE}",
            "data": {"message": {"text": NOTE}},
            "headers": {"X-Telegram-Bot-Api-Secret-Token": BOT_SECRET},
            "cookies": {"wj_session": "session-token"},
            "env": {"REMOTE_ADDR": "203.0.113.9"},
        },
        "user": {"id": "u-1", "ip_address": "203.0.113.9", "username": "casey-example"},
        "extra": {"sys.argv": ["wj", "extract", "note.txt"], "job": {"task_kwargs": NOTE}},
        "contexts": {
            "runtime": {"name": "CPython", "version": "3.12.9"},
            "trace": {"trace_id": "b" * 32, "span_id": "c" * 16},
            "note": {"text": NOTE},
        },
        "breadcrumbs": {
            "values": [
                {"type": "log", "category": "app.jobs", "message": "refresh %s failed: %s"},
                {"type": "log", "category": "app.jobs", "message": "x", "data": {"t": NOTE}},
                {"type": "log", "category": "httpx2", "message": f"HTTP Request: GET /{NOTE}"},
                {"type": "http", "category": "httplib", "data": {"url": NOTE}},
            ]
        },
    }


def test_an_event_keeps_only_what_cant_hold_text() -> None:
    scrubbed = cast(dict[str, Any], scrub_event(cast("Event", a_full_event()), {}))
    assert NOTE not in dumped(scrubbed)
    assert BOT_SECRET not in dumped(scrubbed)
    assert "session-token" not in dumped(scrubbed)
    assert "203.0.113.9" not in dumped(scrubbed)
    assert "casey-example" not in dumped(scrubbed)
    assert "sys.argv" not in dumped(scrubbed)
    assert set(scrubbed) == {
        "event_id",
        "level",
        "logger",
        "platform",
        "server_name",
        "environment",
        "tags",
        "transaction",
        "logentry",
        "exception",
        "threads",
        "request",
        "contexts",
        "breadcrumbs",
    }
    assert scrubbed["request"] == {
        "method": "POST",
        "url": "https://journal.example.com/telegram/webhook",
    }
    assert scrubbed["logentry"] == {"message": "couldn't read %s"}
    (error,) = scrubbed["exception"]["values"]
    assert error["type"] == "ValidationError"
    assert error["mechanism"] == {"type": "logging", "handled": True}
    (frame,) = error["stacktrace"]["frames"]
    assert frame["function"] == "extract_entry"
    assert frame["context_line"] == "    await run_extraction(entry_id)"
    assert "vars" not in frame
    assert "vars" not in scrubbed["threads"]["values"][0]["stacktrace"]["frames"][0]
    assert set(scrubbed["contexts"]) == {"runtime", "trace"}
    assert scrubbed["breadcrumbs"]["values"] == [
        {"type": "log", "category": "app.jobs", "message": "refresh %s failed: %s"},
        {"type": "log", "category": "app.jobs", "message": "x"},
    ]


def test_another_librarys_log_message_is_left_out() -> None:
    event = {"logger": "sqlalchemy.pool", "logentry": {"message": f"failed on {NOTE}"}}
    assert scrub_event(cast("Event", event), {}) == {"logger": "sqlalchemy.pool"}


def test_a_log_breadcrumb_keeps_its_template_and_others_are_dropped() -> None:
    logger = logging.getLogger("app.jobs")
    record = logger.makeRecord("app.jobs", logging.WARNING, __file__, 1, "saved %s", (NOTE,), None)
    record.getMessage()
    crumb = {
        "type": "log",
        "level": "warning",
        "category": "app.jobs",
        "message": f"saved {NOTE}",
        "timestamp": "2026-11-01T10:00:00Z",
        "data": {"note": NOTE},
    }
    assert scrub_breadcrumb(crumb, {"log_record": record}) == {
        "type": "log",
        "category": "app.jobs",
        "level": "warning",
        "timestamp": "2026-11-01T10:00:00Z",
        "message": "saved %s",
    }
    assert scrub_breadcrumb(crumb, None) is None
    httpx = logger.makeRecord(
        "httpx2", logging.INFO, __file__, 1, "HTTP Request: %s", (NOTE,), None
    )
    assert scrub_breadcrumb({**crumb, "category": "httpx2"}, {"log_record": httpx}) is None
    assert scrub_breadcrumb({"type": "http", "data": {"url": NOTE}}, {}) is None


def test_performance_traces_are_not_sent() -> None:
    assert drop_transaction(cast("Event", {"type": "transaction", "spans": []}), {}) is None


def test_sentry_is_off_without_a_dsn() -> None:
    assert start_sentry(Settings(sentry_dsn=None), component="api") is False
    assert not sentry_sdk.get_client().is_active()


@pytest.mark.parametrize(
    "dsn", ["not a dsn", "http://key@sentry.example.com/1", "https://sentry.example.com/1"]
)
def test_a_dsn_that_isnt_one_is_refused(dsn: str) -> None:
    with pytest.raises(ValidationError, match="must be a Sentry DSN"):
        Settings(sentry_dsn=SecretStr(dsn))


def test_an_error_in_a_request_is_reported_without_its_text(
    transport: Captured, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(request: Request) -> None:
        body = await request.json()
        logging.getLogger("app.telegram").warning("couldn't read update %s", body["text"])
        raise ValueError(f"can't parse {body['text']!r}")

    # The served app starts Sentry as it starts, after its routes exist.
    monkeypatch.setattr(main, "setup_logging", lambda settings: None)
    monkeypatch.setattr(main, "start_sentry", partial(start_sentry, transport=transport))
    api = main.create_app(Settings(database_url=None, sentry_dsn=SecretStr(DSN)), observe=True)
    api.add_api_route("/fail", fail, methods=["POST"])
    with TestClient(api, raise_server_exceptions=False) as client:
        client.cookies.set("wj_session", "session-token")
        response = client.post(
            "/fail?token=query-secret",
            json={"text": NOTE},
            headers={"X-Telegram-Bot-Api-Secret-Token": BOT_SECRET},
        )
    assert response.status_code == 500
    sentry_sdk.flush()
    (event,) = transport.events
    text = dumped(event)
    for secret in (NOTE, BOT_SECRET, "query-secret", "session-token"):
        assert secret not in text
    (error,) = event["exception"]["values"]
    assert error["type"] == "ValueError"
    assert "value" not in error
    assert event["request"] == {"method": "POST", "url": "http://testserver/fail"}
    assert event["tags"]["component"] == "api"
    crumbs = [crumb["message"] for crumb in event["breadcrumbs"]["values"]]
    assert "couldn't read update %s" in crumbs
    transport.events.clear()


@pytest.mark.anyio
async def test_a_jobs_last_failure_is_reported_without_its_text(captured: Captured) -> None:
    try:
        raise RuntimeError(f"extraction failed for {NOTE!r}")
    except RuntimeError:
        logging.getLogger("procrastinate.worker").error(
            "Job extract_entry(entry_id='e-1') ended with status: Error, lasted 1.000 s",
            exc_info=True,
            extra={
                "action": "job_error",
                "job": {"id": 7, "task_kwargs": {"note": NOTE}},
                "result": NOTE,
            },
        )
    await flush_reports()
    (event,) = captured.events
    assert NOTE not in dumped(event)
    assert "extra" not in event
    assert event["logentry"] == {
        "message": "Job extract_entry(entry_id='e-1') ended with status: Error, lasted 1.000 s"
    }
    assert event["exception"]["values"][0]["type"] == "RuntimeError"
    assert event["tags"]["component"] == "worker"
