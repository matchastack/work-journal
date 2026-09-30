"""Compile LaTeX to PDF in a sandbox, and inspect the result (FR-RES-3, FR-RES-4, NFR-SEC-3).

Each compile runs `latexmk -pdf` (pdfLaTeX) in a new temporary folder, deleted afterwards:
- shell escape is off, and TeX may read and write files only inside that folder (kpathsea's
  paranoid `openin_any` and `openout_any`);
- latexmk reads no configuration files, and HOME is the folder, so no user settings apply;
- the whole process group is killed at the timeout, 30 s by default.

pypdf counts the pages and extracts the text. A compile log can quote the document, so it goes to
the owner (the CLI or the web app) and never to application logs.
"""

import os
import re
import signal
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import DictionaryObject

COMPILE_TIMEOUT_S = 30.0
_MAIN = "resume"
_LATEXMK = [
    "latexmk",
    "-pdf",
    "-norc",
    "-interaction=nonstopmode",
    "-halt-on-error",
    "-no-shell-escape",
    "-file-line-error",
    f"{_MAIN}.tex",
]
_ERROR_LINE = re.compile(r"^(?:\./)?[^:\s]+\.tex:\d+: .+|^! .+", re.MULTILINE)
_PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class CompiledPdf:
    pdf: bytes
    pages: int
    text: str
    """The text pypdf extracts, which is roughly what an applicant tracking system reads."""
    bitmap_fonts: bool
    """Some text is set in a bitmap (Type 3) font, which looks rough and extracts badly."""


class CompileError(Exception):
    """LaTeX failed. `log` is pdfLaTeX's log, which can quote the document."""

    def __init__(self, message: str, log: str) -> None:
        super().__init__(message)
        self.log = log


def compile_latex(
    source: str, *, files: Mapping[str, bytes] | None = None, timeout: float = COMPILE_TIMEOUT_S
) -> CompiledPdf:
    """Compile `source` as the main document. `files` are other files it needs, by file name."""
    with tempfile.TemporaryDirectory(prefix="wj-latex-") as folder:
        work = Path(folder)
        for name, content in (files or {}).items():
            if not _PLAIN_NAME.fullmatch(name):
                raise ValueError(f"not a plain file name: {name!r}")
            (work / name).write_bytes(content)
        (work / f"{_MAIN}.tex").write_text(source, encoding="utf-8")
        _run_latexmk(work, timeout)
        pdf = (work / f"{_MAIN}.pdf").read_bytes()
    return inspect_pdf(pdf)


def inspect_pdf(pdf: bytes) -> CompiledPdf:
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(page.extract_text() for page in reader.pages)
    subtypes = {
        str(font.get_object().get("/Subtype"))
        for page in reader.pages
        for font in _fonts(page).values()
    }
    bitmap_fonts = "/Type3" in subtypes
    return CompiledPdf(pdf=pdf, pages=len(reader.pages), text=text, bitmap_fonts=bitmap_fonts)


def _fonts(page: DictionaryObject) -> DictionaryObject:
    resources = page.get("/Resources")
    fonts = resources.get_object().get("/Font") if resources is not None else None
    return fonts.get_object() if fonts is not None else DictionaryObject()


def _run_latexmk(work: Path, timeout: float) -> None:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(work),
        "openin_any": "p",
        "openout_any": "p",
        "shell_escape": "f",
        "max_print_line": "10000",  # don't wrap log lines, so error messages stay whole
    }
    try:
        process = subprocess.Popen(
            _LATEXMK,
            cwd=work,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise CompileError(
            "latexmk isn't installed; see backend/templates/README.md", log=""
        ) from None
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise CompileError(f"LaTeX took longer than {timeout:g} s", _read_log(work)) from None
    if process.returncode != 0 or not (work / f"{_MAIN}.pdf").exists():
        log = _read_log(work) or output.decode("utf-8", errors="replace")
        error = _ERROR_LINE.search(log)
        message = f"LaTeX failed: {error[0].strip()}" if error else "LaTeX failed; see the log"
        raise CompileError(message, log)


def _read_log(work: Path) -> str:
    path = work / f"{_MAIN}.log"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
