"""Refreshes

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13 23:40:09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COVER_PROPOSED_FACTS = """
UPDATE facts SET refreshed_at = change_sets.created_at
FROM change_ops JOIN change_sets ON change_sets.id = change_ops.change_set_id
WHERE facts.origin = 'journal'
    AND change_ops.user_id = facts.user_id
    AND change_ops.op -> 'factIds' @> to_jsonb(facts.id)
"""
"""Before refreshes, `wj propose --db` proposed changes from the journal facts that no change set
cited yet. The facts a change set cites were covered then, so a refresh waits for them to be
edited before it covers them again."""


def upgrade() -> None:
    op.create_table(
        "refreshes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fact_count", sa.Integer(), nullable=True),
        sa.Column("change_set_id", sa.Uuid(), nullable=True),
        sa.Column("outputs", sa.String(length=16), nullable=True),
        sa.Column("output_version", sa.Integer(), nullable=True),
        sa.Column("outputs_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("master_pdf_sha256", sa.String(length=64), nullable=True),
        sa.Column("master_pages", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(outputs = 'updated') = (master_pdf_sha256 IS NOT NULL)",
            name=op.f("ck_refreshes_master_pdf"),
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)", name=op.f("ck_refreshes_finished")
        ),
        sa.CheckConstraint(
            "outputs IN ('pending', 'updated', 'failed', 'superseded')",
            name=op.f("ck_refreshes_outputs"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'proposed', 'nothing_new', 'failed')",
            name=op.f("ck_refreshes_status"),
        ),
        sa.CheckConstraint(
            "(outputs IS NULL) = (output_version IS NULL)", name=op.f("ck_refreshes_output_version")
        ),
        sa.ForeignKeyConstraint(
            ["change_set_id"],
            ["change_sets.id"],
            name=op.f("fk_refreshes_change_set_id_change_sets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "master_pdf_sha256"],
            ["artifacts.user_id", "artifacts.sha256"],
            name=op.f("fk_refreshes_user_id_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_refreshes_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refreshes")),
    )
    op.create_index(op.f("ix_refreshes_user_id"), "refreshes", ["user_id"], unique=False)
    op.create_index(
        "uq_refreshes_one_running",
        "refreshes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.add_column("facts", sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(COVER_PROPOSED_FACTS)


def downgrade() -> None:
    op.drop_column("facts", "refreshed_at")
    op.drop_index(
        "uq_refreshes_one_running",
        table_name="refreshes",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_index(op.f("ix_refreshes_user_id"), table_name="refreshes")
    op.drop_table("refreshes")
