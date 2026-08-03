"""add crawler source geographic scope

Revision ID: 20260803000100
Revises: 20260801000300
Create Date: 2026-08-03 00:01:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260803000100"
down_revision: Union[str, None] = "20260801000300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("crawler_source", "country_code", nullable=True)
    op.add_column(
        "crawler_source",
        sa.Column(
            "geographic_scope",
            sa.String(),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.execute(
        """
        UPDATE crawler_source
        SET geographic_scope = 'country'
        WHERE status IN ('processing', 'pr_open', 'active', 'blocked', 'disabled')
          AND country_code IS NOT NULL
          AND crawler_path = 'crawlers/' || lower(country_code) || '/' ||
              split_part(crawler_path, '/', 3)
        """
    )
    op.create_check_constraint(
        "ck_crawler_source_geographic_scope",
        "crawler_source",
        "geographic_scope IN ('unknown', 'country', 'multi_country')",
    )
    op.create_check_constraint(
        "ck_crawler_source_geographic_identity",
        "crawler_source",
        """
        geographic_scope = 'unknown'
        OR (
            geographic_scope = 'country'
            AND country_code IS NOT NULL
            AND crawler_path IS NOT NULL
            AND country_code ~ '^[A-Z]{2}$'
            AND crawler_path ~ ('^crawlers/' || lower(country_code) || '/[^/]+$')
        )
        OR (
            geographic_scope = 'multi_country'
            AND country_code IS NULL
            AND crawler_path IS NOT NULL
            AND crawler_path ~ '^crawlers/common/[^/]+$'
        )
        """,
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM crawler_source WHERE country_code IS NULL) THEN
                RAISE EXCEPTION
                    'cannot restore non-null country_code while multi-country sources exist';
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "ck_crawler_source_geographic_identity",
        "crawler_source",
        type_="check",
    )
    op.drop_constraint(
        "ck_crawler_source_geographic_scope",
        "crawler_source",
        type_="check",
    )
    op.drop_column("crawler_source", "geographic_scope")
    op.alter_column("crawler_source", "country_code", nullable=False)
