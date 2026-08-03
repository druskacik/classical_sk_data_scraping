import unittest

from automation.crawler_registry import (
    identity_is_mutable,
    normalized_geographic_identity,
)


class CrawlerRegistryGeographyTests(unittest.TestCase):
    def test_unknown_scope_accepts_nullable_seed_hints(self):
        self.assertEqual(
            normalized_geographic_identity(None, "unknown", None),
            (None, "unknown", None),
        )
        self.assertEqual(
            normalized_geographic_identity(
                "es", "unknown", "crawlers/es/example_es"
            ),
            ("ES", "unknown", "crawlers/es/example_es"),
        )

    def test_country_scope_requires_matching_country_path(self):
        self.assertEqual(
            normalized_geographic_identity(
                "es", "country", "crawlers/es/example_es"
            ),
            ("ES", "country", "crawlers/es/example_es"),
        )
        with self.assertRaisesRegex(ValueError, "must match"):
            normalized_geographic_identity(
                "ES", "country", "crawlers/de/example_es"
            )

    def test_multi_country_scope_requires_common_path_and_null_country(self):
        self.assertEqual(
            normalized_geographic_identity(
                None, "multi_country", "crawlers/common/example"
            ),
            (None, "multi_country", "crawlers/common/example"),
        )
        with self.assertRaisesRegex(ValueError, "null country"):
            normalized_geographic_identity(
                "DE", "multi_country", "crawlers/common/example"
            )
        with self.assertRaisesRegex(ValueError, "null country"):
            normalized_geographic_identity(
                None, "multi_country", "crawlers/de/example"
            )

    def test_geographic_scope_is_closed(self):
        with self.assertRaisesRegex(ValueError, "geographic_scope"):
            normalized_geographic_identity(None, "global", None)

    def test_only_unresolved_non_operational_identities_are_mutable(self):
        self.assertTrue(
            identity_is_mutable({"geographic_scope": "unknown", "status": "pending"})
        )
        self.assertFalse(
            identity_is_mutable({"geographic_scope": "country", "status": "pending"})
        )
        for status in ("processing", "pr_open", "active", "blocked", "disabled"):
            with self.subTest(status=status):
                self.assertFalse(
                    identity_is_mutable(
                        {"geographic_scope": "unknown", "status": status}
                    )
                )


if __name__ == "__main__":
    unittest.main()
