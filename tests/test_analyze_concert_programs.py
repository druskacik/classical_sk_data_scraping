import io
import json
import os
import tempfile
import unittest
from datetime import date, time
from unittest.mock import MagicMock, patch

from agent_utils import concert_catalog
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
        self.assertIn("commonly established in English-language classical references", prompt)
        self.assertIn("original wording in evidence", prompt)
        self.assertIn("Keep uncertainty local", prompt)
        self.assertIn("Divertimento in D major (selection)", prompt)
        self.assertIn("without a Köchel number", prompt)
        self.assertIn("Your primary task is the composer and work extraction", prompt)
        self.assertIn("Otherwise return an empty event_updates list", prompt)
        self.assertLess(
            prompt.index("Your primary task is the composer and work extraction"),
            prompt.index("As a secondary best-effort task"),
        )
        self.assertLess(prompt.index("URL: https://example.test/event"), prompt.index("fallback"))

    @patch.object(concert_catalog, "get_connection")
    def test_composer_lookup_uses_alias_and_returns_canonical_name_once(self, get_connection):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            (5, "Pyotr Ilyich Tchaikovsky", "pyotr ilyich tchaikovsky", "Piotr Iľjič Čajkovskij", "piotr iljic cajkovskij"),
            (5, "Pyotr Ilyich Tchaikovsky", "pyotr ilyich tchaikovsky", "P. I. Čajkovskij", "p i cajkovskij"),
            (16, "Dmitri Shostakovich", "dmitri shostakovich", "Dmitrij Šostakovič", "dmitrij sostakovic"),
        ]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        get_connection.return_value = connection

        result = concert_catalog.find_composers("Piotr Iľjič Čajkovskij")

        self.assertEqual(
            result[0],
            {"id": 5, "name": "Pyotr Ilyich Tchaikovsky", "score": 1.0},
        )
        self.assertEqual([item["id"] for item in result].count(5), 1)
        self.assertIn("LEFT JOIN composer_alias", cursor.execute.call_args.args[0])

    def test_composer_lookup_rejects_empty_input_without_database_query(self):
        with patch.object(concert_catalog, "get_connection") as get_connection:
            self.assertEqual(concert_catalog.find_composers(" -- "), [])
        get_connection.assert_not_called()

    def test_no_program_result_must_not_include_program_entries(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "no_program",
            "source_url": "https://example.test",
            "notes": "unclear",
            "composers": [],
            "program": [{"unexpected": "entry"}],
            "unresolved_program": [],
        }
        with self.assertRaisesRegex(ValueError, "must not contain"):
            analyzer.validate_result(MagicMock(), concert, result)

    def test_ambiguous_result_requires_only_unresolved_entries(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "ambiguous",
            "source_url": "https://example.test",
            "notes": "unclear",
            "composers": [],
            "program": [],
            "unresolved_program": [
                {
                    "programme_label": "Prelude or Toccata",
                    "evidence": "The page lists both alternatives.",
                    "reason": "The performed alternative is not identified.",
                }
            ],
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
            "unresolved_program": [],
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
        self.assertIn("a.status IN ('no_program', 'partial', 'ambiguous')", query)
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
            "unresolved_program": [],
        }
        with self.assertRaisesRegex(ValueError, "must contain composers"):
            analyzer.validate_result(MagicMock(), concert, result)

    def test_partial_requires_confident_and_unresolved_entries(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "partial",
            "source_url": concert.url,
            "notes": "",
            "composers": [{"existing_id": 1, "name": "Wolfgang Amadeus Mozart"}],
            "program": [],
            "unresolved_program": [
                {
                    "programme_label": "Divertimento in D major (selection)",
                    "evidence": "The page lists the title without a Köchel number.",
                    "reason": "Multiple Mozart divertimenti match.",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "confident programme entries"):
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
            "unresolved_program": [],
        }

        analyzer.persist_result(conn, concert, result, "gpt-5.6-terra")

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("DELETE FROM classical_concert_work" in query for query in executed))
        self.assertTrue(any("DELETE FROM classical_concert_composer" in query for query in executed))
        self.assertTrue(any("INSERT INTO classical_concert_composer" in query for query in executed))
        upsert = next(query for query in executed if "INSERT INTO concert_program_analysis" in query)
        self.assertIn("EXCLUDED.status = 'no_program'", upsert)
        conn.commit.assert_called_once()

    @patch.object(analyzer, "_resolve_work", return_value=96)
    @patch.object(analyzer, "_resolve_composer", return_value=1)
    def test_partial_saves_confident_work_but_not_unresolved_mozart_divertimento(
        self,
        _resolve_composer,
        resolve_work,
    ):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        concert = analyzer.Concert(2392, "Vivaldi Four Seasons", date.today(), "https://example.test", None)
        composer = {"existing_id": 1, "name": "Wolfgang Amadeus Mozart"}
        result = {
            "status": "partial",
            "source_url": concert.url,
            "notes": "One Mozart slot cannot be identified.",
            "composers": [composer],
            "program": [
                {
                    "composer": composer,
                    "work": {
                        "existing_id": 96,
                        "title": "Exsultate, jubilate",
                        "catalogue_number": "K. 165",
                    },
                    "programme_label": "Alleluia",
                    "evidence": "The page names Exsultate, jubilate.",
                }
            ],
            "unresolved_program": [
                {
                    "programme_label": "Divertimento in D major (selection)",
                    "evidence": "The page gives no Köchel number.",
                    "reason": "Multiple Mozart divertimenti in D major match.",
                }
            ],
        }

        analyzer.persist_result(conn, concert, result, "gpt-5.6-terra")

        resolve_work.assert_called_once()
        self.assertEqual(resolve_work.call_args.args[2]["title"], "Exsultate, jubilate")
        work_inserts = [
            call
            for call in cursor.execute.call_args_list
            if "INSERT INTO classical_concert_work" in call.args[0]
        ]
        self.assertEqual(len(work_inserts), 1)
        self.assertEqual(work_inserts[0].args[1][2], "Alleluia")
        analysis_upsert = next(
            call for call in cursor.execute.call_args_list if "INSERT INTO concert_program_analysis" in call.args[0]
        )
        self.assertEqual(analysis_upsert.args[1][1], "partial")

    def test_degraded_retry_preserves_existing_partial_result(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("partial",)
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        result = {
            "status": "ambiguous",
            "source_url": concert.url,
            "notes": "The updated page is unclear.",
            "composers": [],
            "program": [],
            "unresolved_program": [
                {
                    "programme_label": "Unclear selection",
                    "evidence": "The page is unclear.",
                    "reason": "No work is uniquely identified.",
                }
            ],
        }

        analyzer.persist_result(conn, concert, result, "gpt-5.6-terra")

        executed = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(any("DELETE FROM classical_concert_work" in query for query in executed))
        self.assertFalse(any("INSERT INTO concert_program_analysis" in query for query in executed))
        update = next(query for query in executed if "UPDATE concert_program_analysis" in query)
        self.assertIn("attempts = attempts + 1", update)
        conn.commit.assert_called_once()

    def test_error_retry_preserves_existing_partial_status(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)

        analyzer.persist_error(conn, concert, "gpt-5.6-terra", RuntimeError("network error"))

        upsert = cursor.execute.call_args.args[0]
        self.assertIn("concert_program_analysis.status = 'partial'", upsert)
        self.assertIn("THEN concert_program_analysis.model", upsert)
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
            "unresolved_program": [],
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

    @patch.object(analyzer, "_resolve_work", side_effect=[31, 31, 31, 44])
    @patch.object(analyzer, "_resolve_composer", return_value=17)
    def test_complete_aggregates_distinct_excerpts_by_canonical_work(
        self,
        _resolve_composer,
        _resolve_work,
    ):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        composer = {"existing_id": 17, "name": "Max Reger"}

        def entry(work_id, title, label, evidence):
            return {
                "composer": composer,
                "work": {
                    "existing_id": work_id,
                    "title": title,
                    "catalogue_number": None,
                },
                "programme_label": label,
                "evidence": evidence,
            }

        result = {
            "status": "complete",
            "source_url": " https://example.test/programme ",
            "notes": "",
            "composers": [composer],
            "unresolved_program": [],
            "program": [
                entry(31, "Twelve Pieces for Organ", "No. 8: Romance", "Lists No. 8."),
                entry(31, "Twelve Pieces for Organ", "No. 11: Toccata", "Lists No. 11."),
                entry(31, "Twelve Pieces for Organ", "No. 11: Toccata", "Lists No. 11."),
                entry(44, "Ave Maria", "Ave Maria", "Lists Ave Maria."),
            ],
        }

        analyzer.persist_result(conn, concert, result, "gpt-5.6-terra")

        work_inserts = [
            call
            for call in cursor.execute.call_args_list
            if "INSERT INTO classical_concert_work" in call.args[0]
        ]
        self.assertEqual(len(work_inserts), 2)
        self.assertEqual(
            work_inserts[0].args[1],
            (
                1,
                31,
                "No. 8: Romance; No. 11: Toccata",
                "https://example.test/programme",
                "Lists No. 8.\nLists No. 11.",
            ),
        )
        self.assertEqual(
            work_inserts[1].args[1],
            (1, 44, "Ave Maria", "https://example.test/programme", "Lists Ave Maria."),
        )
        conn.commit.assert_called_once()

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
                "unresolved_program": [],
                "event_updates": [],
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
            "unresolved_program": [],
            "event_updates": [],
            "location_resolution": {
                "status": "not_needed", "existing_city_id": None,
                "english_name": None, "local_name": None, "country_code": None,
                "external_source": None, "external_id": None, "raw_value_type": None,
                "source_url": "", "evidence": "",
            },
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
        self.assertIn("partial", analyzer.OUTPUT_SCHEMA["properties"]["status"]["enum"])
        self.assertIn("composers", analyzer.OUTPUT_SCHEMA["required"])
        self.assertIn("unresolved_program", analyzer.OUTPUT_SCHEMA["required"])
        self.assertIn("event_updates", analyzer.OUTPUT_SCHEMA["required"])
        self.assertIn("location_resolution", analyzer.OUTPUT_SCHEMA["required"])
        item = analyzer.OUTPUT_SCHEMA["properties"]["program"]["items"]
        self.assertEqual(
            item["required"],
            ["composer", "work", "programme_label", "evidence"],
        )
        unresolved = analyzer.OUTPUT_SCHEMA["properties"]["unresolved_program"]["items"]
        self.assertEqual(
            unresolved["required"],
            ["programme_label", "evidence", "reason"],
        )
        event_update = analyzer.OUTPUT_SCHEMA["properties"]["event_updates"]["items"]
        self.assertEqual(
            event_update["required"],
            ["field", "new_value", "source_url", "evidence"],
        )

    def test_validates_supported_event_updates_and_rejects_location_fields(self):
        conn = MagicMock()
        concert = analyzer.Concert(
            1,
            "Test",
            date(2026, 8, 1),
            "https://example.test",
            None,
            time(19, 0),
            None,
            "Praha",
            "CZ",
            "Old Hall",
            "scheduled",
        )
        updates = [
            {
                "field": "time_from",
                "new_value": "20:00",
                "source_url": concert.url,
                "evidence": "The event page gives 20:00.",
            },
            {
                "field": "city",
                "new_value": "Praha",
                "source_url": concert.url,
                "evidence": "The event page says Praha.",
            },
        ]

        accepted = analyzer.validate_event_updates(conn, concert, updates)

        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["field"], "time_from")
        self.assertEqual(accepted[0]["db_value"], time(20, 0))

    def test_validates_existing_city_and_rejects_country_conflict(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("Prague", "Praha", "CZ")
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        proposal = {
            "status": "existing_city", "existing_city_id": 7,
            "english_name": None, "local_name": None, "country_code": "CZ",
            "external_source": None, "external_id": None,
            "raw_value_type": "legitimate_name",
            "source_url": "https://example.test", "evidence": "The page says Praha.",
        }
        accepted = analyzer.validate_location_resolution(conn, concert, proposal)
        self.assertEqual(accepted["city_id"], 7)
        proposal["country_code"] = "SK"
        self.assertIsNone(analyzer.validate_location_resolution(conn, concert, proposal))

    def test_new_city_requires_stable_external_identity(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        proposal = {
            "status": "new_city", "existing_city_id": None,
            "english_name": "Hukvaldy", "local_name": "Hukvaldy", "country_code": "CZ",
            "external_source": "geonames", "external_id": None,
            "raw_value_type": "extraction_artifact",
            "source_url": "https://example.test", "evidence": "The venue is in Hukvaldy.",
        }
        self.assertIsNone(analyzer.validate_location_resolution(MagicMock(), concert, proposal))

    def test_page_unavailable_cannot_resolve_location(self):
        concert = analyzer.Concert(1, "Test", date.today(), "https://example.test", None)
        proposal = {
            "status": "country_only", "country_code": "CZ",
            "source_url": "https://example.test", "evidence": "Stored fallback.",
        }
        self.assertIsNone(
            analyzer.validate_location_resolution(
                MagicMock(), concert, proposal, page_available=False
            )
        )

    def test_persists_existing_city_alias_and_audit(self):
        cursor = MagicMock()
        concert = analyzer.Concert(
            1, "Test", date.today(), "https://example.test", None,
            city_raw="Praha", country_code_raw="SK", source="Test source",
        )
        resolution = {
            "status": "existing_city", "city_id": 7, "country_code": "CZ",
            "raw_value_type": "legitimate_name", "source_url": concert.url,
            "evidence": "The page identifies Prague.",
        }
        analyzer.apply_location_resolution(cursor, concert, resolution, "test-model")
        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("INSERT INTO city_alias" in query for query in queries))
        self.assertTrue(any("country_code_resolved" in query for query in queries))
        self.assertEqual(
            sum("INSERT INTO classical_concert_change" in query for query in queries),
            2,
        )

    def test_skips_alias_for_empty_city_sentinel(self):
        cursor = MagicMock()
        concert = analyzer.Concert(
            1, "Test", date.today(), "https://example.test", None,
            city_raw="NaN", country_code_raw="CZ", source="Test source",
        )
        resolution = {
            "status": "existing_city", "city_id": 7, "country_code": "IT",
            "raw_value_type": "extraction_artifact", "source_url": concert.url,
            "evidence": "The page identifies Sterzing.",
        }

        analyzer.apply_location_resolution(cursor, concert, resolution, "test-model")

        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(any("INSERT INTO city_alias" in query for query in queries))
        self.assertTrue(any("UPDATE classical_concert" in query for query in queries))

    def test_rejects_date_update_that_would_duplicate_a_concert(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)
        concert = analyzer.Concert(
            1,
            "Test",
            date(2026, 8, 1),
            "https://example.test",
            None,
        )

        accepted = analyzer.validate_event_updates(
            conn,
            concert,
            [
                {
                    "field": "date",
                    "new_value": "2026-08-02",
                    "source_url": concert.url,
                    "evidence": "The event page gives 2 August.",
                }
            ],
        )

        self.assertEqual(accepted, [])

    def test_persists_event_update_with_audit_and_verification_timestamp(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        concert = analyzer.Concert(
            1,
            "Test",
            date(2026, 8, 1),
            "https://example.test",
            None,
        )
        result = {
            "status": "no_program",
            "source_url": concert.url,
            "notes": "No programme published.",
            "composers": [],
            "program": [],
            "unresolved_program": [],
            "event_updates": [],
        }
        event_updates = [
            {
                "field": "event_status",
                "db_value": "cancelled",
                "new_value": "cancelled",
                "old_value": "scheduled",
                "source_url": concert.url,
                "evidence": "The organizer marks the event as cancelled.",
            }
        ]

        analyzer.persist_result(
            conn,
            concert,
            result,
            "gpt-5.6-terra",
            event_updates,
        )

        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("last_verified_at = now()" in query for query in queries))
        status_update = next(query for query in queries if "event_status = %s" in query)
        self.assertIn("event_status_updated_at = now()", status_update)
        self.assertTrue(any("INSERT INTO classical_concert_change" in query for query in queries))
        conn.commit.assert_called_once()

    def test_page_unavailable_does_not_mark_concert_verified(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        concert = analyzer.Concert(
            1,
            "Test",
            date(2026, 8, 1),
            "https://example.test",
            None,
        )
        result = {
            "status": "page_unavailable",
            "source_url": concert.url,
            "notes": "The page could not be opened.",
            "composers": [],
            "program": [],
            "unresolved_program": [],
            "event_updates": [],
        }

        analyzer.persist_result(conn, concert, result, "gpt-5.6-terra")

        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(any("last_verified_at = now()" in query for query in queries))

    def test_invalid_event_update_does_not_block_programme_result(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        concert = analyzer.Concert(
            1,
            "Test",
            date(2026, 8, 1),
            "https://example.test",
            None,
        )
        result = {
            "status": "no_program",
            "source_url": concert.url,
            "notes": "No programme published.",
            "composers": [],
            "program": [],
            "unresolved_program": [],
            "event_updates": [
                {
                    "field": "time_from",
                    "new_value": "eight o'clock",
                    "source_url": concert.url,
                    "evidence": "Unparseable time.",
                }
            ],
        }

        analyzer.persist_result(conn, concert, result, "gpt-5.6-terra")

        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("INSERT INTO concert_program_analysis" in query for query in queries))
        self.assertFalse(any("INSERT INTO classical_concert_change" in query for query in queries))
        conn.commit.assert_called_once()

    def test_degraded_programme_retry_still_applies_event_update(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("partial",)
        concert = analyzer.Concert(
            1,
            "Test",
            date(2026, 8, 1),
            "https://example.test",
            None,
        )
        result = {
            "status": "no_program",
            "source_url": concert.url,
            "notes": "Programme was removed from the page.",
            "composers": [],
            "program": [],
            "unresolved_program": [],
            "event_updates": [],
        }
        event_updates = [
            {
                "field": "event_status",
                "db_value": "cancelled",
                "new_value": "cancelled",
                "old_value": "scheduled",
                "source_url": concert.url,
                "evidence": "The event is cancelled.",
            }
        ]

        analyzer.persist_result(
            conn,
            concert,
            result,
            "gpt-5.6-terra",
            event_updates,
        )

        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("INSERT INTO classical_concert_change" in query for query in queries))
        self.assertTrue(any("UPDATE concert_program_analysis" in query for query in queries))
        self.assertFalse(any("DELETE FROM classical_concert_work" in query for query in queries))

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
