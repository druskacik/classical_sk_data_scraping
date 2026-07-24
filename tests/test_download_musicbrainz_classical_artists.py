import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from downloaders.download_musicbrainz_classical_artists import (
    artist_to_row,
    download,
    enrich_urls,
    url_fields,
)


class MusicBrainzDownloaderTests(unittest.TestCase):
    def test_url_fields_preserve_typed_relations(self) -> None:
        fields = url_fields(
            [
                {
                    "type": "official homepage",
                    "type-id": "homepage-id",
                    "ended": False,
                    "url": {"resource": "https://orchestra.example/events"},
                },
                {
                    "type": "social network",
                    "type-id": "social-id",
                    "ended": True,
                    "url": {"resource": "https://social.example/orchestra"},
                },
            ]
        )

        self.assertEqual(
            fields["official_homepages"],
            "https://orchestra.example/events",
        )
        relations = json.loads(fields["url_relations_json"])
        self.assertEqual(relations[1]["type"], "social network")
        self.assertTrue(relations[1]["ended"])

    def test_artist_to_row_preserves_classical_tag_score(self) -> None:
        row = artist_to_row(
            {
                "id": "artist-id",
                "name": "Test Orchestra",
                "sort-name": "Test Orchestra",
                "type": "Orchestra",
                "country": "SK",
                "area": {"id": "area-id", "name": "Bratislava"},
                "life-span": {"begin": "1949", "end": None, "ended": False},
                "score": 100,
                "tags": [
                    {"name": "classical", "count": -2},
                    {"name": "symphony orchestra", "count": 3},
                ],
            }
        )

        self.assertEqual(row["classical_tag_score"], -2)
        self.assertEqual(row["area"], "Bratislava")
        self.assertEqual(json.loads(row["tags_json"])[1]["name"], "symphony orchestra")

    @patch("downloaders.download_musicbrainz_classical_artists.fetch_page")
    def test_download_writes_paginated_results(self, fetch_page) -> None:
        pages = {
            0: {"count": 2, "artists": [{"id": "one", "name": "One"}]},
            1: {"count": 2, "artists": [{"id": "two", "name": "Two"}]},
        }
        fetch_page.side_effect = lambda session, offset, **kwargs: pages[offset]

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "artists.csv"
            count = download(
                output,
                delay=0,
                timeout=1,
                retries=0,
                max_pages=None,
                user_agent="test",
                quiet=True,
            )

            with output.open(encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))

        self.assertEqual(count, 2)
        self.assertEqual([row["musicbrainz_id"] for row in rows], ["one", "two"])

    @patch("downloaders.download_musicbrainz_classical_artists.fetch_artist_urls")
    def test_enrich_urls_reads_existing_csv(self, fetch_artist_urls) -> None:
        fetch_artist_urls.return_value = [
            {
                "type": "official homepage",
                "url": {"resource": "https://one.example"},
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "artists.csv"
            output_path = Path(tmp_dir) / "artists_with_urls.csv"
            with input_path.open("w", encoding="utf-8", newline="") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=["musicbrainz_id", "name"],
                )
                writer.writeheader()
                writer.writerow({"musicbrainz_id": "one", "name": "One"})

            count = enrich_urls(
                input_path,
                output_path,
                delay=0,
                timeout=1,
                retries=0,
                max_artists=None,
                user_agent="test",
                quiet=True,
            )
            with output_path.open(encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))

        self.assertEqual(count, 1)
        self.assertEqual(rows[0]["official_homepages"], "https://one.example")


if __name__ == "__main__":
    unittest.main()
