import json
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import automation.run_crawler_factory as factory
from automation.run_crawler_factory import (
    attempt_url,
    crawler_directory,
    load_state,
    normalize_blocked_metadata,
    parse_blocked_metadata,
    select_urls,
    validate_change_scope,
)
from automation.validate_crawler_pr import (
    PullRequestValidationError,
    generated_directories,
    validate_directory,
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

            result = parse_blocked_metadata(path)

        self.assertEqual(result["retry_after"], (today + timedelta(days=30)).isoformat())

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

    def test_main_and_blocked_together_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            expected = Path("crawlers/cz/example_cz")
            directory = workspace / expected
            directory.mkdir(parents=True)
            (directory / "main.py").write_text("# generated\n", encoding="utf-8")
            (directory / "BLOCKED.md").write_text("blocked\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                validate_change_scope(workspace, expected)


class AttemptTests(unittest.TestCase):
    def test_nonzero_builder_result_is_preserved_when_scope_is_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            run_dir = root / "runs"
            workspace.mkdir()
            run_dir.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            original_run_command = factory.run_command

            def fake_run_command(command, **kwargs):
                if any(str(part).endswith("build_crawlers_with_codex.py") for part in command):
                    path = workspace / crawler_directory("https://www.hamu.cz/") / "main.py"
                    path.parent.mkdir(parents=True)
                    path.write_text("# useful partial result\n", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 1, "", "builder failed")
                return original_run_command(command, **kwargs)

            with (
                patch.object(factory, "run_command", side_effect=fake_run_command),
                patch.object(factory, "git_commit", return_value="abc123"),
            ):
                result = attempt_url(
                    workspace,
                    run_dir,
                    "https://www.hamu.cz/",
                    30,
                    {},
                )

        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["commit"], "abc123")
        self.assertEqual(result["generation_warning"], "builder exited with status 1")

    def test_timed_out_builder_result_is_preserved_when_scope_is_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            run_dir = root / "runs"
            workspace.mkdir()
            run_dir.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            original_run_command = factory.run_command

            def fake_run_command(command, **kwargs):
                if any(str(part).endswith("build_crawlers_with_codex.py") for part in command):
                    path = workspace / crawler_directory("https://www.hamu.cz/") / "main.py"
                    path.parent.mkdir(parents=True)
                    path.write_text("# useful partial result\n", encoding="utf-8")
                    raise subprocess.TimeoutExpired(command, 60, output="partial log\n")
                return original_run_command(command, **kwargs)

            with (
                patch.object(factory, "run_command", side_effect=fake_run_command),
                patch.object(factory, "git_commit", return_value="abc123"),
            ):
                result = attempt_url(
                    workspace,
                    run_dir,
                    "https://www.hamu.cz/",
                    1,
                    {},
                )

        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["commit"], "abc123")
        self.assertEqual(result["generation_warning"], "builder exceeded 1 minute")


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

    def test_valid_python_is_compiled_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            directory = Path("crawlers/cz/example_cz")
            main_path = workspace / directory / "main.py"
            main_path.parent.mkdir(parents=True)
            main_path.write_text(
                "raise RuntimeError('must not execute')\n",
                encoding="utf-8",
            )

            result = validate_directory(workspace, directory)

        self.assertEqual(result, {"status": "passed", "kind": "crawler"})

    def test_invalid_python_syntax_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            directory = Path("crawlers/cz/example_cz")
            main_path = workspace / directory / "main.py"
            main_path.parent.mkdir(parents=True)
            main_path.write_text("def broken(:\n", encoding="utf-8")

            with self.assertRaisesRegex(PullRequestValidationError, "invalid Python syntax"):
                validate_directory(workspace, directory)

    def test_blocked_result_does_not_require_live_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            directory = Path("crawlers/cz/example_cz")
            blocked_path = workspace / directory / "BLOCKED.md"
            blocked_path.parent.mkdir(parents=True)
            blocked_path.write_text("Temporarily unavailable.\n", encoding="utf-8")

            result = validate_directory(workspace, directory)

        self.assertEqual(result, {"status": "passed", "kind": "blocked"})


if __name__ == "__main__":
    unittest.main()
