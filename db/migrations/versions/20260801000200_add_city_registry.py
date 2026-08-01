"""add canonical city registry and resolved event locations

Revision ID: 20260801000200
Revises: 20260801000100
Create Date: 2026-08-01 00:02:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260801000200"
down_revision: Union[str, None] = "20260801000100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_CITIES = (
    ("3067696", "CZ", "Prague", "Praha"),
    ("3060972", "SK", "Bratislava", "Bratislava"),
    ("3078610", "CZ", "Brno", "Brno"),
    ("724443", "SK", "Košice", "Košice"),
    ("3056508", "SK", "Žilina", "Žilina"),
    ("3061186", "SK", "Banská Bystrica", "Banská Bystrica"),
    ("3068799", "CZ", "Ostrava", "Ostrava"),
)
SEED_ALIASES = (
    ("3067696", "Prague", "prague", "en"),
    ("3067696", "Praha", "praha", "cs"),
    ("3067696", "Prag", "prag", "de"),
    ("3060972", "Bratislava", "bratislava", "sk"),
    ("3078610", "Brno", "brno", "cs"),
    ("724443", "Košice", "košice", "sk"),
    ("3056508", "Žilina", "žilina", "sk"),
    ("3061186", "Banská Bystrica", "banská bystrica", "sk"),
    ("3068799", "Ostrava", "ostrava", "cs"),
)


def upgrade() -> None:
    op.create_table(
        "city",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("english_name", sa.Text(), nullable=False),
        sa.Column("local_name", sa.Text(), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("external_source", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False, server_default="seed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="ck_city_country_code"),
        sa.UniqueConstraint("external_source", "external_id", name="uq_city_external_identity"),
    )
    op.create_index("ix_city_country_english_name", "city", ["country_code", "english_name"])
    op.create_table(
        "city_alias",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("city_id", sa.BigInteger(), sa.ForeignKey("city.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("language_code", sa.String()),
        sa.Column("alias_kind", sa.String(), nullable=False),
        sa.Column("source_scope", sa.String()),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False, server_default="seed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("alias_kind IN ('legitimate_name', 'extraction_artifact')", name="ck_city_alias_kind"),
    )
    op.create_index("ix_city_alias_lookup", "city_alias", ["normalized_alias"])
    op.create_index("uq_city_alias_global", "city_alias", ["city_id", "normalized_alias"], unique=True, postgresql_where=sa.text("source_scope IS NULL"))
    op.create_index("uq_city_alias_scoped", "city_alias", ["city_id", "normalized_alias", "source_scope"], unique=True, postgresql_where=sa.text("source_scope IS NOT NULL"))

    for table in ("classical_concert", "potential_event"):
        op.alter_column(table, "city", new_column_name="city_raw")
        op.alter_column(table, "country_code", new_column_name="country_code_raw")
        op.add_column(table, sa.Column("city_id", sa.BigInteger()))
        op.add_column(table, sa.Column("country_code_resolved", sa.String(2)))
        op.create_foreign_key(f"fk_{table}_city_id", table, "city", ["city_id"], ["id"], ondelete="SET NULL")
        op.create_check_constraint(f"ck_{table}_country_code_resolved", table, "country_code_resolved IS NULL OR country_code_resolved ~ '^[A-Z]{2}$'")

    op.drop_index("ix_classical_concert_country_city_date", table_name="classical_concert")
    op.drop_index("ix_classical_concert_country_date_time_id", table_name="classical_concert")
    op.create_index("ix_classical_concert_country_date_time_id", "classical_concert", ["country_code_resolved", "date", "time_from", "id"])
    op.create_index("ix_classical_concert_location_date", "classical_concert", ["country_code_resolved", "city_id", "date"])
    op.create_index("ix_potential_event_city_id", "potential_event", ["city_id"])

    for external_id, country, english, local in SEED_CITIES:
        op.execute(sa.text("INSERT INTO city (english_name, local_name, country_code, external_source, external_id, source_url, created_by) VALUES (:english, :local, :country, 'geonames', :external_id, :url, 'seed')").bindparams(english=english, local=local, country=country, external_id=external_id, url=f"https://www.geonames.org/{external_id}"))
    for external_id, alias, normalized, language in SEED_ALIASES:
        op.execute(sa.text("INSERT INTO city_alias (city_id, alias, normalized_alias, language_code, alias_kind, source_url, created_by) SELECT id, :alias, :normalized, :language, 'legitimate_name', :url, 'seed' FROM city WHERE external_source = 'geonames' AND external_id = :external_id").bindparams(alias=alias, normalized=normalized, language=language, url=f"https://www.geonames.org/{external_id}", external_id=external_id))

    for table in ("classical_concert", "potential_event"):
        op.execute(f"""UPDATE {table} event SET city_id = matched.city_id, country_code_resolved = matched.country_code FROM (SELECT a.normalized_alias, MIN(a.city_id) city_id, MIN(c.country_code) country_code FROM city_alias a JOIN city c ON c.id = a.city_id WHERE a.source_scope IS NULL GROUP BY a.normalized_alias HAVING COUNT(DISTINCT a.city_id) = 1) matched WHERE LOWER(REGEXP_REPLACE(BTRIM(event.city_raw), '\\s+', ' ', 'g')) = matched.normalized_alias""")

    op.drop_constraint("ck_classical_concert_change_field", "classical_concert_change", type_="check")
    op.create_check_constraint("ck_classical_concert_change_field", "classical_concert_change", "field_name IN ('event_status', 'date', 'time_from', 'time_to', 'city', 'country_code', 'city_id', 'country_code_resolved', 'venue')")


def downgrade() -> None:
    op.drop_constraint("ck_classical_concert_change_field", "classical_concert_change", type_="check")
    op.create_check_constraint("ck_classical_concert_change_field", "classical_concert_change", "field_name IN ('event_status', 'date', 'time_from', 'time_to', 'city', 'country_code', 'venue')")
    op.drop_index("ix_potential_event_city_id", table_name="potential_event")
    op.drop_index("ix_classical_concert_location_date", table_name="classical_concert")
    op.drop_index("ix_classical_concert_country_date_time_id", table_name="classical_concert")
    op.create_index("ix_classical_concert_country_date_time_id", "classical_concert", ["country_code_raw", "date", "time_from", "id"])
    op.create_index("ix_classical_concert_country_city_date", "classical_concert", ["country_code_raw", "city_raw", "date"])
    for table in ("potential_event", "classical_concert"):
        op.drop_constraint(f"ck_{table}_country_code_resolved", table, type_="check")
        op.drop_constraint(f"fk_{table}_city_id", table, type_="foreignkey")
        op.drop_column(table, "country_code_resolved")
        op.drop_column(table, "city_id")
        op.alter_column(table, "country_code_raw", new_column_name="country_code")
        op.alter_column(table, "city_raw", new_column_name="city")
    op.drop_table("city_alias")
    op.drop_table("city")
