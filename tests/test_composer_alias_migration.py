import importlib
import unittest

import sqlalchemy as sa


migration = importlib.import_module(
    "db.migrations.versions.20260801000300_add_composer_aliases_and_canonical_names"
)


class ComposerAliasMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine("sqlite://")
        self.connection = self.engine.connect()
        for statement in (
            "CREATE TABLE composer (id INTEGER PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE)",
            "CREATE TABLE composer_alias (id INTEGER PRIMARY KEY, composer_id INTEGER NOT NULL REFERENCES composer(id), alias TEXT NOT NULL, normalized_alias TEXT NOT NULL, language_code TEXT, UNIQUE(composer_id, normalized_alias))",
            "CREATE TABLE classical_concert_composer (id INTEGER PRIMARY KEY, classical_concert_id INTEGER NOT NULL, composer_id INTEGER NOT NULL REFERENCES composer(id), UNIQUE(classical_concert_id, composer_id))",
            "CREATE TABLE work (id INTEGER PRIMARY KEY, composer_id INTEGER NOT NULL REFERENCES composer(id), title TEXT NOT NULL, normalized_title TEXT NOT NULL, UNIQUE(composer_id, normalized_title))",
        ):
            self.connection.exec_driver_sql(statement)

    def tearDown(self):
        self.connection.close()
        self.engine.dispose()

    def test_merge_moves_links_and_works_and_preserves_alias(self):
        self.connection.exec_driver_sql(
            "INSERT INTO composer VALUES (4, 'Giuseppe Verdi', 'giuseppe verdi'), (575, 'Verdi', 'verdi')"
        )
        self.connection.exec_driver_sql(
            "INSERT INTO classical_concert_composer VALUES (1, 10, 4), (2, 10, 575), (3, 11, 575)"
        )
        self.connection.exec_driver_sql(
            "INSERT INTO work VALUES (20, 575, 'Test work', 'test work')"
        )

        migration._merge_composer(self.connection, "Verdi", "Giuseppe Verdi")

        self.assertEqual(
            self.connection.exec_driver_sql("SELECT id, name FROM composer ORDER BY id").fetchall(),
            [(4, "Giuseppe Verdi")],
        )
        self.assertEqual(
            self.connection.exec_driver_sql(
                "SELECT classical_concert_id, composer_id FROM classical_concert_composer ORDER BY classical_concert_id"
            ).fetchall(),
            [(10, 4), (11, 4)],
        )
        self.assertEqual(
            self.connection.exec_driver_sql("SELECT composer_id FROM work WHERE id = 20").scalar(),
            4,
        )
        self.assertEqual(
            self.connection.exec_driver_sql(
                "SELECT alias, normalized_alias FROM composer_alias"
            ).one(),
            ("Verdi", "verdi"),
        )

    def test_merge_stops_on_work_collision(self):
        self.connection.exec_driver_sql(
            "INSERT INTO composer VALUES (4, 'Giuseppe Verdi', 'giuseppe verdi'), (575, 'Verdi', 'verdi')"
        )
        self.connection.exec_driver_sql(
            "INSERT INTO work VALUES (20, 4, 'Aida', 'aida'), (21, 575, 'Aida', 'aida')"
        )

        with self.assertRaisesRegex(RuntimeError, "colliding works"):
            migration._merge_composer(self.connection, "Verdi", "Giuseppe Verdi")

        self.assertEqual(
            self.connection.exec_driver_sql("SELECT COUNT(*) FROM composer").scalar(),
            2,
        )

    def test_renormalize_uses_application_compatible_keys(self):
        self.connection.exec_driver_sql(
            "INSERT INTO composer VALUES (2, 'Antonín Dvořák', 'antonín dvořák')"
        )

        migration._renormalize_composers(self.connection)

        self.assertEqual(
            self.connection.exec_driver_sql(
                "SELECT normalized_name FROM composer WHERE id = 2"
            ).scalar(),
            "antonin dvorak",
        )


if __name__ == "__main__":
    unittest.main()
