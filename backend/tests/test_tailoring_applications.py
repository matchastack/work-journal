import hashlib
from datetime import date
from pathlib import Path

from app.schema.jobs import Application, JobPosting, SwapUse
from app.schema.profile import Profile
from app.schema.variant import Variant
from app.schema.verification import VerifierReport
from app.selection import Selection, select
from app.tailoring.applications import (
    LoggedApplication,
    ResumeChecks,
    application_id,
    record,
)
from app.tailoring.ats import SECTION_HEADINGS, printed_sections
from app.tailoring.coverage import printed_texts
from app.tailoring.tailor import Phrasing, Tailored
from app.validate.lint import Finding, LintReport

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
POSTING = JobPosting.model_validate_json(
    (FIXTURES / "postings" / "backend.json").read_text(encoding="utf-8")
)
PDF = b"%PDF-1.5 tailored"
TODAY = date(2026, 10, 15)
SWAPPED = "Built event-driven Python services that process order events from a message queue."
REPHRASED = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes."
WARNING = Finding(rule="R4", severity="warning", location="x", message="shared metric")


def pdf_text(printed: Selection) -> str:
    """Text as pypdf would extract it from the PDF of `printed`: its headings, then the rest."""
    headings = [SECTION_HEADINGS[section] for section in printed_sections(printed)]
    return "\n".join([*headings, *(text for _, text in printed_texts(printed))])


def resume() -> Selection:
    variant = Variant(id="tailored", name="Backend", role_type="backend")
    return select(PROFILE, variant).model_copy(update={"max_pages": 1})


def phrasing(bullet_id: str, text: str) -> Phrasing:
    report = VerifierReport(text=text, passed=True, fact_ids=("fact_nw_export",))
    return Phrasing(bullet_id=bullet_id, original="before", report=report)


def tailored(printed: Selection) -> Tailored:
    return Tailored(
        posting=POSTING,
        selection=printed,
        swaps=(phrasing("nw_orders", SWAPPED),),
        proposals=(phrasing("nw_export", REPHRASED), phrasing("rb_app", "Built an app.")),
    )


def run(printed: Selection, accepted: set[str]) -> tuple[Application, ResumeChecks]:
    return record(
        tailored(printed),
        printed,
        accepted=accepted,
        rules=LintReport(findings=(WARNING,)),
        posting_text="Senior Backend Engineer, Payments at Fabrikam Logistics...",
        pdf=PDF,
        text=pdf_text(printed),
        applied_on=TODAY,
        suffix="3f9a2c",
    )


def test_an_application_records_what_went_into_the_resume() -> None:
    printed = resume()
    application, checks = record(
        tailored(printed),
        printed,
        accepted={"nw_export"},
        rules=LintReport(findings=(WARNING,)),
        posting_text="the posting as pasted",
        pdf=PDF,
        text=pdf_text(printed),
        applied_on=TODAY,
        suffix="3f9a2c",
    )
    assert application.id == "20261015-fabrikam-logistics-senior-backend-engineer-3f9a2c"
    assert (application.company, application.position) == (
        "Fabrikam Logistics",
        "Senior Backend Engineer, Payments",
    )
    assert application.applied_on == TODAY
    assert application.posting == POSTING
    assert application.posting_text == "the posting as pasted"
    assert application.title_variant == "Software Engineer", "the current role's title"
    assert application.bullet_ids == ("nw_orders", "nw_export", "ss_minor", "rb_app")
    assert application.swaps_used == (
        SwapUse(bullet_id="nw_orders", text=SWAPPED),
        SwapUse(bullet_id="nw_export", text=REPHRASED),
    ), "the swap, and the new phrasing you approved; not the one you declined"
    assert application.pdf_sha256 == hashlib.sha256(PDF).hexdigest()
    assert [report.text for report in checks.texts] == [SWAPPED, REPHRASED]
    assert checks.rules.findings == (WARNING,)
    assert [entry.term for entry in checks.coverage.covered][:2] == ["Python", "PostgreSQL"]
    assert checks.ats is not None
    assert checks.ats.passed, checks.ats.findings
    assert checks.ats.terms == len(checks.coverage.covered)


def test_the_ats_check_of_the_pdfs_text_is_kept() -> None:
    printed = resume()
    _, checks = record(
        tailored(printed),
        printed,
        accepted=set(),
        rules=LintReport(),
        posting_text="text",
        pdf=PDF,
        text="Education\nWork Experience",
        applied_on=TODAY,
        suffix="ab12cd",
    )
    assert checks.ats is not None
    codes = {finding.code for finding in checks.ats.findings}
    assert codes == {"term_missing", "section_missing"}


def test_only_what_the_pdf_shows_is_recorded() -> None:
    printed = resume()
    fitted = printed.model_copy(
        update={
            "work": (printed.work[0].model_copy(update={"bullets": printed.work[0].bullets[1:]}),),
            "projects": (),
        }
    )
    application, checks = record(
        tailored(printed),
        fitted,
        accepted={"nw_export", "rb_app"},
        rules=LintReport(),
        posting_text="text",
        pdf=PDF,
        text=pdf_text(fitted),
        applied_on=TODAY,
        suffix="ab12cd",
    )
    assert application.bullet_ids == ("nw_export", "ss_minor")
    assert [use.bullet_id for use in application.swaps_used] == ["nw_export"]
    assert [report.text for report in checks.texts] == [REPHRASED]
    assert "full-text search" not in [entry.term for entry in checks.coverage.covered]


def test_without_a_company_or_a_role_the_application_still_reads() -> None:
    posting = JobPosting(title="Data Engineer")
    assert application_id(posting, TODAY, "ab12cd") == "20261015-data-engineer-ab12cd"
    printed = resume().model_copy(update={"work": ()})
    untitled = Tailored(posting=posting, selection=printed)
    application, _ = record(
        untitled,
        printed,
        accepted=set(),
        rules=LintReport(),
        posting_text="text",
        pdf=PDF,
        text=pdf_text(printed),
        applied_on=TODAY,
        suffix="ab12cd",
    )
    assert application.company == "Unknown company"
    assert application.title_variant == "Data Engineer"


def test_a_long_name_keeps_the_id_within_its_limit() -> None:
    posting = JobPosting(title="Senior " * 20, company="Fabrikam")
    assert len(application_id(posting, TODAY, "ab12cd")) <= 64


def test_a_logged_application_reads_back() -> None:
    printed = resume()
    application, checks = run(printed, {"nw_export"})
    logged = LoggedApplication.model_validate(
        {"application": application, "checks": checks, "pdf": "local/resumes/fabrikam.pdf"}
    )
    assert LoggedApplication.model_validate_json(logged.model_dump_json(by_alias=True)) == logged
