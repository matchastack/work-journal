from importlib.metadata import version

import app


def test_version_comes_from_package_metadata() -> None:
    assert app.__version__ == version("work-journal")
    assert app.__version__
