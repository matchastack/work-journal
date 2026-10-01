import time
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject

from app.render.compile import CompileError, compile_latex, inspect_pdf


def document(body: str) -> str:
    return "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n"


@pytest.mark.latex
def test_a_document_compiles_with_its_pages_and_text() -> None:
    compiled = compile_latex(document(r"Hello, world. \newpage Second page."))
    assert compiled.pdf.startswith(b"%PDF")
    assert compiled.pages == 2
    assert "Hello, world." in compiled.text
    assert not compiled.bitmap_fonts


@pytest.mark.latex
def test_extra_files_are_available_to_the_document() -> None:
    source = r"\documentclass{article}\usepackage{greeting}\begin{document}\greeting\end{document}"
    style = rb"\newcommand{\greeting}{Hello from a style file.}"
    assert "Hello from a style file." in compile_latex(source, files={"greeting.sty": style}).text


@pytest.mark.parametrize("name", ["../escape.sty", "sub/file.sty", ".hidden", ""])
def test_extra_files_need_plain_names(name: str) -> None:
    with pytest.raises(ValueError, match="not a plain file name"):
        compile_latex(document("Hello."), files={name: b""})


@pytest.mark.latex
def test_a_latex_error_reports_its_first_error_and_the_log() -> None:
    with pytest.raises(
        CompileError, match=r"resume\.tex:\d+: Undefined control sequence"
    ) as caught:
        compile_latex(document(r"\notacommand"))
    assert "Undefined control sequence" in caught.value.log


@pytest.mark.latex
def test_a_compile_that_runs_too_long_is_stopped() -> None:
    started = time.perf_counter()
    with pytest.raises(CompileError, match="longer than 1 s"):
        compile_latex(document(r"\def\forever{\forever}\forever"), timeout=1)
    assert time.perf_counter() - started < 10


@pytest.mark.latex
def test_shell_escape_is_off() -> None:
    with pytest.raises(CompileError) as caught:
        compile_latex(document(r"\immediate\write18{echo pwned}\notacommand"))
    assert "runsystem(echo pwned)...disabled" in caught.value.log


@pytest.mark.latex
def test_files_outside_the_folder_cant_be_read(tmp_path: Path) -> None:
    secret = tmp_path / "secret.tex"
    secret.write_text("Top secret.", encoding="utf-8")
    with pytest.raises(CompileError, match="not found"):
        compile_latex(document(f"\\input{{{secret}}}"))


@pytest.mark.latex
def test_files_outside_the_folder_cant_be_written() -> None:
    body = r"\newwrite\out\immediate\openout\out=../escaped.tex\immediate\write\out{x}"
    with pytest.raises(CompileError, match="can't write on file"):
        compile_latex(document(body))


def test_bitmap_fonts_are_reported() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    font = DictionaryObject(
        {NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type3")}
    )
    fonts = DictionaryObject({NameObject("/F1"): font})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
    buffer = BytesIO()
    writer.write(buffer)
    inspected = inspect_pdf(buffer.getvalue())
    assert inspected.pages == 1
    assert inspected.bitmap_fonts
