"""The application log: each tailored resume, with what went into it (FR-TLR-8, FR-FID-8).

`record` turns a tailored resume, as fitted to its page, into an `Application`: the posting and
its text as given, the bullets and swaps it shows, the title it gives the current role, and the
PDF's hash. The checks are kept with it: the verifier's report on each swap and new phrasing it
uses, the resume rules' warnings, the keyword coverage report and the ATS check of the PDF's
text (`app/tailoring/ats.py`). With the database, `wj tailor`
stores the PDF and the posting beside it; without, it writes a `LoggedApplication` file.
"""

import hashlib
import re
from collections.abc import Collection
from datetime import date

from app.schema.common import Model
from app.schema.jobs import Application, JobPosting, SwapUse
from app.schema.verification import VerifierReport
from app.selection import Selection
from app.tailoring.ats import AtsReport, check_text
from app.tailoring.coverage import CoverageReport, coverage
from app.tailoring.tailor import Tailored
from app.validate.lint import LintReport

_SLUG_LENGTH = 48
"""The longest company and title part of an ID, in whole words: with the date and a 6-character
suffix, an ID stays within 64 characters."""


class ResumeChecks(Model):
    """What the checks found in one tailored resume (FR-FID-8)."""

    texts: tuple[VerifierReport, ...] = ()
    """The verifier's report on each swap and new phrasing the resume shows."""
    rules: LintReport = LintReport()
    """The resume rules' findings. An error doesn't stop the PDF being written; it's kept here,
    and the resume mustn't be sent until it's fixed (R2)."""
    coverage: CoverageReport = CoverageReport()
    ats: AtsReport | None = None
    """The ATS check of the PDF's text; None in applications logged before it existed."""


class LoggedApplication(Model):
    """An application as `wj tailor` logs it without the database, in `local/applications/`."""

    application: Application
    checks: ResumeChecks
    pdf: str | None = None
    """Where the PDF was written, unless only its LaTeX source was."""
    tex: str | None = None
    """Where the LaTeX source was written, unless only the PDF was."""


def record(
    tailored: Tailored,
    printed: Selection,
    *,
    accepted: Collection[str],
    rules: LintReport,
    posting_text: str,
    pdf: bytes,
    text: str,
    applied_on: date,
    suffix: str,
) -> tuple[Application, ResumeChecks]:
    """The application for a tailored resume, and its checks.

    `printed` is what the PDF shows, after cuts to fit the page, `text` the PDF's text as pypdf
    extracts it, and `accepted` the bullets whose new phrasings the owner approved. `suffix`
    makes the ID unique, such as random hex.
    """
    posting = tailored.posting
    bullet_ids = tuple(
        bullet.id
        for item in (*printed.work, *printed.education, *printed.projects)
        for bullet in item.bullets
    )
    shown = set(bullet_ids)
    used = [
        *tailored.swaps,
        *(proposal for proposal in tailored.proposals if proposal.bullet_id in accepted),
    ]
    used = [phrasing for phrasing in used if phrasing.bullet_id in shown]
    application = Application(
        id=application_id(posting, applied_on, suffix),
        applied_on=applied_on,
        company=posting.company or "Unknown company",
        position=posting.title,
        posting=posting,
        posting_text=posting_text,
        variant_id=printed.variant_id,
        title_variant=_current_title(printed) or posting.title,
        bullet_ids=bullet_ids,
        swaps_used=tuple(SwapUse(bullet_id=p.bullet_id, text=p.text) for p in used),
        pdf_sha256=hashlib.sha256(pdf).hexdigest(),
    )
    report = coverage(printed, posting)
    checks = ResumeChecks(
        texts=tuple(phrasing.report for phrasing in used),
        rules=rules,
        coverage=report,
        ats=check_text(text, printed, [entry.term for entry in report.covered]),
    )
    return application, checks


def application_id(posting: JobPosting, applied_on: date, suffix: str) -> str:
    """e.g. `20261015-fabrikam-logistics-backend-engineer-3f9a2c`."""
    name = " ".join(part for part in (posting.company, posting.title) if part)
    slug = ""
    for word in re.findall(r"[a-z0-9]+", name.casefold()):
        if len(slug) + len(word) + 1 > _SLUG_LENGTH:
            break
        slug = f"{slug}-{word}" if slug else word
    return "-".join(part for part in (f"{applied_on:%Y%m%d}", slug, suffix) if part)


def _current_title(printed: Selection) -> str | None:
    """The title the resume gives the current role, or its first role if none is current. The
    LinkedIn pack compares it with the role's title there (FR-LIN-4)."""
    current = next((role for role in printed.work if role.end_date is None), None)
    role = current or next(iter(printed.work), None)
    return role.title if role else None
