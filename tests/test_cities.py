import math
import unittest
from unittest.mock import MagicMock

from crawlers.cities import clean_city_raw, normalize_city_key, resolve_city


class CityResolutionTests(unittest.TestCase):
    def test_cleans_raw_value_without_translating_it(self):
        self.assertEqual(clean_city_raw("  Bad   Kissingen "), "Bad Kissingen")
        self.assertEqual(normalize_city_key(" Praha "), "praha")

    def test_converts_missing_values_to_none(self):
        for value in (None, "", " NaN ", "null", math.nan):
            with self.subTest(value=value):
                self.assertIsNone(clean_city_raw(value))

    def test_prefers_unique_source_scoped_alias(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [(12, "Hukvaldy", "Hukvaldy", "CZ")]
        result = resolve_city(cursor, "Hukvaldyvstupné 400 Kč", "Festival")
        self.assertEqual(result.city_id, 12)
        self.assertEqual(cursor.execute.call_args.args[1],
                         ("hukvaldyvstupné 400 kč", "Festival"))

    def test_global_alias_must_be_unambiguous(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            (1, "Springfield", "Springfield", "US"),
            (2, "Springfield", "Springfield", "US"),
        ]
        self.assertIsNone(resolve_city(cursor, "Springfield"))


if __name__ == "__main__":
    unittest.main()
