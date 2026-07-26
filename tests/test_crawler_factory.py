import json
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from automation.run_crawler_factory import (
    crawler_directory,
    load_state,
    normalize_blocked_metadata,
    select_urls,
    validate_change_scope,
)
from automation.validate_crawler_pr import (
    PullRequestValidationError,
    generated_directories,
    is_transient_failure,
)
from automation.validate_generated_crawler import (
    ValidationError,
    _validate_records,
    canonical_source_url,
    parse_blocked_metadata,
    validate_blocked,
)


def blocked_text(url: str, country_code: str, attempted: date) -> str:
    metadata = {
        "url": url,
        "country_code": country_code,
        "reason_code": "no_current_events",
        "attempted_at": attempted.isoformat(),
        "retry_after": (attempted + timedelta(days=30)).isoformat(),
    }
    evidence = " ".join(["investigation evidence"] * 25)
    return (
        "<!-- crawler-factory-metadata\n"
        f"{json.dumps(metadata)}\n"
        "-->\n\n"
        f"# Blocked\n\n{evidence}\n"
    )


class BlockedMetadataTests(unittest.TestCase):
    def test_valid_blocked_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "BLOCKED.md"
            today = date.today()
            path.write_text(blocked_text("https://example.cz/", "CZ", today), encoding="utf-8")

            result = validate_blocked(path, "https://example.cz/", "CZ")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["metadata"]["retry_after"], (today + timedelta(days=30)).isoformat())

    def test_invalid_retry_interval_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "BLOCKED.md"
            metadata = {
                "url": "https://example.cz/",
                "country_code": "CZ",
                "reason_code": "no_current_events",
                "attempted_at": date.today().isoformat(),
                "retry_after": (date.today() + timedelta(days=29)).isoformat(),
            }
            path.write_text(
                f"<!-- crawler-factory-metadata\n{json.dumps(metadata)}\n-->\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValidationError, "exactly 30 days"):
                parse_blocked_metadata(path)

    def test_worker_normalizes_blocked_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            url = "https://www.hamu.cz/"
            path = workspace / crawler_directory(url) / "BLOCKED.md"
            path.parent.mkdir(parents=True)
            path.write_text(
                "<!-- crawler-factory-metadata\n"
                '{"url":"https://hamu.cz","country_code":"CZ",'
                '"reason_code":"no_current_events","attempted_at":"2020-01-01",'
                '"retry_after":"2020-02-01"}\n'
                "-->\n\nThe source currently publishes no concerts.\n",
                encoding="utf-8",
            )
            attempted = date(2026, 7, 25)

            normalize_blocked_metadata(workspace, url, "CZ", attempted)
            metadata = parse_blocked_metadata(path)

        self.assertEqual(metadata["url"], url)
        self.assertEqual(metadata["attempted_at"], "2026-07-25")
        self.assertEqual(metadata["retry_after"], "2026-08-24")


class CrawlerValidationTests(unittest.TestCase):
    def test_source_url_canonicalization(self):
        self.assertEqual(
            canonical_source_url("https://www.bachcollegium.cz/"),
            canonical_source_url("http://bachcollegium.cz"),
        )
        self.assertNotEqual(
            canonical_source_url("https://salvator.farnost.cz/"),
            canonical_source_url("https://farnostsalvator.cz/"),
        )

    def test_configured_deduplication_runs_before_duplicate_validation(self):
        import pandas as pd

        class Config:
            front_fields = [
                ("source", "Example"),
                ("source_url", "https://example.cz"),
            ]
            country_code = "CZ"
            dedupe_subset = ["title", "date", "time_from"]

        class Crawler:
            config = Config()

            @staticmethod
            def build_dataframe(records):
                return pd.DataFrame(records)

            @staticmethod
            def transform(frame):
                return frame

        record = {
            "title": "Concert",
            "date": "2026-08-01",
            "time_from": "19:00",
            "url": "https://example.cz/concert",
        }

        normalized = _validate_records([record, record.copy()], Crawler(), "https://example.cz", "CZ")

        self.assertEqual(len(normalized), 1)

    def test_short_but_meaningful_blocked_explanation_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "BLOCKED.md"
            path.write_text(
                blocked_text("https://example.cz/", "CZ", date.today()).split("# Blocked")[0]
                + "# Blocked\n\nNo concerts are currently published.\n",
                encoding="utf-8",
            )

            result = validate_blocked(path, "https://www.example.cz", "CZ")

        self.assertEqual(result["status"], "blocked")


class SelectionTests(unittest.TestCase):
    URLS = [
        "https://www.hamu.cz/",
        "https://www.berg.cz/",
        "https://www.fok.cz/",
    ]

    def test_selection_skips_existing_and_respects_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            existing = workspace / crawler_directory(self.URLS[0])
            existing.mkdir(parents=True)
            (existing / "main.py").write_text("", encoding="utf-8")

            selected = select_urls(self.URLS, workspace, {"urls": {}}, date.today(), 1)

        self.assertEqual(selected, [self.URLS[1]])

    def test_blocked_source_is_due_after_retry_date(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            blocked = workspace / crawler_directory(self.URLS[0]) / "BLOCKED.md"
            blocked.parent.mkdir(parents=True)
            attempted = date.today() - timedelta(days=30)
            blocked.write_text(blocked_text(self.URLS[0], "CZ", attempted), encoding="utf-8")

            selected = select_urls(self.URLS, workspace, {"urls": {}}, date.today(), 1)

        self.assertEqual(selected, [self.URLS[0]])

    def test_recent_blocked_source_is_not_due(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            blocked = workspace / crawler_directory(self.URLS[0]) / "BLOCKED.md"
            blocked.parent.mkdir(parents=True)
            attempted = date.today() - timedelta(days=5)
            blocked.write_text(blocked_text(self.URLS[0], "CZ", attempted), encoding="utf-8")

            selected = select_urls(self.URLS, workspace, {"urls": {}}, date.today(), 1)

        self.assertEqual(selected, [self.URLS[1]])

    def test_failure_backoff_skips_url(self):
        state = {
            "urls": {
                self.URLS[0]: {
                    "next_attempt_at": (date.today() + timedelta(days=7)).isoformat()
                }
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            selected = select_urls(self.URLS, Path(temporary), state, date.today(), 1)

        self.assertEqual(selected, [self.URLS[1]])

    def test_invalid_state_file_falls_back_to_empty_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_state(path), {"urls": {}})


class ScopeTests(unittest.TestCase):
    def test_expected_main_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            expected = Path("crawlers/cz/example_cz")
            path = workspace / expected / "main.py"
            path.parent.mkdir(parents=True)
            path.write_text("# generated\n", encoding="utf-8")

            validate_change_scope(workspace, expected)

    def test_unrelated_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            expected = Path("crawlers/cz/example_cz")
            path = workspace / expected / "main.py"
            path.parent.mkdir(parents=True)
            path.write_text("# generated\n", encoding="utf-8")
            (workspace / "pyproject.toml").write_text("# changed\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "outside the allowed scope"):
                validate_change_scope(workspace, expected)


class PullRequestScopeTests(unittest.TestCase):
    def initialize_repository(self, workspace: Path) -> None:
        subprocess.run(["git", "init", "-q", "-b", "master"], cwd=workspace, check=True)
        (workspace / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "base",
            ],
            cwd=workspace,
            check=True,
        )

    def commit(self, workspace: Path, message: str) -> None:
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                message,
            ],
            cwd=workspace,
            check=True,
        )

    def test_new_crawler_directory_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self.initialize_repository(workspace)
            subprocess.run(["git", "switch", "-q", "-c", "crawler-factory/test"], cwd=workspace, check=True)
            main_path = workspace / "crawlers/cz/example_cz/main.py"
            main_path.parent.mkdir(parents=True)
            main_path.write_text("# generated\n", encoding="utf-8")
            self.commit(workspace, "generated")

            directories = generated_directories(workspace, "master")

        self.assertEqual(directories, [Path("crawlers/cz/example_cz")])

    def test_existing_crawler_modification_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self.initialize_repository(workspace)
            main_path = workspace / "crawlers/cz/example_cz/main.py"
            main_path.parent.mkdir(parents=True)
            main_path.write_text("# original\n", encoding="utf-8")
            self.commit(workspace, "existing crawler")
            subprocess.run(["git", "switch", "-q", "-c", "crawler-factory/test"], cwd=workspace, check=True)
            main_path.write_text("# changed\n", encoding="utf-8")
            self.commit(workspace, "modify crawler")

            with self.assertRaisesRegex(PullRequestValidationError, "existing crawler"):
                generated_directories(workspace, "master")

    def test_only_transient_failures_are_retried(self):
        self.assertTrue(is_transient_failure({"error": "ReadTimeout: source was slow"}))
        self.assertFalse(
            is_transient_failure({"error": "ValidationError: country_code does not match"})
        )


if __name__ == "__main__":
    unittest.main()
