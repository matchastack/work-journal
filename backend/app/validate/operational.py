"""Operational detail: what text about a sensitive role must leave out (resume rule R5).

A role marked sensitive is described by its architecture and outcomes only. Code can't tell
architecture from operations in general, so it looks for the specifics that only operational
detail has, each a pattern:

- network and host details: IP addresses, host names, server names such as `prod-db-01`, ports
  and web addresses;
- file paths, configuration names such as `PAYMENTS_API_URL`, and code or commands in backticks;
- ticket, incident and change numbers, such as `INC0012345` or `PAY-1234`, and CVE numbers;
- credentials and access: SSH, private and secret keys, and root, admin or shared passwords and
  access;
- times of day, such as `02:00 UTC` or `3am`, the schedule of a job;
- email addresses.

Each finding names what matched and what it looks like. It errs towards flagging: in the Inbox
a flagged change still waits for the owner, who can accept it by hand (FR-FID-7). Standard names
that look like ticket numbers, such as `SHA-256` or `ISO-27001`, aren't flagged.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Detail:
    """Something in a text that looks like operational detail."""

    text: str
    """What matched, as the text has it."""
    kind: str
    """What it looks like, such as `an IP address`."""


_STANDARDS = frozenset(
    {"AES", "AMD", "ARM", "COVID", "CVE", "DDR", "ECMA", "ES", "FIPS", "GPT", "HTTP", "IEC"}
    | {"IEEE", "IPV", "ISO", "LTE", "MD", "NIST", "PCI", "PEP", "RFC", "RSA", "SHA", "SOC"}
    | {"SSL", "TLS", "USB", "UTF", "WCAG", "X86"}
)
"""Prefixes of standards and other names that read like ticket keys: `SHA-256`, `ISO-27001`."""

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a web address", re.compile(r"\b(?:https?|ftp|sftp|ssh|s3|gs)://[^\s,;)]+", re.IGNORECASE)),
    ("an email address", re.compile(r"\b[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")),
    (
        "an IP address",
        re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![\w.]*\w)"),
    ),
    (
        "a host name",
        re.compile(
            r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|local|localdomain|corp|lan|intranet|"
            r"intra|private|prod|staging)\b"
            r"|\b(?:[a-z0-9-]+\.){2,}(?:com|net|org|io|cloud|dev|app|co|ai|gov|edu|[a-z]{2})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "a server name",
        re.compile(
            r"\b(?:prod|prd|stg|stage|staging|dev|qa|uat|db|web|app|api|srv|svr|server|node|"
            r"host|vm|k8s|cluster|worker|cache|proxy|lb|gw)(?:[-_][a-z0-9]+)*[-_]?\d{1,4}\b"
        ),
    ),
    (
        "a port",
        re.compile(
            r"\bports?\s+\d{2,5}\b|\b[A-Za-z0-9][A-Za-z0-9.-]*(?<=[A-Za-z]):\d{2,5}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "a file path",
        re.compile(r"(?<![\w/.~])(?:~|\.{1,2})?(?:/[\w.@-]+){2,}/?|\b[A-Z]:\\[^\s,;]+"),
    ),
    ("a configuration name", re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")),
    ("code or a command", re.compile(r"`[^`\n]+`")),
    ("a CVE number", re.compile(r"\bCVE-\d{4}-\d{3,}\b", re.IGNORECASE)),
    (
        "a ticket or incident number",
        re.compile(
            r"\b(?:INC|CHG|RITM|REQ|PRB|TASK)\d{4,}\b"
            r"|\b(?:tickets?|incidents?|change requests?|cases?|issues?|bugs?)\s*#\s*\d+\b"
            r"|\b(?:ticket|incident|change request|case)\s+\d{2,}\b",
            re.IGNORECASE,
        ),
    ),
    ("a ticket number", re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d{2,}\b")),
    (
        "credentials or access",
        re.compile(
            r"\b(?:root|admin|administrator|sudo|superuser|service[- ]account)"
            r" (?:passwords?|credentials?|access|rights|privileges|accounts?)\b"
            r"|\b(?:shared|database|db) (?:passwords?|credentials?)\b"
            r"|\b(?:ssh|private|secret|signing) keys?\b|\bpasswd\b",
            re.IGNORECASE,
        ),
    ),
    (
        "a time of day",
        re.compile(
            r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:\s?(?:UTC|GMT|[ap]\.?m\.?))?(?!\d)"
            r"|\b(?:1[0-2]|0?[1-9])\s?[ap]\.?m\b\.?",
            re.IGNORECASE,
        ),
    ),
)


def find_operational_detail(text: str) -> tuple[Detail, ...]:
    """What in `text` looks like operational detail, in the order it comes, each once. Where
    two patterns match the same words, such as a web address and the host name in it, the first
    to start, then the longer, is kept."""
    found: list[tuple[int, int, Detail]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if kind == "a ticket number" and match[1].upper() in _STANDARDS:
                continue
            found.append((match.start(), match.end(), Detail(text=match[0], kind=kind)))
    found.sort(key=lambda item: (item[0], -item[1]))
    kept: list[Detail] = []
    end = -1
    seen: set[str] = set()
    for start, stop, detail in found:
        if start < end:
            continue
        end = stop
        if detail.text.casefold() not in seen:
            seen.add(detail.text.casefold())
            kept.append(detail)
    return tuple(kept)
