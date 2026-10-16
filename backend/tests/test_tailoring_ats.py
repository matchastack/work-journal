"""The ATS check, on fixture PDFs.

`fixtures/pdfs/backend-resume.pdf` is the default template's render of `RESUME`, the fictional
person's backend resume with its summary and the skills before the work experience. Render it
again with `render_resume(RESUME)` after changing the template. The other PDFs are built here,
with a font whose bytes 128 and 129 are the "fi" and "fl" ligature glyphs.
"""

from pathlib import Path

import pytest

from app.render.resume import TEMPLATES_DIR, render_resume
from app.schema.profile import Profile
from app.schema.variant import Variant
from app.selection import select
from app.tailoring.ats import (
    SECTION_HEADINGS,
    AtsReport,
    check_pdf,
    check_text,
    printed_sections,
    report_lines,
)

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
RESUME = select(
    PROFILE,
    Variant(
        id="tailored",
        name="Backend",
        role_type="backend",
        include_summary=True,
        section_order=("education", "skills", "work", "projects"),
    ),
)
RENDERED = (FIXTURES / "pdfs" / "backend-resume.pdf").read_bytes()
TERMS = ("Python", "PostgreSQL", "message queue", "full-text search", "Distributed Systems")
HEADINGS = ("Summary", "Education", "Technical Skills", "Work Experience", "Projects")


def pdf(*lines: bytes) -> bytes:
    """A one-page PDF with each of `lines` on a line of its own, in Helvetica."""
    content = b"BT /F1 11 Tf 72 720 Td 14 TL\n"
    content += b"".join(b"(" + line + b") Tj T*\n" for line in lines) + b"ET"
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding << /Type /Encoding "
    font += b"/BaseEncoding /WinAnsiEncoding /Differences [128 /fi 129 /fl] >> >>"
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    page += b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page,
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        font,
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def headed(*body: bytes) -> bytes:
    """A PDF with every heading of `RESUME`, in order, then `body`."""
    return pdf(*(heading.encode() for heading in HEADINGS), *body)


def codes(report: AtsReport) -> list[str]:
    return [finding.code for finding in report.findings]


def test_the_rendered_resume_reads_back_whole() -> None:
    report = check_pdf(RENDERED, RESUME, TERMS)
    assert report.passed, report.findings
    assert report.sections == HEADINGS
    assert report.terms == 5


def test_a_term_the_text_lacks_is_missing() -> None:
    report = check_pdf(RENDERED, RESUME, ("Python", "Kafka"))
    assert codes(report) == ["term_missing"]
    assert report.findings[0].message == (
        '"Kafka" is on the resume but not in the text extracted from the PDF.'
    )


def test_a_term_split_at_a_line_end_is_reported() -> None:
    report = check_pdf(
        headed(b"Built a full-", b"text search for Post-", b"greSQL users"),
        RESUME,
        ("full-text search", "PostgreSQL", "users"),
    )
    assert codes(report) == ["term_hyphenated", "term_hyphenated"]
    assert report.findings[0].message == (
        '"full-text search" is split by a hyphen at the end of a line, so it reads as two words.'
    )


def test_ligatures_and_unmapped_glyphs_are_reported() -> None:
    report = check_pdf(headed(b"Pro\x80led the \x81ow of \\(cid:12\\) orders"), RESUME, ())
    assert codes(report) == ["ligature", "garbled"]
    assert "U+FB01, U+FB02" in report.findings[0].message
    assert report.findings[1].message.startswith("The text has 1 character that didn't extract")
    replacement = check_text("\n".join(HEADINGS) + "\nBuilt � and ", RESUME, ())
    assert codes(replacement) == ["garbled"]
    assert "2 characters" in replacement.findings[0].message


def test_each_section_heading_must_be_a_line_in_order() -> None:
    shuffled = pdf(b"Summary", b"Education", b"Work Experience", b"Technical Skills", b"Projects")
    report = check_pdf(shuffled, RESUME, ())
    assert codes(report) == ["section_order"]
    assert report.findings[0].message == (
        "The sections read in the order Summary, Education, Work Experience, Technical Skills, "
        "Projects; the resume prints Summary, Education, Technical Skills, Work Experience, "
        "Projects."
    )
    merged = pdf(b"Summary", b"Education", b"Technical Skills Python", b"Work Experience")
    report = check_pdf(merged, RESUME, ())
    assert codes(report) == ["section_missing", "section_missing"]
    assert report.findings[0].message == (
        'The "Technical Skills" heading isn\'t a line of its own in the text.'
    )


def test_only_the_sections_with_content_are_expected() -> None:
    bare = RESUME.model_copy(update={"summary": None, "projects": ()})
    assert printed_sections(bare) == ["education", "skills", "work"]
    report = check_pdf(pdf(b"Education", b"Technical Skills", b"Work Experience"), bare, ())
    assert report.passed


def test_a_pdf_without_text_or_that_cant_be_read_fails() -> None:
    assert codes(check_pdf(b"not a PDF", RESUME, ("Python",))) == ["unreadable"]
    assert codes(check_pdf(pdf(), RESUME, ())) == ["no_text"]


def test_the_report_says_what_passed_or_what_didnt() -> None:
    assert report_lines(check_pdf(RENDERED, RESUME, TERMS)) == [
        "ATS check passed: the PDF's text has its 5 covered terms and its 5 sections, in order."
    ]
    one = report_lines(
        check_text("Education", RESUME.model_copy(update=_only_education()), ["SQL"])
    )
    assert one == [
        "The ATS check found problems in the PDF's text:",
        '   "SQL" is on the resume but not in the text extracted from the PDF.',
    ]


def _only_education() -> dict[str, object]:
    return {"summary": None, "work": (), "projects": (), "skills": ()}


def test_the_headings_are_the_templates() -> None:
    template = (TEMPLATES_DIR / "default" / "resume.tex").read_text(encoding="utf-8")
    for heading in SECTION_HEADINGS.values():
        assert f"\\section{{{heading}}}" in template


@pytest.mark.latex
def test_a_fresh_render_reads_back_whole() -> None:
    rendered = render_resume(RESUME)
    report = check_pdf(rendered.pdf, rendered.selection, TERMS)
    assert report.passed, report.findings
    assert check_text(rendered.text, rendered.selection, TERMS) == report
