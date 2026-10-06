"""The parts of Telegram's updates the bot reads (https://core.telegram.org/bots/api#update).

The models keep Telegram's snake_case names and ignore every other field, so a new field in the
Bot API never breaks the webhook.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


class TelegramModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Chat(TelegramModel):
    id: int
    type: str
    """`private` for a chat with one person; groups and channels have other types."""


class Message(TelegramModel):
    message_id: int
    """Unique within its chat."""
    chat: Chat
    date: int
    """When it was sent, in Unix time."""
    edit_date: int | None = None
    """When it was last edited, in Unix time."""
    text: str | None = None
    caption: str | None = None
    """The text sent with a photo or a file."""

    @property
    def sent_at(self) -> datetime:
        return datetime.fromtimestamp(self.date, UTC)

    @property
    def edited_at(self) -> datetime:
        return datetime.fromtimestamp(self.edit_date or self.date, UTC)

    @property
    def body(self) -> str | None:
        """The message's text, or the caption of a photo or file."""
        return self.text if self.text is not None else self.caption


class Update(TelegramModel):
    update_id: int
    """Increases with each update, and identifies one even when Telegram sends it again."""
    message: Message | None = None
    edited_message: Message | None = None
