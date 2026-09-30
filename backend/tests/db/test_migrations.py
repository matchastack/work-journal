from alembic import command
from alembic.config import Config


def test_the_migrations_match_the_models(alembic: Config) -> None:
    """Fails when a model changes without a migration (`alembic revision --autogenerate`)."""
    command.check(alembic)


def test_the_migrations_go_down_and_up_again(alembic: Config) -> None:
    command.downgrade(alembic, "base")
    command.upgrade(alembic, "head")
