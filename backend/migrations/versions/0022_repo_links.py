"""Linked GitHub repositories

Revision ID: 0022
Revises: 0015
Create Date: 2026-11-13 11:13:36
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DELETE_GITHUB_FACTS = "DELETE FROM facts WHERE origin = 'github'"
"""Without the `github` origin, facts from GitHub can't stay: the downgrade deletes them."""


def upgrade() -> None:
    op.create_table(
        "repo_links",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.LargeBinary(), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("new_project", sa.LargeBinary(), nullable=True),
        sa.Column("ownership", sa.String(length=16), nullable=True),
        sa.Column("since", sa.Date(), nullable=True),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ownership IN ('owned', 'contributed', 'assisted')",
            name=op.f("ck_repo_links_ownership"),
        ),
        sa.CheckConstraint(
            "(project_id IS NULL) <> (new_project IS NULL)", name=op.f("ck_repo_links_project")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_repo_links_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repo_links")),
        sa.UniqueConstraint("user_id", "github_id", name=op.f("uq_repo_links_user_id")),
    )
    op.create_table(
        "repo_items",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=False),
        sa.Column(
            "read_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("failed", sa.Boolean(), server_default="false", nullable=False),
        sa.CheckConstraint("kind IN ('about', 'pull', 'commit')", name=op.f("ck_repo_items_kind")),
        sa.ForeignKeyConstraint(
            ["link_id"],
            ["repo_links.id"],
            name=op.f("fk_repo_items_link_id_repo_links"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_repo_items_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("link_id", "kind", "ref", name=op.f("pk_repo_items")),
    )
    op.create_index(op.f("ix_repo_items_user_id"), "repo_items", ["user_id"], unique=False)
    op.drop_constraint(op.f("ck_facts_origin"), "facts", type_="check")
    op.create_check_constraint(
        op.f("ck_facts_origin"), "facts", "origin IN ('journal', 'import', 'github')"
    )


def downgrade() -> None:
    op.execute(DELETE_GITHUB_FACTS)
    op.drop_constraint(op.f("ck_facts_origin"), "facts", type_="check")
    op.create_check_constraint(op.f("ck_facts_origin"), "facts", "origin IN ('journal', 'import')")
    op.drop_index(op.f("ix_repo_items_user_id"), table_name="repo_items")
    op.drop_table("repo_items")
    op.drop_table("repo_links")
