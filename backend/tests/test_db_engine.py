import pytest

from app.db.engine import async_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://app@db:5432/app", "postgresql+asyncpg://app@db:5432/app"),
        ("postgres://app@db/app", "postgresql+asyncpg://app@db/app"),
        ("postgresql+asyncpg://app@db/app", "postgresql+asyncpg://app@db/app"),
    ],
)
def test_urls_use_the_asyncpg_driver(url: str, expected: str) -> None:
    assert async_url(url) == expected
