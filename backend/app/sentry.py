"""Error reports to Sentry from the app, the worker and the tick (NFR-OBS-1), off unless
`SENTRY_DSN` is set.

Journal and fact text never reach Sentry (NFR-PRIV-2). The SDK is asked to collect as little as
it can (`DATA_COLLECTION`), and every event then passes `scrub_event`, which keeps only what
can't hold text and drops the rest, rather than looking for text in it:
- an exception's type and stack trace (file, line, function and source line), without its
  message, which may quote what failed to parse, and without local variables;
- a request's method and address, without its query string, headers, cookies or body: a
  Telegram update's body holds the journal message;
- the template of the app's and Procrastinate's log messages, such as `refresh %s failed: %s`,
  without the values put into it or any extra fields;
- breadcrumbs from those logs only, as templates. Others, such as an outgoing request whose
  address holds the bot's token, are dropped.

Errors are reported, but performance traces aren't. A job that fails for the last time reaches
Sentry through Procrastinate's error log, with its exception.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit, urlunsplit

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.transport import Transport

from app.config import Settings

if TYPE_CHECKING:
    from sentry_sdk.types import (
        Breadcrumb,
        BreadcrumbHint,
        DataCollectionUserOptions,
        Event,
        Hint,
    )

FLUSH_TIMEOUT_S = 2.0
"""How long to wait for reports to be sent before a tick ends."""

DATA_COLLECTION: "DataCollectionUserOptions" = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_headers": {"request": {"mode": "off"}},
    "http_bodies": [],
    "url_query_params": {"mode": "off"},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": False,
}
"""Everything the SDK would collect by itself is off: user details, cookies, headers, bodies,
query strings, model inputs and outputs, SQL values, job arguments and local variables. Only
the source lines around each stack frame stay."""

_EVENT_FIELDS = (
    "event_id",
    "timestamp",
    "level",
    "logger",
    "platform",
    "sdk",
    "server_name",
    "release",
    "environment",
    "dist",
    "transaction",
    "transaction_info",
    "fingerprint",
    "modules",
    "tags",
    "type",
)
"""An event's fields that are kept as they are: they describe the error and where it happened."""
_CONTEXTS = frozenset({"runtime", "os", "trace", "response", "app", "device", "cloud_resource"})
_FRAME_FIELDS = (
    "filename",
    "abs_path",
    "module",
    "function",
    "lineno",
    "colno",
    "in_app",
    "package",
    "platform",
    "pre_context",
    "context_line",
    "post_context",
)
"""A stack frame without its local variables: where it is in the code, and the code there."""
_MECHANISM_FIELDS = (
    "type",
    "handled",
    "synthetic",
    "exception_id",
    "parent_id",
    "is_exception_group",
    "source",
)
_BREADCRUMB_FIELDS = ("type", "category", "level", "timestamp", "message")
_OWN_LOGGERS = ("app", "procrastinate")
"""Loggers whose messages are templates with IDs, counts and error types in them, never text."""


def start_sentry(settings: Settings, *, component: str, transport: Transport | None = None) -> bool:
    """Report errors to Sentry when `SENTRY_DSN` is set, tagged with the `component` that
    reports them (`api`, `worker` or `tick`). Returns whether it's on. Call it inside the event
    loop, so that tasks' errors are caught too."""
    dsn = settings.sentry_dsn
    if dsn is None:
        return False
    sentry_sdk.init(
        dsn=dsn.get_secret_value(),
        environment=settings.app_env,
        data_collection=DATA_COLLECTION,
        include_local_variables=False,
        max_request_body_size="never",
        auto_enabling_integrations=False,
        integrations=[StarletteIntegration(), FastApiIntegration(), AsyncioIntegration()],
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        before_send_transaction=drop_transaction,
        transport=transport,
    )
    sentry_sdk.get_global_scope().set_tag("component", component)
    return True


async def flush_reports() -> None:
    """Send the reports waiting to go, so that a function frozen after answering, as Vercel's
    may be, doesn't lose them."""
    if sentry_sdk.get_client().is_active():
        await asyncio.to_thread(sentry_sdk.flush, FLUSH_TIMEOUT_S)


def scrub_event(event: "Event", hint: "Hint") -> "Event | None":
    """The parts of an error event that can't hold journal or fact text."""
    source = cast(Mapping[str, Any], event)
    scrubbed: dict[str, Any] = {key: source[key] for key in _EVENT_FIELDS if key in source}
    if isinstance(contexts := source.get("contexts"), Mapping):
        named = cast(Mapping[str, Any], contexts)
        scrubbed["contexts"] = {key: named[key] for key in named if key in _CONTEXTS}
    if isinstance(request := source.get("request"), Mapping):
        scrubbed["request"] = _request(cast(Mapping[str, Any], request))
    if (exceptions := _values(source.get("exception"))) is not None:
        scrubbed["exception"] = {"values": [_exception(value) for value in exceptions]}
    if (threads := _values(source.get("threads"))) is not None:
        scrubbed["threads"] = {"values": [_thread(value) for value in threads]}
    if isinstance(entry := source.get("logentry"), Mapping) and _own_logger(source.get("logger")):
        message = cast(Mapping[str, Any], entry).get("message")
        if isinstance(message, str):
            scrubbed["logentry"] = {"message": message}
    if (breadcrumbs := _values(source.get("breadcrumbs"))) is not None:
        scrubbed["breadcrumbs"] = {
            "values": [
                {key: crumb[key] for key in _BREADCRUMB_FIELDS if key in crumb}
                for crumb in breadcrumbs
                if crumb.get("type") == "log" and _own_logger(crumb.get("category"))
            ]
        }
    return cast("Event", scrubbed)


def scrub_breadcrumb(crumb: "Breadcrumb", hint: "BreadcrumbHint | None") -> "Breadcrumb | None":
    """A breadcrumb from the app's or Procrastinate's logs, with the message's template in
    place of the message. Every other breadcrumb is dropped."""
    record = (hint or {}).get("log_record")
    if crumb.get("type") != "log" or not isinstance(record, logging.LogRecord):
        return None
    if not _own_logger(record.name) or not isinstance(record.msg, str):
        return None
    return {
        "type": "log",
        "category": record.name,
        "level": crumb.get("level"),
        "timestamp": crumb.get("timestamp"),
        "message": record.msg,
    }


def drop_transaction(event: "Event", hint: "Hint") -> "Event | None":
    """Performance traces aren't sent: their spans name addresses and queries."""
    return None


def _own_logger(name: object) -> bool:
    return isinstance(name, str) and any(
        name == own or name.startswith(f"{own}.") for own in _OWN_LOGGERS
    )


def _values(container: object) -> list[Mapping[str, Any]] | None:
    """The mappings in `{"values": [...]}`, as exceptions, threads and breadcrumbs come."""
    if isinstance(container, Mapping):
        container = cast(Mapping[str, Any], container).get("values")
    if not isinstance(container, Sequence) or isinstance(container, str):
        return None
    items = cast(Sequence[object], container)
    return [cast(Mapping[str, Any], item) for item in items if isinstance(item, Mapping)]


def _request(request: Mapping[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    if isinstance(method := request.get("method"), str):
        kept["method"] = method
    if isinstance(url := request.get("url"), str):
        parts = urlsplit(url)
        kept["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return kept


def _exception(value: Mapping[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {key: value[key] for key in ("type", "module") if key in value}
    if isinstance(mechanism := value.get("mechanism"), Mapping):
        fields = cast(Mapping[str, Any], mechanism)
        kept["mechanism"] = {key: fields[key] for key in _MECHANISM_FIELDS if key in fields}
    if (stacktrace := _stacktrace(value.get("stacktrace"))) is not None:
        kept["stacktrace"] = stacktrace
    return kept


def _thread(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("id", "name", "crashed", "current", "main")
    kept: dict[str, Any] = {key: value[key] for key in fields if key in value}
    if (stacktrace := _stacktrace(value.get("stacktrace"))) is not None:
        kept["stacktrace"] = stacktrace
    return kept


def _stacktrace(stacktrace: object) -> dict[str, Any] | None:
    if not isinstance(stacktrace, Mapping):
        return None
    frames = _values(cast(Mapping[str, Any], stacktrace).get("frames")) or []
    return {
        "frames": [{key: frame[key] for key in _FRAME_FIELDS if key in frame} for frame in frames]
    }
