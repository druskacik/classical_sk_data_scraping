"""correct composer names and Radetzky March attribution

Revision ID: 20260807000100
Revises: 20260803000100
Create Date: 2026-08-07 00:01:00
"""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807000100"
down_revision: Union[str, None] = "20260803000100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CANONICAL_RENAMES = (
    ("Johann Strauss", "Johann Strauss I", None),
    ("Johann Strauss ml.", "Johann Strauss II", "sk"),
    ("Hildegarda z Bingenu", "Hildegard of Bingen", "sk"),
    ("Richard I Levie srdce", "Richard the Lionheart", "sk"),
)
MALFORMED_COMPOSER = "Concerto delle DonneFrancesca Caccini Barbara Strozzi ***"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def _composer_id(connection, name: str) -> int | None:
    return connection.execute(
        sa.text("SELECT id FROM composer WHERE name = :name"), {"name": name}
    ).scalar()


def _add_alias(
    connection, composer_id: int, alias: str, language_code: str | None
) -> None:
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
            "normalized_alias": normalize(alias),
            "language_code": language_code,
        },
    )


def _rename_composer(
    connection, old_name: str, canonical_name: str, language_code: str | None
) -> int | None:
    old_id = _composer_id(connection, old_name)
    canonical_id = _composer_id(connection, canonical_name)
    if old_id is not None and canonical_id is not None and old_id != canonical_id:
        raise RuntimeError(
            f"Cannot rename {old_name!r}: canonical row {canonical_name!r} already exists"
        )

    composer_id = old_id if old_id is not None else canonical_id
    if composer_id is None:
        return None
    _add_alias(connection, composer_id, old_name, language_code)
    if old_id is not None:
        connection.execute(
            sa.text(
                """
                UPDATE composer
                SET name = :canonical_name, normalized_name = :normalized_name
                WHERE id = :composer_id
                """
            ),
            {
                "canonical_name": canonical_name,
                "normalized_name": normalize(canonical_name),
                "composer_id": composer_id,
            },
        )
    return composer_id


def _correct_radetzky_march(connection) -> None:
    strauss_i_id = _composer_id(connection, "Johann Strauss I")
    strauss_ii_id = _composer_id(connection, "Johann Strauss II")
    if strauss_i_id is None or strauss_ii_id is None:
        return

    correct_work_id = connection.execute(
        sa.text(
            """
            SELECT id FROM work
            WHERE composer_id = :composer_id AND normalized_title = 'radetzky march'
            """
        ),
        {"composer_id": strauss_i_id},
    ).scalar()
    wrong_work_id = connection.execute(
        sa.text(
            """
            SELECT id FROM work
            WHERE composer_id = :composer_id AND normalized_title = 'radetzky march'
            """
        ),
        {"composer_id": strauss_ii_id},
    ).scalar()
    if wrong_work_id is None:
        return

    connection.execute(
        sa.text(
            """
            INSERT INTO classical_concert_composer (classical_concert_id, composer_id)
            SELECT classical_concert_id, :strauss_i_id
            FROM classical_concert_work
            WHERE work_id = :wrong_work_id
            ON CONFLICT (classical_concert_id, composer_id) DO NOTHING
            """
        ),
        {"strauss_i_id": strauss_i_id, "wrong_work_id": wrong_work_id},
    )

    if correct_work_id is None:
        connection.execute(
            sa.text("UPDATE work SET composer_id = :composer_id WHERE id = :work_id"),
            {"composer_id": strauss_i_id, "work_id": wrong_work_id},
        )
        return

    connection.execute(
        sa.text(
            """
            INSERT INTO classical_concert_work
                (classical_concert_id, work_id, programme_label, source_url, evidence)
            SELECT classical_concert_id, :correct_work_id, programme_label, source_url, evidence
            FROM classical_concert_work
            WHERE work_id = :wrong_work_id
            ON CONFLICT (classical_concert_id, work_id) DO NOTHING
            """
        ),
        {"correct_work_id": correct_work_id, "wrong_work_id": wrong_work_id},
    )
    connection.execute(
        sa.text("DELETE FROM classical_concert_work WHERE work_id = :work_id"),
        {"work_id": wrong_work_id},
    )
    connection.execute(
        sa.text("DELETE FROM work WHERE id = :work_id"), {"work_id": wrong_work_id}
    )


def _drop_malformed_composer(connection) -> None:
    composer_id = _composer_id(connection, MALFORMED_COMPOSER)
    if composer_id is None:
        return
    work_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM work WHERE composer_id = :composer_id"),
        {"composer_id": composer_id},
    ).scalar_one()
    if work_count:
        raise RuntimeError(
            f"Refusing to delete malformed composer {MALFORMED_COMPOSER!r}: "
            f"it owns {work_count} work(s)"
        )
    connection.execute(
        sa.text("DELETE FROM classical_concert_composer WHERE composer_id = :composer_id"),
        {"composer_id": composer_id},
    )
    connection.execute(
        sa.text("DELETE FROM composer_alias WHERE composer_id = :composer_id"),
        {"composer_id": composer_id},
    )
    connection.execute(
        sa.text("DELETE FROM composer WHERE id = :composer_id"),
        {"composer_id": composer_id},
    )


def upgrade() -> None:
    connection = op.get_bind()
    for old_name, canonical_name, language_code in CANONICAL_RENAMES:
        _rename_composer(connection, old_name, canonical_name, language_code)
    _correct_radetzky_march(connection)
    _drop_malformed_composer(connection)


def downgrade() -> None:
    # These are reviewed data corrections. Restoring known-bad catalogue data
    # would be more harmful than leaving the corrected identities in place.
    pass
