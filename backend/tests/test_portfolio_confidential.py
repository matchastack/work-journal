"""Screening the portfolio page and its resumes for confidential terms (T-049)."""

from pathlib import Path

import pytest

from app.portfolio.build import Link, portfolio_page, render_page
from app.portfolio.confidential import ADDRESS, HEAD, PdfTerms, page_matches, pdf_texts
from app.schema.profile import Profile
from app.validate.confidential import TermMatch

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
RENDERED = (FIXTURES / "pdfs" / "backend-resume.pdf").read_bytes()


def page(profile: Profile = PROFILE, label: str = "Resume") -> str:
    downloads = [Link(text=label, href="/p/casey-example/resumes/0123.pdf")]
    built = portfolio_page(
        profile,
        year=2026,
        site_url="https://journal.example.com/p/casey-example",
        downloads=downloads,
    )
    return render_page(built)


def text_pdf(*lines: str, uri: str | None = None, title: str | None = None) -> bytes:
    """A one-page PDF with `lines` of text, a link to `uri` and a `title`, as a PDF maker might
    write it."""
    shown = " ".join(f"({line}) Tj T*" for line in lines)
    content = f"BT /F1 12 Tf 72 720 Td 14 TL {shown} ET"
    annots = " /Annots [6 0 R]" if uri else ""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R{annots} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        f"<< /Type /Annot /Subtype /Link /Rect [72 700 200 712] /A << /S /URI /URI ({uri}) >> >>"
        if uri
        else "<< >>",
        f"<< /Title ({title}) >>" if title else "<< >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 7 0 R >>\n"
    out += f"{trailer}startxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# --- The page ------------------------------------------------------------------------------------


def test_a_term_on_the_page_is_named_with_where_it_is() -> None:
    html = page()
    assert page_matches(html, handle="casey-example", terms=["northwind"]) == (
        TermMatch(term="northwind", where="Experience: Software Engineer"),
    )
    assert page_matches(html, handle=None, terms=["Recipe Box", "Magna Cum Laude"]) == (
        TermMatch(term="Magna Cum Laude", where="About: Education"),
        TermMatch(term="Recipe Box", where="Projects: Recipe Box"),
    ), "in the order the page shows them"
    assert page_matches(html, handle=None, terms=["Languages"]) == (
        TermMatch(term="Languages", where="Skills: Languages"),
    )


def test_links_downloads_and_the_handle_are_screened() -> None:
    html = page(label="Falcon resume")
    assert page_matches(html, handle="casey-example", terms=["Falcon"]) == (
        TermMatch(term="Falcon", where="Contact"),
    ), "the download button's text"
    assert page_matches(html, handle=None, terms=["casey example"]) == (
        TermMatch(term="casey example", where="Projects: Recipe Box"),
        TermMatch(term="casey example", where="Contact"),
    ), "links' addresses"
    assert page_matches(html, handle="casey-falcon", terms=["falcon"]) == (
        TermMatch(term="falcon", where=ADDRESS),
        TermMatch(term="falcon", where="Contact"),
    )


def test_what_the_template_makes_isnt_the_owners_text() -> None:
    html = page()
    terms = ["portfolio", "rounded", "assets", "css", "download", "heading", "resumes", "0123"]
    terms += ["summary", "profile", "device-width", "noindex"]
    assert page_matches(html, handle="casey-example", terms=terms) == ()
    assert page_matches(html, handle="casey-example", terms=[]) == ()


def test_the_head_is_named_only_for_a_term_shown_nowhere_else() -> None:
    label = PROFILE.basics.model_copy(update={"summary": "Builds the Falcon platform."})
    html = page(PROFILE.model_copy(update={"basics": label}))
    assert page_matches(html, handle=None, terms=["falcon"]) == (
        TermMatch(term="falcon", where="About: Profile"),
    )
    head_only = html.replace("Builds the Falcon platform.</p>", "</p>")
    assert page_matches(head_only, handle=None, terms=["falcon"]) == (
        TermMatch(term="falcon", where=HEAD),
    )


def test_the_search_data_is_read_as_json() -> None:
    html = (
        '<html><head><script type="application/ld+json">{"name": "Project\\nFalcon", '
        '"knowsAbout": ["Orion\\u0026Co"]}</script></head><body></body></html>'
    )
    assert page_matches(html, handle=None, terms=["Project Falcon", "Orion"]) == (
        TermMatch(term="Project Falcon", where=HEAD),
        TermMatch(term="Orion", where=HEAD),
    )
    broken = '<html><head><script type="application/ld+json">{"Falcon"</script></head></html>'
    assert page_matches(broken, handle=None, terms=["falcon"]) == (
        TermMatch(term="falcon", where=HEAD),
    )


# --- Resumes -------------------------------------------------------------------------------------


def test_a_rendered_resumes_text_and_links_are_read() -> None:
    texts = pdf_texts(RENDERED)
    assert texts is not None
    assert "Software Engineer, Northwind Traders" in texts[0]
    assert "https://github.com/casey-example/recipe-box" in texts


@pytest.mark.parametrize(
    ("pdf", "term"),
    [
        (text_pdf("Built the North-", "wind export."), "Northwind"),
        (text_pdf("Shipped Project", "Falcon on time."), "Project Falcon"),
        (text_pdf("Shipped it.", uri="https://falcon.example.com/x"), "falcon"),
        (text_pdf("Shipped it.", title="Falcon resume"), "Falcon"),
    ],
)
def test_a_pdf_names_a_term_in_its_text_links_or_information(pdf: bytes, term: str) -> None:
    cache = PdfTerms()
    cache.remember("a" * 64, pdf)
    assert cache.terms("a" * 64, ["Orion", term]) == (term,)


def test_a_pdf_that_cant_be_read_cant_be_checked() -> None:
    assert pdf_texts(b"%PDF-1.7 not really") is None
    cache = PdfTerms()
    assert not cache.known("b" * 64)
    assert cache.terms("b" * 64, ["Falcon"]) is None, "not known yet"
    cache.remember("b" * 64, b"not a pdf")
    assert cache.known("b" * 64) and cache.terms("b" * 64, ["Falcon"]) is None


def test_the_cache_keeps_the_newest_pdfs() -> None:
    cache = PdfTerms(size=2)
    for name in ("1", "2", "3"):
        cache.remember(name * 64, text_pdf(f"Resume {name}"))
    assert not cache.known("1" * 64)
    assert cache.terms("3" * 64, ["resume"]) == ("resume",)
