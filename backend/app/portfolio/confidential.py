"""Keeping confidential terms off the portfolio page and its resumes (T-049, NFR-PRIV-4).

What's screened is what visitors get, not what it's made from, so nothing the template or a PDF
adds can slip past:

- **The page.** Its HTML as served: every text, the search data (JSON-LD) and the values of
  attributes, such as links and the page's description. Only what the app makes itself is left
  out: CSS classes and IDs, the template's own meta tags, the addresses of the page's own CSS,
  JavaScript and resume downloads, and its canonical address, whose only owner's text is the
  handle, screened on its own. Each
  match says where it is: the section and the heading of the item, such as
  `Experience: Engineer`.
- **A resume.** The text of its stored PDF, as pypdf extracts it (with words LaTeX hyphenated at
  a line's end joined again), the addresses its links go to and its title, author, subject and
  keywords. A PDF that can't be read can't be checked, so it isn't offered while there are terms.

`app/portfolio/publishing.py` decides what a match blocks. Terms and the text around them are
never logged.
"""

import json
import re
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from html.parser import HTMLParser
from io import BytesIO
from threading import Lock

from pypdf import PageObject, PdfReader
from pypdf.errors import PyPdfError

from app.validate.confidential import TermMatch, find_terms, screen

ADDRESS = "Page address"
HEAD = "Page title, description and search data"
PAGE = "Page"
_SECTIONS = {
    "top": "Introduction",
    "about": "About",
    "experience": "Experience",
    "projects": "Projects",
    "skills": "Skills",
    "contact": "Contact",
}
_PARTS = {"header": "Menu", "footer": "Footer"}
_APP_ATTRIBUTES = frozenset({"class", "id", "aria-controls", "aria-labelledby"})
"""Attributes whose values come from the template alone."""
_OWNERS_META = frozenset({"description", "og:title", "og:description"})
_HYPHENATED = re.compile(r"(\w)-[ \t]*\n\s*(\w)")
_PDF_CACHE_SIZE = 64


def page_matches(html: str, *, handle: str | None, terms: Sequence[str]) -> tuple[TermMatch, ...]:
    """The confidential terms the page's HTML, or its handle, names. A term found in the page's
    head only repeats what the page shows, so the head is named only for a term found nowhere
    else."""
    if not terms:
        return ()
    texts: list[tuple[str, str]] = [(ADDRESS, handle)] if handle else []
    reader = _PageText()
    reader.feed(html)
    reader.close()
    texts += reader.texts
    matches = screen(texts, terms)
    shown = {match.term for match in matches if match.where != HEAD}
    return tuple(match for match in matches if match.where != HEAD or match.term not in shown)


def pdf_texts(pdf: bytes) -> tuple[str, ...] | None:
    """What a visitor can read in a PDF: its text, the same with hyphenated line breaks joined,
    its links' addresses and its document information. None when pypdf can't read it."""
    try:
        reader = PdfReader(BytesIO(pdf))
        text = "\n".join(page.extract_text() for page in reader.pages)
        links = [uri for page in reader.pages for uri in _link_addresses(page)]
        info = reader.metadata
        keys = ("/Title", "/Author", "/Subject", "/Keywords")
        details = [str(info.get(key) or "") for key in keys] if info is not None else []
    except (PyPdfError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return (text, _HYPHENATED.sub(r"\1\2", text), *links, *(d for d in details if d))


class PdfTerms:
    """Which confidential terms stored PDFs name. A PDF's text is kept for the last few PDFs, by
    hash, since a page shows the same resumes on every visit and PDFs never change."""

    def __init__(self, size: int = _PDF_CACHE_SIZE) -> None:
        self._texts: OrderedDict[str, tuple[str, ...] | None] = OrderedDict()
        self._size = size
        self._lock = Lock()

    def known(self, sha256: str) -> bool:
        with self._lock:
            return sha256 in self._texts

    def remember(self, sha256: str, pdf: bytes) -> None:
        texts = pdf_texts(pdf)
        with self._lock:
            self._texts[sha256] = texts
            self._texts.move_to_end(sha256)
            while len(self._texts) > self._size:
                self._texts.popitem(last=False)

    def terms(self, sha256: str, terms: Sequence[str]) -> tuple[str, ...] | None:
        """The terms the PDF names, in the order of `terms`; None when it couldn't be read or
        isn't known."""
        with self._lock:
            if sha256 not in self._texts:
                return None
            self._texts.move_to_end(sha256)
            texts = self._texts[sha256]
        if texts is None:
            return None
        found = {term for text in texts for term in find_terms(text, terms)}
        return tuple(term for term in terms if term in found)


PDF_TERMS = PdfTerms()


def _link_addresses(page: PageObject) -> Iterator[str]:
    annotations = page.get("/Annots")
    if annotations is None:
        return
    for annotation in annotations.get_object():
        action = annotation.get_object().get("/A")
        uri = action.get_object().get("/URI") if action is not None else None
        if uri is not None:
            yield str(uri)


class _PageText(HTMLParser):
    """Collects what the page shows, with where each piece is."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[tuple[str, str]] = []
        self._head = False
        self._part: str | None = None
        self._item: str | None = None
        self._heading: list[str] | None = None
        """The text of the item's heading, while it's read."""
        self._in_heading: list[str] = []
        """Attribute values inside the heading, such as its link, kept until it's read."""
        self._script: list[str] | None = None
        self._canonical: str | None = None
        """The page's own address, which the search data repeats as its `url`."""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "head":
            self._head = True
        elif tag == "section" and values.get("id") in _SECTIONS:
            self._part, self._item = _SECTIONS[values["id"] or ""], None
        elif tag in _PARTS:
            self._part, self._item = _PARTS[tag], None
        elif tag == "h3":
            self._heading = []
        elif tag == "script" and values.get("type") == "application/ld+json":
            self._script = []
        elif tag == "link" and values.get("rel") == "canonical":
            self._canonical = values.get("href")
        for name, value in attrs:
            if value and name not in _APP_ATTRIBUTES and not _made_by_app(tag, name, values):
                if self._heading is not None and tag != "h3":
                    self._in_heading.append(value)
                else:
                    self.texts.append((self._where(), value))

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._head = False
        elif tag == "section" or tag in _PARTS:
            self._part, self._item = None, None
        elif tag == "article":
            self._item = None
        elif tag == "h3" and self._heading is not None:
            heading = " ".join(" ".join(self._heading).split())
            self._heading = None
            self._item = heading or None
            inside, self._in_heading = self._in_heading, []
            self.texts += [(self._where(), text) for text in (heading, *inside) if text]
        elif tag == "script" and self._script is not None:
            content, self._script = "".join(self._script), None
            strings = _json_strings(content)
            self.texts += [(HEAD, text) for text in strings if text != self._canonical]

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        elif self._heading is not None:
            self._heading.append(data)
        elif data.strip():
            self.texts.append((self._where(), data))

    def _where(self) -> str:
        if self._head:
            return HEAD
        part = self._part or PAGE
        return f"{part}: {self._item}" if self._item else part


def _made_by_app(tag: str, name: str, values: dict[str, str | None]) -> bool:
    """Addresses the app makes: the page's own files and downloads, its canonical address, and
    links within the page."""
    value = values.get(name) or ""
    if name == "href" and value.startswith("#"):
        return True
    if tag == "meta":  # only the page's description and title come from the owner
        kind = values.get("name") or values.get("property")
        return name != "content" or kind not in _OWNERS_META
    return (
        (tag == "link" and name == "href" and values.get("rel") in ("stylesheet", "canonical"))
        or (tag == "script" and name == "src")
        or (tag == "a" and name == "href" and "download" in values)
    )


def _json_strings(content: str) -> list[str]:
    """Every string in the search data, or its raw text if it isn't JSON."""
    try:
        data: object = json.loads(content)
    except ValueError:
        return [content]
    found: list[str] = []
    stack = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            stack.extend(reversed(list(value.values())))  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        elif isinstance(value, list):
            stack.extend(reversed(value))  # pyright: ignore[reportUnknownArgumentType]
    return found
