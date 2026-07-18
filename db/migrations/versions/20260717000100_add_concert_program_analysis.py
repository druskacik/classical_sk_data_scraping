"""add concert programme analysis

Revision ID: 20260717000100
Revises: 20260619000200
Create Date: 2026-07-17 00:01:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260717000100"
down_revision: Union[str, None] = "20260619000200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "classical_concert",
        sa.Column(
            "program_analysis_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column(
        "classical_concert",
        "program_analysis_eligible",
        server_default=sa.text("true"),
    )
    op.add_column("composer", sa.Column("normalized_name", sa.String(), nullable=True))
    op.execute("UPDATE composer SET normalized_name = lower(btrim(name))")
    op.alter_column("composer", "normalized_name", nullable=False)
    op.create_unique_constraint("uq_composer_normalized_name", "composer", ["normalized_name"])
    op.create_unique_constraint(
        "uq_classical_concert_composer_link",
        "classical_concert_composer",
        ["classical_concert_id", "composer_id"],
    )

    op.create_table(
        "work",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("composer_id", sa.Integer(), sa.ForeignKey("composer.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("normalized_title", sa.String(), nullable=False),
        sa.Column("catalogue_number", sa.String()),
        sa.UniqueConstraint("composer_id", "normalized_title", name="uq_work_composer_title"),
    )
    op.create_index("ix_work_composer_id", "work", ["composer_id"])
    op.create_table(
        "classical_concert_work",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("classical_concert_id", sa.Integer(), sa.ForeignKey("classical_concert.id"), nullable=False),
        sa.Column("work_id", sa.Integer(), sa.ForeignKey("work.id"), nullable=False),
        sa.Column("programme_label", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text()),
        sa.UniqueConstraint("classical_concert_id", "work_id", name="uq_classical_concert_work_link"),
    )
    op.create_table(
        "concert_program_analysis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("classical_concert_id", sa.Integer(), sa.ForeignKey("classical_concert.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("model", sa.String()),
        sa.Column("raw_result", postgresql.JSONB()),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("classical_concert_id", name="uq_concert_program_analysis_concert"),
    )
    op.create_index("ix_concert_program_analysis_status", "concert_program_analysis", ["status"])


def downgrade() -> None:
    op.drop_index("ix_concert_program_analysis_status", table_name="concert_program_analysis")
    op.drop_table("concert_program_analysis")
    op.drop_table("classical_concert_work")
    op.drop_index("ix_work_composer_id", table_name="work")
    op.drop_table("work")
    op.drop_constraint("uq_classical_concert_composer_link", "classical_concert_composer", type_="unique")
    op.drop_constraint("uq_composer_normalized_name", "composer", type_="unique")
    op.drop_column("composer", "normalized_name")
    op.drop_column("classical_concert", "program_analysis_eligible")
