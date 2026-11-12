"""Tailor drafts

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-31 05:26:19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tailor_drafts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default="parsing", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posting_text", sa.Text(), nullable=False),
        sa.Column("posting", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=True),
        sa.Column("tailoring", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("choices", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("render_status", sa.String(length=16), nullable=True),
        sa.Column("render_key", sa.String(length=64), nullable=True),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=True),
        sa.Column("render", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("render_error", sa.Text(), nullable=True),
        sa.Column("application_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "(render_status = 'rendered') = (pdf_sha256 IS NOT NULL)",
            name=op.f("ck_tailor_drafts_pdf"),
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error IS NOT NULL)", name=op.f("ck_tailor_drafts_error")
        ),
        sa.CheckConstraint(
            "(status = 'ready') = (tailoring IS NOT NULL)", name=op.f("ck_tailor_drafts_tailoring")
        ),
        sa.CheckConstraint(
            "application_id IS NULL OR render_status = 'rendered'",
            name=op.f("ck_tailor_drafts_logged"),
        ),
        sa.CheckConstraint(
            "render_status IN ('waiting', 'rendered', 'failed')",
            name=op.f("ck_tailor_drafts_render_status"),
        ),
        sa.CheckConstraint(
            "status IN ('parsing', 'tailoring', 'ready', 'failed')",
            name=op.f("ck_tailor_drafts_status"),
        ),
        sa.CheckConstraint(
            "render_status <> 'rendered' OR render_key IS NOT NULL",
            name=op.f("ck_tailor_drafts_render_key"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "application_id"],
            ["applications.user_id", "applications.id"],
            name=op.f("fk_tailor_drafts_user_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "pdf_sha256"],
            ["artifacts.user_id", "artifacts.sha256"],
            name=op.f("fk_tailor_drafts_user_id_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tailor_drafts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tailor_drafts")),
    )
    op.create_index(
        op.f("ix_tailor_drafts_user_id"), "tailor_drafts", ["user_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tailor_drafts_user_id"), table_name="tailor_drafts")
    op.drop_table("tailor_drafts")
