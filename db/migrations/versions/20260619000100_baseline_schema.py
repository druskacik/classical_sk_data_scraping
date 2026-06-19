"""baseline schema

Revision ID: 20260619000100
Revises:
Create Date: 2026-06-19 00:01:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260619000100"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "classical_concert",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("source", sa.String()),
        sa.Column("source_url", sa.String()),
        sa.Column("time_from", sa.Time()),
        sa.Column("time_to", sa.Time()),
        sa.Column("city", sa.String()),
        sa.Column("venue", sa.String()),
        sa.Column("type", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("description", sa.Text()),
        sa.Column("is_concert_details_filled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("composers", postgresql.ARRAY(sa.Text())),
    )
    op.create_table(
        "potential_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("source", sa.String()),
        sa.Column("source_url", sa.String()),
        sa.Column("time_from", sa.Time()),
        sa.Column("time_to", sa.Time()),
        sa.Column("city", sa.String()),
        sa.Column("venue", sa.String()),
        sa.Column("type", sa.String()),
        sa.Column("analyzed", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("is_classical_concert", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("added", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("description", sa.Text()),
        sa.Column("is_concert_details_filled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("composers", postgresql.ARRAY(sa.Text())),
    )
    op.create_table(
        "composer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
    )
    op.create_table(
        "classical_concert_composer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("classical_concert_id", sa.Integer(), sa.ForeignKey("classical_concert.id")),
        sa.Column("composer_id", sa.Integer(), sa.ForeignKey("composer.id")),
    )


def downgrade() -> None:
    op.drop_table("classical_concert_composer")
    op.drop_table("composer")
    op.drop_table("potential_event")
    op.drop_table("classical_concert")
