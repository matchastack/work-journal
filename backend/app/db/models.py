"""Database tables (requirements §13).

v1 has one user, but every row belongs to one (constraint C4): each table has a `user_id` that
references `users`, unless it's listed in `NOT_PER_USER`. `tests/db/test_models.py` checks this.
Constraint names follow `NAMING_CONVENTION`, so migrations can refer to them.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.crypto import EncryptedText
from app.schema.changes import Author, ChangeSetStatus, OpStatus

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
NOT_PER_USER = frozenset({"users", "telegram_updates"})
"""Tables that don't belong to a user. Adding one needs a reason: `telegram_updates` keeps each
raw update the moment it arrives, before anyone knows whose chat it came from (FR-JRN-1)."""


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    """The GitHub account's number, which stays the same when its username changes."""
    github_login: Mapped[str | None] = mapped_column(String(39))
    """The GitHub username at the latest sign-in."""


class UserSession(Base):
    """A signed-in browser.

    The browser's cookie holds a random token, and only the token's SHA-256 hash is stored here,
    so a copy of the database can't be used to sign in.
    """

    __tablename__ = "sessions"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(64))
    """Requests that change something must repeat it in a header (`app/auth/sessions.py`)."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    """One of a user's settings: a key such as `style_notes`, and its value as JSON.

    Each feature validates its own keys with a Pydantic model, so a new setting needs no
    migration.
    """

    __tablename__ = "settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChangeSetRow(Base):
    """Operations proposed together and reviewed together (FR-REV-1). Accepted operations are
    applied as one new profile version (FR-REV-3)."""

    __tablename__ = "change_sets"
    __table_args__ = (
        CheckConstraint("author IN ('owner', 'ai')", name="author"),
        CheckConstraint("status IN ('proposed', 'applied', 'rejected')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    author: Mapped[Author] = mapped_column(String(8))
    status: Mapped[ChangeSetStatus] = mapped_column(String(16), server_default="proposed")
    summary: Mapped[str | None] = mapped_column(Text)
    base_version: Mapped[int | None]
    """The number of the profile version the operations were proposed against."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChangeOpRow(Base):
    """One operation in a change set, with the owner's decision on it (FR-REV-2) and its verifier
    report (FR-FID-8)."""

    __tablename__ = "change_ops"
    __table_args__ = (
        UniqueConstraint("change_set_id", "position"),
        CheckConstraint("status IN ('proposed', 'accepted', 'rejected')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    op: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """The operation (`app/schema/changes.py`)."""
    status: Mapped[OpStatus] = mapped_column(String(16), server_default="proposed")
    reason: Mapped[str | None] = mapped_column(Text)
    """Why the owner rejected it."""
    verifier_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ProfileVersionRow(Base):
    """One version of a user's master profile, as a JSON snapshot (FR-PRF-2).

    Versions are immutable (NFR-DATA-1): a trigger rejects any update, so a change always saves a
    new version, and restoring an old one copies it into a new version (FR-PRF-3).
    """

    __tablename__ = "profile_versions"
    __table_args__ = (
        UniqueConstraint("user_id", "number"),
        CheckConstraint("author IN ('owner', 'ai')", name="author"),
        CheckConstraint("number > 0", name="number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    number: Mapped[int]
    """1, 2, 3, ... for each user."""
    parent: Mapped[int | None]
    """The number of the version this one was made from."""
    author: Mapped[Author] = mapped_column(String(8))
    change_set_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("change_sets.id"))
    """The change set that made this version, if one did."""
    restored_from: Mapped[int | None]
    """The number of the version this one restores, if it's a restore."""
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FactRow(Base):
    """A fact from the journal or the import. Its content is encrypted (FR-JRN-4); the columns
    beside it are IDs and dates for finding it."""

    __tablename__ = "facts"
    __table_args__ = (CheckConstraint("origin IN ('journal', 'import')", name="origin"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[str] = mapped_column(EncryptedText("facts.data"))
    """The fact (`app/schema/fact.py`) as JSON."""
    origin: Mapped[Literal["journal", "import"]] = mapped_column(String(8))
    entry_id: Mapped[str | None] = mapped_column(String(64))
    role_id: Mapped[str | None] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VariantRow(Base):
    """A variant (`app/schema/variant.py`): what one output shows from the master profile."""

    __tablename__ = "variants"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobPostingRow(Base):
    """A job posting as pasted, and parsed (`app/schema/jobs.py`)."""

    __tablename__ = "job_postings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    posting: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Artifact(Base):
    """A generated file, such as a resume PDF, stored once per user under its SHA-256 hash."""

    __tablename__ = "artifacts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApplicationRow(Base):
    """A tailored resume made for one job posting (FR-TLR-8), with its verifier report
    (FR-FID-8) and its PDF."""

    __tablename__ = "applications"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "pdf_sha256"], ["artifacts.user_id", "artifacts.sha256"]),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """The application (`app/schema/jobs.py`)."""
    applied_on: Mapped[date] = mapped_column(Date)
    posting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_postings.id", ondelete="SET NULL")
    )
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    verifier_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmCallRow(Base):
    """One LLM call: task, model, prompt version, tokens, cost and latency (NFR-COST-1). Like the
    call log it replaces, it holds no journal text, prompts or outputs."""

    __tablename__ = "llm_calls"
    __table_args__ = (Index(None, "user_id", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    task: Mapped[str] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    served_by: Mapped[str | None] = mapped_column(String(128))
    fell_back: Mapped[bool] = mapped_column(server_default="false")
    prompt: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cache_read_tokens: Mapped[int] = mapped_column(server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(server_default="0")
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int]
    request_id: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)


class TelegramLink(Base):
    """The Telegram chat a user journals from (FR-CAP-2, FR-CAP-3). Messages from chats that
    aren't linked are never stored as journal messages."""

    __tablename__ = "telegram_links"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    """The private chat with the bot, which Telegram numbers the same as the person."""
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramUpdate(Base):
    """A raw update from Telegram, kept the moment it arrives (FR-JRN-1) and deleted after 7 days
    (FR-JRN-2). Its `update_id` makes storing it idempotent: Telegram resends an update until the
    webhook answers, and a resent one is ignored. The update holds message text, so it's
    encrypted."""

    __tablename__ = "telegram_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    payload: Mapped[str] = mapped_column(EncryptedText("telegram_updates.payload"))
    """The update as Telegram sent it, as JSON."""


class JournalMessage(Base):
    """A message in the journal, from the owner's linked chat (FR-JRN-1). An edit in Telegram
    changes its text and keeps the earlier one (FR-JRN-3)."""

    __tablename__ = "journal_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id"),
        CheckConstraint("sender IN ('owner', 'bot')", name="sender"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    """Telegram's number for the message, unique within its chat."""
    sender: Mapped[Literal["owner", "bot"]] = mapped_column(String(8))
    text: Mapped[str] = mapped_column(EncryptedText("journal_messages.text"))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the text was last edited in Telegram, if it was."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JournalMessageEdit(Base):
    """A journal message's earlier text, kept when it was edited in Telegram (FR-JRN-3)."""

    __tablename__ = "journal_message_edits"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_messages.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(EncryptedText("journal_message_edits.text"))
    """The text before the edit."""
    replaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    """When the edit replaced it."""
