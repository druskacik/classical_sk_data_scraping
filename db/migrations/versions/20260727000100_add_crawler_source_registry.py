"""add crawler source registry

Revision ID: 20260727000100
Revises: 20260717000100
Create Date: 2026-07-27 00:01:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260727000100"
down_revision: Union[str, None] = "20260717000100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crawler_source",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("crawler_path", sa.Text()),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("duplicate_of_id", sa.BigInteger(), sa.ForeignKey("crawler_source.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending','processing','pr_open','active','blocked','retry_wait',"
            "'duplicate','needs_attention','disabled')",
            name="ck_crawler_source_status",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_crawler_source_priority"),
        sa.UniqueConstraint("crawler_path", name="uq_crawler_source_crawler_path"),
    )
    op.create_index(
        "ix_crawler_source_due",
        "crawler_source",
        ["status", "next_attempt_at", "priority"],
    )
    op.create_index("ix_crawler_source_lease", "crawler_source", ["lease_expires_at"])
    op.create_table(
        "crawler_source_url",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "crawler_source_id",
            sa.BigInteger(),
            sa.ForeignKey("crawler_source.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("discovered_by", sa.String(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('submitted','canonical','redirect')",
            name="ck_crawler_source_url_role",
        ),
        sa.UniqueConstraint("normalized_url", name="uq_crawler_source_url_normalized"),
    )
    op.create_index("ix_crawler_source_url_source", "crawler_source_url", ["crawler_source_id"])
    op.create_table(
        "crawler_factory_run",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column("branch", sa.Text()),
        sa.Column("pull_request_url", sa.Text()),
        sa.Column("model", sa.String()),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('running','no_changes','pr_open','completed','failed')",
            name="ck_crawler_factory_run_status",
        ),
    )
    op.create_table(
        "crawler_source_attempt",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "crawler_source_id",
            sa.BigInteger(),
            sa.ForeignKey("crawler_source.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(),
            sa.ForeignKey("crawler_factory_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempted_url", sa.Text(), nullable=False),
        sa.Column("resolved_url", sa.Text()),
        sa.Column("crawler_path", sa.Text()),
        sa.Column("outcome", sa.String(), nullable=False, server_default="running"),
        sa.Column("commit_sha", sa.String()),
        sa.Column("pull_request_url", sa.Text()),
        sa.Column("generation_warning", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("retry_after", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "outcome IN ('running','generated','blocked','generation_failed','duplicate',"
            "'skipped_existing','abandoned')",
            name="ck_crawler_source_attempt_outcome",
        ),
    )
    op.create_index(
        "ix_crawler_source_attempt_source_started",
        "crawler_source_attempt",
        ["crawler_source_id", "started_at"],
    )
    op.create_table(
        "crawler_source_seed",
        sa.Column("filename", sa.Text(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("crawler_source_seed")
    op.drop_index("ix_crawler_source_attempt_source_started", table_name="crawler_source_attempt")
    op.drop_table("crawler_source_attempt")
    op.drop_table("crawler_factory_run")
    op.drop_index("ix_crawler_source_url_source", table_name="crawler_source_url")
    op.drop_table("crawler_source_url")
    op.drop_index("ix_crawler_source_lease", table_name="crawler_source")
    op.drop_index("ix_crawler_source_due", table_name="crawler_source")
    op.drop_table("crawler_source")
