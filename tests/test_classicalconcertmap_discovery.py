import csv
import tempfile
import unittest
from pathlib import Path

from source_discovery.classicalconcertmap import compile_seed_csv


def _write_csv(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class ClassicalConcertMapDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compile_seed_applies_reviewed_country_and_scope_overrides(self):
        discovery = self.tmp_path / "discovery.csv"
        overrides = self.tmp_path / "overrides.csv"
        output = self.tmp_path / "seed.csv"
        fields = [
            "org_id",
            "name",
            "org_type",
            "event_count",
            "country_code",
            "sample_event_url",
            "homepage_url",
        ]
        _write_csv(
            discovery,
            fields,
            [
                {
                    "org_id": "1",
                    "name": "Touring orchestra",
                    "country_code": "DE",
                    "sample_event_url": "https://venue.example/event",
                    "homepage_url": "https://venue.example/event",
                },
                {
                    "org_id": "2",
                    "name": "Ticket platform",
                    "country_code": "US",
                    "sample_event_url": "https://tickets.example/show/1",
                    "homepage_url": "https://tickets.example/",
                },
            ],
        )
        _write_csv(
            overrides,
            ["url", "country_code", "scope_hint", "notes"],
            [
                {
                    "url": "https://venue.example/",
                    "country_code": "ES",
                    "scope_hint": "country",
                    "notes": "official address",
                },
                {
                    "url": "https://tickets.example/",
                    "country_code": "",
                    "scope_hint": "multi_country",
                    "notes": "global platform",
                },
            ],
        )

        compile_seed_csv(discovery, output, overrides)

        with output.open(newline="", encoding="utf-8") as handle:
            rows = {row["url"]: row for row in csv.DictReader(handle)}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows["https://venue.example/"]["country_code"], "ES")
        self.assertEqual(rows["https://venue.example/"]["crawler_path"], "crawlers/es/venue_example")
        self.assertEqual(rows["https://tickets.example/"]["country_code"], "")
        self.assertEqual(rows["https://tickets.example/"]["scope_hint"], "multi_country")
        self.assertEqual(
            rows["https://tickets.example/"]["crawler_path"],
            "crawlers/common/tickets_example",
        )
    def test_compile_seed_rejects_stale_override(self):
        discovery = self.tmp_path / "discovery.csv"
        overrides = self.tmp_path / "overrides.csv"
        _write_csv(
            discovery,
            ["org_id", "name", "country_code", "sample_event_url", "homepage_url"],
            [],
        )
        _write_csv(
            overrides,
            ["url", "country_code", "scope_hint", "notes"],
            [{"url": "https://missing.example/", "country_code": "FR", "scope_hint": "country"}],
        )

        with self.assertRaisesRegex(ValueError, "Overrides do not match discovery rows"):
            compile_seed_csv(discovery, self.tmp_path / "seed.csv", overrides)
