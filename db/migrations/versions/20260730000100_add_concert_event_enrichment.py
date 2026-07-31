"""add concert event enrichment

Revision ID: 20260730000100
Revises: 20260727000100
Create Date: 2026-07-30 00:01:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260730000100"
down_revision: Union[str, None] = "20260727000100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "classical_concert",
        sa.Column("event_status", sa.String(), nullable=False, server_default="scheduled"),
    )
    op.add_column(
        "classical_concert",
        sa.Column("event_status_updated_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "classical_concert",
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_classical_concert_event_status",
        "classical_concert",
        "event_status IN ('scheduled', 'cancelled', 'postponed', 'rescheduled')",
    )

    op.create_table(
        "classical_concert_change",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "classical_concert_id",
            sa.Integer(),
            sa.ForeignKey("classical_concert.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(), nullable=False),
        sa.Column("old_value", postgresql.JSONB()),
        sa.Column("new_value", postgresql.JSONB(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "field_name IN ('event_status', 'date', 'time_from', 'time_to', "
            "'city', 'country_code', 'venue')",
            name="ck_classical_concert_change_field",
        ),
    )
    op.create_index(
        "ix_classical_concert_change_concert_created",
        "classical_concert_change",
        ["classical_concert_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_classical_concert_change_concert_created",
        table_name="classical_concert_change",
    )
    op.drop_table("classical_concert_change")
    op.drop_constraint(
        "ck_classical_concert_event_status",
        "classical_concert",
        type_="check",
    )
    op.drop_column("classical_concert", "last_verified_at")
    op.drop_column("classical_concert", "event_status_updated_at")
    op.drop_column("classical_concert", "event_status")
