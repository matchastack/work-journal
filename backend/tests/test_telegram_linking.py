import pytest

from app.telegram.linking import deep_link, start_token


@pytest.mark.parametrize(
    ("text", "token"),
    [
        ("/start Ab3_-x", "Ab3_-x"),
        ("/start@wj_bot Ab3_-x", "Ab3_-x"),
        ("/start " + "x" * 64, "x" * 64),
        ("/start", None),
        ("/start two words", None),
        ("/start " + "x" * 65, None),
        ("/start not=allowed", None),
        ("Remember to /start Ab3", None),
    ],
)
def test_the_token_comes_from_a_start_command(text: str, token: str | None) -> None:
    """A link to the bot sends `/start <token>`: up to 64 letters, digits, `_` and `-`."""
    assert start_token(text) == token


def test_the_link_opens_the_bot_with_the_token() -> None:
    assert deep_link("wj_bot", "Ab3_-x") == "https://t.me/wj_bot?start=Ab3_-x"
