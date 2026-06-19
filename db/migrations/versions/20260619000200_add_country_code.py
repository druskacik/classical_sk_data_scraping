"""add country code

Revision ID: 20260619000200
Revises: 20260619000100
Create Date: 2026-06-19 00:02:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260619000200"
down_revision: Union[str, None] = "20260619000100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("classical_concert", sa.Column("country_code", sa.String(length=2), nullable=True))
    op.add_column("potential_event", sa.Column("country_code", sa.String(length=2), nullable=True))
    op.create_index("ix_classical_concert_country_city_date", "classical_concert", ["country_code", "city", "date"])
    op.create_index("ix_potential_event_country_analysis", "potential_event", ["country_code", "analyzed", "added"])


def downgrade() -> None:
    op.drop_index("ix_potential_event_country_analysis", table_name="potential_event")
    op.drop_index("ix_classical_concert_country_city_date", table_name="classical_concert")
    op.drop_column("potential_event", "country_code")
    op.drop_column("classical_concert", "country_code")
