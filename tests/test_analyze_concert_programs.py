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
        self.assertIn(
            'python -m agent_utils.concert_catalog find-composer --name "NAME"',
            prompt,
        )
        self.assertIn("python -m agent_utils.concert_catalog list-works --composer-id ID", prompt)
        self.assertNotIn("uv run", prompt)
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
            "composers": [],
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
            "composers": [],
            "program": [{"candidate": "retained for audit"}],
        }
        analyzer.validate_result(MagicMock(), concert, result)

    def test_complete_result_requires_program(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "complete",
            "source_url": "https://example.test",
            "notes": "",
            "composers": [],
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
        self.assertIn("a.attempts < %s", query)
        self.assertIn("make_interval(days => %s)", query)
        self.assertEqual(
            cursor.execute.call_args.args[1],
            (
                analyzer.MAX_AUTOMATIC_ATTEMPTS,
                analyzer.NO_PROGRAM_RETRY_INTERVAL_DAYS,
                analyzer.MAX_AUTOMATIC_ATTEMPTS,
                25,
            ),
        )

    def test_composer_only_requires_composers_and_no_program(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "composer_only",
            "source_url": "https://example.test",
            "notes": "Mozart works are not specified.",
            "composers": [],
            "program": [],
        }
        with self.assertRaisesRegex(ValueError, "must contain composers"):
            analyzer.validate_result(MagicMock(), concert, result)

    @patch.object(analyzer, "_resolve_composer", return_value=17)
    def test_composer_only_replaces_catalogue_links(self, _resolve_composer):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "composer_only",
            "source_url": concert.url,
            "notes": "No works named.",
            "composers": [{"existing_id": 17, "name": "Wolfgang Amadeus Mozart"}],
            "program": [],
        }

        analyzer.persist_result(conn, concert, result, "gpt-5.6-terra")

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("DELETE FROM classical_concert_work" in query for query in executed))
        self.assertTrue(any("DELETE FROM classical_concert_composer" in query for query in executed))
        self.assertTrue(any("INSERT INTO classical_concert_composer" in query for query in executed))
        upsert = next(query for query in executed if "INSERT INTO concert_program_analysis" in query)
        self.assertIn("EXCLUDED.status = 'no_program'", upsert)
        conn.commit.assert_called_once()

    def test_complete_requires_program_composers_in_top_level_list(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "complete",
            "source_url": concert.url,
            "notes": "",
            "composers": [{"existing_id": 1, "name": "Wolfgang Amadeus Mozart"}],
            "program": [
                {
                    "composer": {"existing_id": 2, "name": "Joseph Haydn"},
                    "work": {"existing_id": None, "title": "Symphony No. 1", "catalogue_number": None},
                    "programme_label": "Symphony No. 1",
                    "evidence": "Programme listing",
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "top-level composers"):
            analyzer.validate_result(conn, concert, result)

    def test_agent_threads_are_persistent(self):
        codex = MagicMock()
        thread = codex.thread_start.return_value
        thread.turn.return_value.run.return_value.error = None
        thread.turn.return_value.run.return_value.final_response = json.dumps(
            {
                "status": "no_program",
                "source_url": "https://example.test",
                "notes": "No programme published.",
                "composers": [],
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
            "composers": [],
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
        self.assertIn("composer_only", analyzer.OUTPUT_SCHEMA["properties"]["status"]["enum"])
        self.assertIn("composers", analyzer.OUTPUT_SCHEMA["required"])
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
