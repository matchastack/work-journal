import pytest

from app.db.engine import async_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://app@db:5432/app", "postgresql+asyncpg://app@db:5432/app"),
        ("postgres://app@db/app", "postgresql+asyncpg://app@db/app"),
        ("postgresql+asyncpg://app@db/app", "postgresql+asyncpg://app@db/app"),
        (
            "postgresql://app:pw@ep-x.aws.neon.tech/app?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://app:pw@ep-x.aws.neon.tech/app?ssl=require",
        ),
    ],
)
def test_urls_use_the_asyncpg_driver(url: str, expected: str) -> None:
    """Neon's URLs name libpq's `sslmode` and `channel_binding`, which asyncpg doesn't take."""
    assert async_url(url) == expected
