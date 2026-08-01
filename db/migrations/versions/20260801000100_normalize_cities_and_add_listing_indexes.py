"""add concert listing indexes

Revision ID: 20260801000100
Revises: 20260730000100
Create Date: 2026-08-01 00:01:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260801000100"
down_revision: Union[str, None] = "20260730000100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_classical_concert_date_time_id",
        "classical_concert",
        ["date", "time_from", "id"],
    )
    op.create_index(
        "ix_classical_concert_country_date_time_id",
        "classical_concert",
        ["country_code", "date", "time_from", "id"],
    )
    op.create_index(
        "ix_classical_concert_composer_composer_concert",
        "classical_concert_composer",
        ["composer_id", "classical_concert_id"],
    )
    op.create_index(
        "ix_classical_concert_work_work_concert",
        "classical_concert_work",
        ["work_id", "classical_concert_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_classical_concert_work_work_concert",
        table_name="classical_concert_work",
    )
    op.drop_index(
        "ix_classical_concert_composer_composer_concert",
        table_name="classical_concert_composer",
    )
    op.drop_index(
        "ix_classical_concert_country_date_time_id",
        table_name="classical_concert",
    )
    op.drop_index(
        "ix_classical_concert_date_time_id",
        table_name="classical_concert",
    )
