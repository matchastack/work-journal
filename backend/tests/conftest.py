import os
import shutil

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip LaTeX tests where TeX isn't installed. CI installs it, so they always run there."""
    if shutil.which("latexmk") or os.environ.get("CI"):
        return
    skip = pytest.mark.skip(reason="needs latexmk and pdfLaTeX; see backend/templates/README.md")
    for item in items:
        if "latex" in item.keywords:
            item.add_marker(skip)
