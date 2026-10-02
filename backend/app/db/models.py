"""Database tables (requirements §13).

v1 has one user, but every row belongs to one (constraint C4): each table has a `user_id` that
references `users`, unless it's listed in `NOT_PER_USER`. `tests/db/test_models.py` checks this.
Constraint names follow `NAMING_CONVENTION`, so migrations can refer to them.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, LargeBinary, MetaData, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
NOT_PER_USER = frozenset({"users"})
"""Tables that don't belong to a user. Adding one needs a reason."""


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
