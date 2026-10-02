"""Finding users by their GitHub username, for commands such as `wj db load-profile --user`."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import GITHUB_LOGIN
from app.db.models import User


class UnknownUserError(LookupError):
    """No user, or more than one, matches."""


async def user_for_login(session: AsyncSession, login: str, *, create: bool = False) -> uuid.UUID:
    """The user with GitHub username `login`, in any case.

    With `create`, a missing user is created with just the username. Their first GitHub sign-in
    claims it (`app/auth/routes.py`), so data loaded before then is theirs.
    """
    if not GITHUB_LOGIN.match(login.lower()):
        raise UnknownUserError(f"{login!r} isn't a GitHub username")
    query = (
        select(User.id)
        .where(func.lower(User.github_login) == login.lower())
        .order_by(User.github_id.is_(None), User.created_at)
    )
    if (found := await session.scalar(query)) is not None:
        return found
    if not create:
        raise UnknownUserError(f"no user {login!r} in the database")
    user = User(github_login=login)
    session.add(user)
    await session.flush()
    return user.id


async def only_user(session: AsyncSession) -> uuid.UUID:
    """The database's one user. v1 has one, the owner; with more, name one."""
    users = (await session.scalars(select(User.id).limit(2))).all()
    if len(users) != 1:
        problem = "no users" if not users else "more than one user"
        raise UnknownUserError(f"the database has {problem}; name one with --user")
    return users[0]
