"""Parse a pasted job posting into the parts tailoring matches against (FR-TLR-1).

A light-tier prompt reads the posting. Applicant tracking systems match skills and terms as the
posting spells them, so each one is checked against the text. A term that differs only in case
takes the posting's spelling. A term the posting doesn't contain is dropped and reported.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.schema.jobs import JobPosting

PROMPT_NAME = "posting_parsing"
PROMPT_VERSION = 1


@dataclass(frozen=True)
class ParsedPosting:
    posting: JobPosting
    dropped: tuple[str, ...]
    """Skills and terms the model returned that the posting doesn't contain."""


def parse_posting(text: str, client: LLMClient, prompt: Prompt | None = None) -> ParsedPosting:
    """Parse `text`, a job posting as pasted, with the light-tier model."""
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    result = client.call("posting_parsing", prompt, JobPosting, variables={"posting": text})
    return exact_spellings(result.output, text)


def exact_spellings(posting: JobPosting, text: str) -> ParsedPosting:
    """Keep the skills and terms the posting contains, spelled the way it spells them."""
    normalized = " ".join(text.split())
    dropped: list[str] = []

    def exact(items: Iterable[str]) -> tuple[str, ...]:
        kept: list[str] = []
        for item in items:
            found = _find(item, normalized)
            if found is None:
                dropped.append(item)
            elif found not in kept:
                kept.append(found)
        return tuple(kept)

    fixed = posting.model_copy(
        update={
            "title": _find(posting.title, normalized) or posting.title,
            "company": posting.company and (_find(posting.company, normalized) or posting.company),
            "must_have": exact(posting.must_have),
            "nice_to_have": exact(posting.nice_to_have),
            "key_terms": exact(posting.key_terms),
        }
    )
    return ParsedPosting(posting=fixed, dropped=tuple(dropped))


def _find(term: str, text: str) -> str | None:
    """`term` as `text` spells it, as whole words, or None. An exact match wins over one that
    differs only in case."""
    words = term.split()
    if not words:
        return None
    body = r"\s+".join(re.escape(word) for word in words)
    pattern = rf"(?<!\w){body}(?!\w)"
    match = re.search(pattern, text) or re.search(pattern, text, re.IGNORECASE)
    return match[0] if match else None
