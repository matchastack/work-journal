"""Engine data: profile versions, facts, change sets, variants, applications and LLM calls

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01 12:12:05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_VERSIONS = (
    """
    CREATE FUNCTION profile_versions_are_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'profile versions are immutable: save a new version instead';
    END;
    $$
    """,
    """
    CREATE TRIGGER profile_versions_are_immutable BEFORE UPDATE ON profile_versions
        FOR EACH ROW EXECUTE FUNCTION profile_versions_are_immutable()
    """,
)
"""NFR-DATA-1: a version never changes once it's written. Deleting history stays possible.
Each statement runs on its own, as asyncpg takes one command at a time."""


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_artifacts_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "sha256", name=op.f("pk_artifacts")),
    )
    op.create_table(
        "change_sets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("author", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="proposed", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("author IN ('owner', 'ai')", name=op.f("ck_change_sets_author")),
        sa.CheckConstraint(
            "status IN ('proposed', 'applied', 'rejected')", name=op.f("ck_change_sets_status")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_change_sets_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_sets")),
    )
    op.create_index(op.f("ix_change_sets_user_id"), "change_sets", ["user_id"], unique=False)
    op.create_table(
        "facts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("entry_id", sa.String(length=64), nullable=True),
        sa.Column("role_id", sa.String(length=64), nullable=True),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("origin IN ('journal', 'import')", name=op.f("ck_facts_origin")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_facts_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "id", name=op.f("pk_facts")),
    )
    op.create_table(
        "job_postings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("posting", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_job_postings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_postings")),
    )
    op.create_index(op.f("ix_job_postings_user_id"), "job_postings", ["user_id"], unique=False)
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task", sa.String(length=64), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("served_by", sa.String(length=128), nullable=True),
        sa.Column("fell_back", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("prompt", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_llm_calls_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(op.f("ix_llm_calls_user_id"), "llm_calls", ["user_id", "at"], unique=False)
    op.create_table(
        "variants",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_variants_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "id", name=op.f("pk_variants")),
    )
    op.create_table(
        "applications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("applied_on", sa.Date(), nullable=False),
        sa.Column("posting_id", sa.Uuid(), nullable=True),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=True),
        sa.Column("verifier_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["posting_id"],
            ["job_postings.id"],
            name=op.f("fk_applications_posting_id_job_postings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "pdf_sha256"],
            ["artifacts.user_id", "artifacts.sha256"],
            name=op.f("fk_applications_user_id_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_applications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "id", name=op.f("pk_applications")),
    )
    op.create_table(
        "change_ops",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("change_set_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("op", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="proposed", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("verifier_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'rejected')", name=op.f("ck_change_ops_status")
        ),
        sa.ForeignKeyConstraint(
            ["change_set_id"],
            ["change_sets.id"],
            name=op.f("fk_change_ops_change_set_id_change_sets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_change_ops_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_ops")),
        sa.UniqueConstraint("change_set_id", "position", name=op.f("uq_change_ops_change_set_id")),
    )
    op.create_index(op.f("ix_change_ops_user_id"), "change_ops", ["user_id"], unique=False)
    op.create_table(
        "profile_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("parent", sa.Integer(), nullable=True),
        sa.Column("author", sa.String(length=8), nullable=False),
        sa.Column("change_set_id", sa.Uuid(), nullable=True),
        sa.Column("restored_from", sa.Integer(), nullable=True),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("author IN ('owner', 'ai')", name=op.f("ck_profile_versions_author")),
        sa.CheckConstraint("number > 0", name=op.f("ck_profile_versions_number")),
        sa.ForeignKeyConstraint(
            ["change_set_id"],
            ["change_sets.id"],
            name=op.f("fk_profile_versions_change_set_id_change_sets"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_profile_versions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_versions")),
        sa.UniqueConstraint("user_id", "number", name=op.f("uq_profile_versions_user_id")),
    )
    for statement in IMMUTABLE_VERSIONS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER profile_versions_are_immutable ON profile_versions")
    op.execute("DROP FUNCTION profile_versions_are_immutable()")
    op.drop_table("profile_versions")
    op.drop_index(op.f("ix_change_ops_user_id"), table_name="change_ops")
    op.drop_table("change_ops")
    op.drop_table("applications")
    op.drop_table("variants")
    op.drop_index(op.f("ix_llm_calls_user_id"), table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index(op.f("ix_job_postings_user_id"), table_name="job_postings")
    op.drop_table("job_postings")
    op.drop_table("facts")
    op.drop_index(op.f("ix_change_sets_user_id"), table_name="change_sets")
    op.drop_table("change_sets")
    op.drop_table("artifacts")
