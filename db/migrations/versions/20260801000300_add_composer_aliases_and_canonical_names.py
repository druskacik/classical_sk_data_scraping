"""add composer aliases and canonical English names

Revision ID: 20260801000300
Revises: 20260801000200
Create Date: 2026-08-01 00:03:00
"""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260801000300"
down_revision: Union[str, None] = "20260801000200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Explicit, reviewed legacy spellings. Rows not listed here already use their
# established English-reference form or cannot be changed without guessing.
CANONICAL_RENAMES = (
    ("Anatolij Liadov", "Anatoly Lyadov"),
    ("Aram Chačaturian", "Aram Khachaturian"),
    ("Dmitrij Šostakovič", "Dmitri Shostakovich"),
    ("Dmytro Bortňanskyj", "Dmitry Bortniansky"),
    ("Georgij Sviridov", "Georgy Sviridov"),
    ("Igor Stravinskij", "Igor Stravinsky"),
    ("Michail Ivanovič Glinka", "Mikhail Glinka"),
    ("Modest Petrovič Musorgskij", "Modest Mussorgsky"),
    ("Nikolaj Rimskij-Korsakov", "Nikolai Rimsky-Korsakov"),
    ("Nikolaj Vasilievič Gogoľ", "Nikolai Gogol"),
    ("Piotr Iľjič Čajkovskij", "Pyotr Ilyich Tchaikovsky"),
    ("Rodion Ščedrin", "Rodion Shchedrin"),
    ("Sergej Banevič", "Sergei Banevich"),
    ("Sergej Kusevickij", "Serge Koussevitzky"),
    ("Sergej Prokofiev", "Sergei Prokofiev"),
    ("Sergej Rachmaninov", "Sergei Rachmaninoff"),
)

# Only identities that are unambiguous in the production catalogue belong
# here. Ambiguous surname rows such as Kupkovič deliberately remain separate.
CONFIRMED_MERGES = (
    ("Czibulka", "Alfons Czibulka"),
    ("Debussy", "Claude Debussy"),
    ("Donizetti", "Gaetano Donizetti"),
    ("Dusík", "Ján Ladislav Dusík"),
    ("Kálmán", "Emmerich Kálmán"),
    ("Lehár", "Franz Lehár"),
    ("Ravel", "Maurice Ravel"),
    ("Satie", "Erik Satie"),
    ("Verdi", "Giuseppe Verdi"),
)


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def _composer_id(connection, name: str) -> int | None:
    return connection.execute(
        sa.text("SELECT id FROM composer WHERE name = :name"), {"name": name}
    ).scalar()


def _add_alias(connection, composer_id: int, alias: str, language_code: str | None) -> None:
    normalized_alias = normalize(alias)
    connection.execute(
        sa.text(
            """
            INSERT INTO composer_alias (composer_id, alias, normalized_alias, language_code)
            VALUES (:composer_id, :alias, :normalized_alias, :language_code)
            ON CONFLICT (composer_id, normalized_alias) DO NOTHING
            """
        ),
        {
            "composer_id": composer_id,
            "alias": alias,
            "normalized_alias": normalized_alias,
            "language_code": language_code,
        },
    )


def _merge_composer(connection, source_name: str, target_name: str) -> None:
    source_id = _composer_id(connection, source_name)
    if source_id is None:
        return
    target_id = _composer_id(connection, target_name)
    if target_id is None:
        raise RuntimeError(f"Cannot merge {source_name!r}: target {target_name!r} is missing")

    work_collisions = connection.execute(
        sa.text(
            """
            SELECT source_work.title, target_work.title
            FROM work source_work
            JOIN work target_work
              ON target_work.composer_id = :target_id
             AND target_work.normalized_title = source_work.normalized_title
            WHERE source_work.composer_id = :source_id
            """
        ),
        {"source_id": source_id, "target_id": target_id},
    ).fetchall()
    if work_collisions:
        raise RuntimeError(
            f"Cannot safely merge {source_name!r} into {target_name!r}; "
            f"review colliding works: {work_collisions!r}"
        )

    connection.execute(
        sa.text(
            """
            INSERT INTO classical_concert_composer (classical_concert_id, composer_id)
            SELECT classical_concert_id, :target_id
            FROM classical_concert_composer
            WHERE composer_id = :source_id
            ON CONFLICT (classical_concert_id, composer_id) DO NOTHING
            """
        ),
        {"source_id": source_id, "target_id": target_id},
    )
    connection.execute(
        sa.text("DELETE FROM classical_concert_composer WHERE composer_id = :source_id"),
        {"source_id": source_id},
    )
    connection.execute(
        sa.text("UPDATE work SET composer_id = :target_id WHERE composer_id = :source_id"),
        {"source_id": source_id, "target_id": target_id},
    )
    _add_alias(connection, target_id, source_name, None)
    connection.execute(
        sa.text("DELETE FROM composer WHERE id = :source_id"), {"source_id": source_id}
    )


def _renormalize_composers(connection) -> None:
    rows = connection.execute(sa.text("SELECT id, name FROM composer ORDER BY id")).fetchall()
    normalized_to_rows: dict[str, list[tuple[int, str]]] = {}
    for composer_id, name in rows:
        normalized_to_rows.setdefault(normalize(name), []).append((composer_id, name))
    collisions = {key: values for key, values in normalized_to_rows.items() if len(values) > 1}
    if collisions:
        raise RuntimeError(f"Unreviewed normalized composer collisions: {collisions!r}")

    for composer_id, _name in rows:
        connection.execute(
            sa.text("UPDATE composer SET normalized_name = :temporary WHERE id = :composer_id"),
            {"temporary": f"__composer_migration_{composer_id}", "composer_id": composer_id},
        )
    for composer_id, name in rows:
        connection.execute(
            sa.text("UPDATE composer SET normalized_name = :normalized WHERE id = :composer_id"),
            {"normalized": normalize(name), "composer_id": composer_id},
        )


def upgrade() -> None:
    op.create_table(
        "composer_alias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "composer_id",
            sa.Integer(),
            sa.ForeignKey("composer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("language_code", sa.String(2)),
        sa.UniqueConstraint(
            "composer_id",
            "normalized_alias",
            name="uq_composer_alias_composer_normalized",
        ),
    )
    op.create_index("ix_composer_alias_normalized", "composer_alias", ["normalized_alias"])

    connection = op.get_bind()
    for source_name, target_name in CONFIRMED_MERGES:
        _merge_composer(connection, source_name, target_name)

    for old_name, canonical_name in CANONICAL_RENAMES:
        composer_id = _composer_id(connection, old_name)
        if composer_id is None:
            continue
        if _composer_id(connection, canonical_name) is not None:
            raise RuntimeError(
                f"Cannot rename {old_name!r}: canonical row {canonical_name!r} already exists"
            )
        _add_alias(connection, composer_id, old_name, "sk")
        connection.execute(
            sa.text("UPDATE composer SET name = :name WHERE id = :composer_id"),
            {"name": canonical_name, "composer_id": composer_id},
        )

    _renormalize_composers(connection)


def downgrade() -> None:
    # Canonical names and identity merges are intentional data corrections and
    # remain in place. Restoring ambiguous legacy identities would lose the
    # provenance of links created after this migration.
    op.drop_index("ix_composer_alias_normalized", table_name="composer_alias")
    op.drop_table("composer_alias")
