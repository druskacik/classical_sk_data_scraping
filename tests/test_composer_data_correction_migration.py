import importlib
import unittest

import sqlalchemy as sa


migration = importlib.import_module(
    "db.migrations.versions.20260807000100_correct_composer_names_and_radetzky"
)


class ComposerDataCorrectionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine("sqlite://")
        self.connection = self.engine.connect()
        for statement in (
            "CREATE TABLE composer (id INTEGER PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE)",
            "CREATE TABLE composer_alias (id INTEGER PRIMARY KEY, composer_id INTEGER NOT NULL REFERENCES composer(id), alias TEXT NOT NULL, normalized_alias TEXT NOT NULL, language_code TEXT, UNIQUE(composer_id, normalized_alias))",
            "CREATE TABLE classical_concert_composer (id INTEGER PRIMARY KEY, classical_concert_id INTEGER NOT NULL, composer_id INTEGER NOT NULL REFERENCES composer(id), UNIQUE(classical_concert_id, composer_id))",
            "CREATE TABLE work (id INTEGER PRIMARY KEY, composer_id INTEGER NOT NULL REFERENCES composer(id), title TEXT NOT NULL, normalized_title TEXT NOT NULL, catalogue_number TEXT, UNIQUE(composer_id, normalized_title))",
            "CREATE TABLE classical_concert_work (id INTEGER PRIMARY KEY, classical_concert_id INTEGER NOT NULL, work_id INTEGER NOT NULL REFERENCES work(id), programme_label TEXT NOT NULL, source_url TEXT NOT NULL, evidence TEXT, UNIQUE(classical_concert_id, work_id))",
        ):
            self.connection.exec_driver_sql(statement)

    def tearDown(self):
        self.connection.close()
        self.engine.dispose()

    def test_renames_preserve_ids_and_add_legacy_aliases(self):
        self.connection.exec_driver_sql(
            """
            INSERT INTO composer VALUES
                (98, 'Johann Strauss', 'johann strauss'),
                (365, 'Johann Strauss ml.', 'johann strauss ml'),
                (181, 'Hildegarda z Bingenu', 'hildegarda z bingenu'),
                (529, 'Richard I Levie srdce', 'richard i levie srdce')
            """
        )

        for old_name, canonical_name, language_code in migration.CANONICAL_RENAMES:
            migration._rename_composer(
                self.connection, old_name, canonical_name, language_code
            )

        self.assertEqual(
            self.connection.exec_driver_sql(
                "SELECT id, name, normalized_name FROM composer ORDER BY id"
            ).fetchall(),
            [
                (98, "Johann Strauss I", "johann strauss i"),
                (181, "Hildegard of Bingen", "hildegard of bingen"),
                (365, "Johann Strauss II", "johann strauss ii"),
                (529, "Richard the Lionheart", "richard the lionheart"),
            ],
        )
        self.assertEqual(
            self.connection.exec_driver_sql(
                "SELECT alias, language_code FROM composer_alias ORDER BY composer_id"
            ).fetchall(),
            [
                ("Johann Strauss", None),
                ("Hildegarda z Bingenu", "sk"),
                ("Johann Strauss ml.", "sk"),
                ("Richard I Levie srdce", "sk"),
            ],
        )

    def test_radetzky_merge_preserves_links_metadata_and_strauss_ii(self):
        self.connection.exec_driver_sql(
            "INSERT INTO composer VALUES (98, 'Johann Strauss I', 'johann strauss i'), (365, 'Johann Strauss II', 'johann strauss ii')"
        )
        self.connection.exec_driver_sql(
            "INSERT INTO work VALUES (517, 98, 'Radetzky March', 'radetzky march', 'Op. 228'), (1021, 365, 'Radetzky March', 'radetzky march', 'Op. 228'), (513, 365, 'Emperor Waltz', 'emperor waltz', 'Op. 437')"
        )
        self.connection.exec_driver_sql(
            "INSERT INTO classical_concert_composer VALUES (1, 10, 365)"
        )
        self.connection.exec_driver_sql(
            "INSERT INTO classical_concert_work VALUES (1, 10, 1021, 'Radetzky March', 'https://example.com', 'listed'), (2, 10, 513, 'Emperor Waltz', 'https://example.com', 'listed')"
        )

        migration._correct_radetzky_march(self.connection)

        self.assertEqual(
            self.connection.exec_driver_sql(
                "SELECT composer_id FROM classical_concert_composer WHERE classical_concert_id = 10 ORDER BY composer_id"
            ).fetchall(),
            [(98,), (365,)],
        )
        self.assertEqual(
            self.connection.exec_driver_sql(
                "SELECT work_id, programme_label, source_url, evidence FROM classical_concert_work ORDER BY work_id"
            ).fetchall(),
            [
                (513, "Emperor Waltz", "https://example.com", "listed"),
                (517, "Radetzky March", "https://example.com", "listed"),
            ],
        )
        self.assertIsNone(
            self.connection.exec_driver_sql(
                "SELECT id FROM work WHERE id = 1021"
            ).scalar()
        )

    def test_radetzky_merge_keeps_existing_canonical_link(self):
        self.connection.exec_driver_sql(
            "INSERT INTO composer VALUES (98, 'Johann Strauss I', 'johann strauss i'), (365, 'Johann Strauss II', 'johann strauss ii')"
        )
        self.connection.exec_driver_sql(
            "INSERT INTO work VALUES (517, 98, 'Radetzky March', 'radetzky march', 'Op. 228'), (1021, 365, 'Radetzky March', 'radetzky march', 'Op. 228')"
        )
        self.connection.exec_driver_sql(
            "INSERT INTO classical_concert_composer VALUES (1, 10, 98), (2, 10, 365)"
        )
        self.connection.exec_driver_sql(
            "INSERT INTO classical_concert_work VALUES (1, 10, 517, 'Existing label', 'https://correct.example', 'existing'), (2, 10, 1021, 'Wrong label', 'https://wrong.example', 'wrong')"
        )

        migration._correct_radetzky_march(self.connection)

        self.assertEqual(
            self.connection.exec_driver_sql(
                "SELECT work_id, programme_label, source_url, evidence FROM classical_concert_work"
            ).one(),
            (517, "Existing label", "https://correct.example", "existing"),
        )

    def test_malformed_composer_is_unlinked_and_deleted(self):
        self.connection.exec_driver_sql(
            "INSERT INTO composer VALUES (672, ?, ?)",
            (
                migration.MALFORMED_COMPOSER,
                migration.normalize(migration.MALFORMED_COMPOSER),
            ),
        )
        self.connection.exec_driver_sql(
            "INSERT INTO classical_concert_composer VALUES (1, 1187, 672)"
        )

        migration._drop_malformed_composer(self.connection)

        self.assertEqual(
            self.connection.exec_driver_sql("SELECT COUNT(*) FROM composer").scalar(), 0
        )
        self.assertEqual(
            self.connection.exec_driver_sql(
                "SELECT COUNT(*) FROM classical_concert_composer"
            ).scalar(),
            0,
        )

    def test_malformed_composer_with_work_is_not_deleted(self):
        self.connection.exec_driver_sql(
            "INSERT INTO composer VALUES (672, ?, ?)",
            (
                migration.MALFORMED_COMPOSER,
                migration.normalize(migration.MALFORMED_COMPOSER),
            ),
        )
        self.connection.exec_driver_sql(
            "INSERT INTO work VALUES (1, 672, 'Unexpected work', 'unexpected work', NULL)"
        )

        with self.assertRaisesRegex(RuntimeError, "it owns 1 work"):
            migration._drop_malformed_composer(self.connection)

        self.assertEqual(
            self.connection.exec_driver_sql("SELECT COUNT(*) FROM composer").scalar(), 1
        )


if __name__ == "__main__":
    unittest.main()
