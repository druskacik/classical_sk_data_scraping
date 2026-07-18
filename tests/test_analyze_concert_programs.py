import io
import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from agent_utils.concert_catalog import normalize
from analyzers import analyze_concert_programs as analyzer


class AnalyzeConcertProgramsTests(unittest.TestCase):
    def test_normalize_handles_diacritics_and_punctuation(self):
        self.assertEqual(normalize("  Antonín DVOŘÁK — op. 95 "), "antonin dvorak op 95")

    def test_prompt_requires_live_url_before_description(self):
        prompt = analyzer.render_prompt(
            analyzer.Concert(7, "Test", date(2026, 8, 1), "https://example.test/event", "fallback")
        )
        self.assertIn("Always try to open and inspect the live URL first", prompt)
        self.assertIn("fallback context", prompt)
        self.assertIn("list-works --composer-id ID", prompt)
        self.assertIn("standard English title of the complete composition", prompt)
        self.assertIn("standard English name in programme_label", prompt)
        self.assertIn("original wording in evidence", prompt)
        self.assertLess(prompt.index("URL: https://example.test/event"), prompt.index("fallback"))

    def test_no_program_result_must_not_include_program_entries(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "no_program",
            "source_url": "https://example.test",
            "notes": "unclear",
            "program": [{"unexpected": "entry"}],
        }
        with self.assertRaisesRegex(ValueError, "must not contain"):
            analyzer.validate_result(MagicMock(), concert, result)

    def test_ambiguous_result_may_retain_candidates_without_validation(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "ambiguous",
            "source_url": "https://example.test",
            "notes": "unclear",
            "program": [{"candidate": "retained for audit"}],
        }
        analyzer.validate_result(MagicMock(), concert, result)

    def test_complete_result_requires_program(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "complete",
            "source_url": "https://example.test",
            "notes": "",
            "program": [],
        }
        with self.assertRaisesRegex(ValueError, "at least one"):
            analyzer.validate_result(MagicMock(), concert, result)

    def test_automatic_selection_excludes_past_concerts(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []

        analyzer.select_concerts(conn, concert_ids=None, limit=25, force=False)

        query = cursor.execute.call_args.args[0]
        self.assertIn("c.program_analysis_eligible = true", query)
        self.assertIn("c.date >= CURRENT_DATE", query)

    def test_agent_threads_are_persistent(self):
        codex = MagicMock()
        thread = codex.thread_start.return_value
        thread.turn.return_value.run.return_value.error = None
        thread.turn.return_value.run.return_value.final_response = json.dumps(
            {
                "status": "no_program",
                "source_url": "https://example.test",
                "notes": "No programme published.",
                "program": [],
            }
        )
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)

        analyzer.run_agent(codex, concert, "gpt-5.6-terra", timeout_seconds=30)

        self.assertIs(codex.thread_start.call_args.kwargs["ephemeral"], False)

    @patch.object(analyzer, "persist_result")
    @patch.object(analyzer, "validate_result")
    @patch.object(analyzer, "run_agent")
    @patch.object(analyzer, "validate_model")
    @patch.object(analyzer, "select_concerts")
    @patch.object(analyzer, "get_connection")
    @patch.object(analyzer, "Codex")
    def test_dry_run_never_persists(
        self,
        codex_class,
        get_connection,
        select_concerts,
        _validate_model,
        run_agent,
        _validate_result,
        persist_result,
    ):
        conn = MagicMock()
        get_connection.return_value = conn
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        select_concerts.return_value = [concert]
        run_agent.return_value = {
            "status": "complete",
            "source_url": concert.url,
            "notes": "",
            "program": [],
        }
        codex_class.return_value.__enter__.return_value = MagicMock()
        with patch("sys.stdout", new_callable=io.StringIO):
            failures = analyzer.run(concert_ids=[1], commit=False)
        self.assertEqual(failures, 0)
        config = codex_class.call_args.args[0]
        self.assertIsNone(config.codex_bin)
        persist_result.assert_not_called()
        conn.commit.assert_not_called()
        conn.close.assert_called_once()

    def test_output_schema_enforces_paired_entities(self):
        item = analyzer.OUTPUT_SCHEMA["properties"]["program"]["items"]
        self.assertEqual(
            item["required"],
            ["composer", "work", "programme_label", "evidence"],
        )

    def test_model_validation_falls_back_to_local_catalogue(self):
        codex = MagicMock()
        codex.models.side_effect = ValueError("new enum value")
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "models_cache.json"), "w", encoding="utf-8") as handle:
                json.dump({"models": [{"slug": "gpt-5.6-terra"}]}, handle)
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                analyzer.validate_model(codex, "gpt-5.6-terra")


if __name__ == "__main__":
    unittest.main()
