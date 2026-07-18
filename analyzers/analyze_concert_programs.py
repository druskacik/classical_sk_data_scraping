from __future__ import annotations

import argparse
import json
import os
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json
import pystache
from dotenv import load_dotenv
from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from agent_utils.concert_catalog import normalize


load_dotenv()

DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_LIMIT = 25
DEFAULT_TIMEOUT_SECONDS = 600
MAX_AUTOMATIC_ATTEMPTS = 3
ADVISORY_LOCK_NAME = "classical-sk-concert-program-analysis"
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "analyze_concert_program.mustache"

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {
            "type": "string",
            "enum": ["complete", "ambiguous", "no_program", "page_unavailable"],
        },
        "source_url": {"type": "string"},
        "notes": {"type": "string"},
        "program": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "composer": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "existing_id": {"type": ["integer", "null"]},
                            "name": {"type": "string"},
                        },
                        "required": ["existing_id", "name"],
                    },
                    "work": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "existing_id": {"type": ["integer", "null"]},
                            "title": {"type": "string"},
                            "catalogue_number": {"type": ["string", "null"]},
                        },
                        "required": ["existing_id", "title", "catalogue_number"],
                    },
                    "programme_label": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["composer", "work", "programme_label", "evidence"],
            },
        },
    },
    "required": ["status", "source_url", "notes", "program"],
}


@dataclass(frozen=True)
class Concert:
    id: int
    title: str
    date: date
    url: str
    description: str | None


def get_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )


def render_prompt(concert: Concert) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return pystache.render(
        template,
        {
            "id": concert.id,
            "title": concert.title,
            "date": concert.date.isoformat(),
            "url": concert.url,
            "description": concert.description or "(not available)",
        },
    )


def select_concerts(conn, concert_ids: list[int] | None, limit: int, force: bool) -> list[Concert]:
    with conn.cursor() as cursor:
        if concert_ids:
            cursor.execute(
                """
                SELECT c.id, c.title, c.date, c.url, c.description
                FROM classical_concert c
                LEFT JOIN concert_program_analysis a ON a.classical_concert_id = c.id
                WHERE c.id = ANY(%s)
                  AND (%s OR a.status IS NULL OR a.status NOT IN ('complete', 'ambiguous', 'expired_no_program', 'failed'))
                ORDER BY c.id
                LIMIT %s
                """,
                (concert_ids, force, limit),
            )
        else:
            cursor.execute(
                """
                SELECT c.id, c.title, c.date, c.url, c.description
                FROM classical_concert c
                LEFT JOIN concert_program_analysis a ON a.classical_concert_id = c.id
                WHERE c.program_analysis_eligible = true
                  AND c.date >= CURRENT_DATE
                  AND (
                    a.id IS NULL
                    OR (a.status = 'no_program' AND c.date >= CURRENT_DATE)
                    OR (a.status IN ('page_unavailable', 'error') AND a.attempts < %s)
                  )
                ORDER BY c.date, c.id
                LIMIT %s
                """,
                (MAX_AUTOMATIC_ATTEMPTS, limit),
            )
        return [Concert(*row) for row in cursor.fetchall()]


def expire_old_no_program(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE concert_program_analysis a
            SET status = 'expired_no_program', completed_at = now()
            FROM classical_concert c
            WHERE a.classical_concert_id = c.id
              AND a.status = 'no_program'
              AND c.date < CURRENT_DATE
            """
        )
    conn.commit()


def validate_model(codex: Codex, model: str) -> None:
    try:
        available = {item.model for item in codex.models().data}
    except Exception as sdk_error:
        cache_path = Path(os.getenv("CODEX_HOME", Path.home() / ".codex")) / "models_cache.json"
        try:
            catalogue = json.loads(cache_path.read_text(encoding="utf-8"))
            available = {
                item.get("slug") or item.get("model")
                for item in catalogue.get("models", catalogue.get("data", []))
            }
            available.discard(None)
        except Exception as cache_error:
            raise RuntimeError(
                f"Could not validate Codex model {model!r}: SDK catalogue failed ({sdk_error}) "
                f"and {cache_path} could not be read ({cache_error})"
            ) from sdk_error
    if model not in available:
        raise RuntimeError(f"Codex model {model!r} is unavailable. Available models: {', '.join(sorted(available))}")


def run_agent(codex: Codex, concert: Concert, model: str, timeout_seconds: int) -> dict[str, Any]:
    thread = codex.thread_start(
        approval_mode=ApprovalMode.deny_all,
        cwd=str(Path.cwd()),
        ephemeral=False,
        model=model,
        sandbox=Sandbox.full_access,
    )
    turn = thread.turn(
        render_prompt(concert),
        approval_mode=ApprovalMode.deny_all,
        cwd=str(Path.cwd()),
        model=model,
        output_schema=OUTPUT_SCHEMA,
        sandbox=Sandbox.full_access,
    )
    timer = threading.Timer(timeout_seconds, turn.interrupt)
    timer.daemon = True
    timer.start()
    try:
        result = turn.run()
    finally:
        timer.cancel()
    if result.error:
        raise RuntimeError(str(result.error))
    if not result.final_response:
        raise RuntimeError("Codex returned no final response")
    return json.loads(result.final_response)


def validate_result(conn, concert: Concert, result: dict[str, Any]) -> None:
    status = result["status"]
    program = result["program"]
    if status == "complete" and not program:
        raise ValueError("A complete result must contain at least one composer/work pair")
    if status in {"no_program", "page_unavailable"} and program:
        raise ValueError(f"A {status} result must not contain catalogue entries")
    if status in {"ambiguous", "no_program", "page_unavailable"}:
        return
    with conn.cursor() as cursor:
        for entry in program:
            composer = entry["composer"]
            work = entry["work"]
            if not composer["name"].strip() or not work["title"].strip():
                raise ValueError("Composer names and work titles must not be empty")
            if not entry["programme_label"].strip():
                raise ValueError("Programme labels must not be empty")
            if composer["existing_id"] is not None:
                cursor.execute("SELECT 1 FROM composer WHERE id = %s", (composer["existing_id"],))
                if cursor.fetchone() is None:
                    raise ValueError(f"Unknown composer ID {composer['existing_id']}")
            if work["existing_id"] is not None:
                cursor.execute("SELECT composer_id FROM work WHERE id = %s", (work["existing_id"],))
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(f"Unknown work ID {work['existing_id']}")
                if composer["existing_id"] is None or row[0] != composer["existing_id"]:
                    raise ValueError("Existing work does not belong to the selected existing composer")
    if result["source_url"].strip() == "":
        raise ValueError("source_url must not be empty")


def _resolve_composer(cursor, composer: dict[str, Any]) -> int:
    if composer["existing_id"] is not None:
        return composer["existing_id"]
    normalized_name = normalize(composer["name"])
    cursor.execute(
        """
        INSERT INTO composer (name, normalized_name)
        VALUES (%s, %s)
        ON CONFLICT (normalized_name) DO UPDATE SET normalized_name = EXCLUDED.normalized_name
        RETURNING id
        """,
        (composer["name"].strip(), normalized_name),
    )
    return cursor.fetchone()[0]


def _resolve_work(cursor, composer_id: int, work: dict[str, Any]) -> int:
    if work["existing_id"] is not None:
        return work["existing_id"]
    normalized_title = normalize(work["title"])
    cursor.execute(
        """
        INSERT INTO work (composer_id, title, normalized_title, catalogue_number)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (composer_id, normalized_title)
        DO UPDATE SET catalogue_number = COALESCE(work.catalogue_number, EXCLUDED.catalogue_number)
        RETURNING id
        """,
        (composer_id, work["title"].strip(), normalized_title, work["catalogue_number"]),
    )
    return cursor.fetchone()[0]


def persist_result(conn, concert: Concert, result: dict[str, Any], model: str) -> None:
    status = result["status"]
    completed = status in {"complete", "ambiguous", "expired_no_program", "failed"}
    try:
        with conn.cursor() as cursor:
            if status == "complete":
                cursor.execute(
                    "DELETE FROM classical_concert_work WHERE classical_concert_id = %s",
                    (concert.id,),
                )
                cursor.execute(
                    "DELETE FROM classical_concert_composer WHERE classical_concert_id = %s",
                    (concert.id,),
                )
                for entry in result["program"]:
                    composer_id = _resolve_composer(cursor, entry["composer"])
                    work_id = _resolve_work(cursor, composer_id, entry["work"])
                    cursor.execute(
                        """
                        INSERT INTO classical_concert_composer (classical_concert_id, composer_id)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                        """,
                        (concert.id, composer_id),
                    )
                    cursor.execute(
                        """
                        INSERT INTO classical_concert_work
                            (classical_concert_id, work_id, programme_label, source_url, evidence)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (classical_concert_id, work_id) DO UPDATE SET
                            programme_label = EXCLUDED.programme_label,
                            source_url = EXCLUDED.source_url,
                            evidence = EXCLUDED.evidence
                        """,
                        (
                            concert.id,
                            work_id,
                            entry["programme_label"].strip(),
                            result["source_url"].strip(),
                            entry["evidence"].strip(),
                        ),
                    )
            cursor.execute(
                """
                INSERT INTO concert_program_analysis
                    (classical_concert_id, status, attempts, model, raw_result, last_error, last_attempted_at, completed_at)
                VALUES (%s, %s, 1, %s, %s, NULL, now(), CASE WHEN %s THEN now() ELSE NULL END)
                ON CONFLICT (classical_concert_id) DO UPDATE SET
                    status = CASE
                        WHEN EXCLUDED.status = 'page_unavailable'
                         AND concert_program_analysis.attempts + 1 >= %s THEN 'failed'
                        ELSE EXCLUDED.status
                    END,
                    attempts = concert_program_analysis.attempts + 1,
                    model = EXCLUDED.model,
                    raw_result = EXCLUDED.raw_result,
                    last_error = NULL,
                    last_attempted_at = now(),
                    completed_at = CASE
                        WHEN EXCLUDED.status = 'page_unavailable'
                         AND concert_program_analysis.attempts + 1 >= %s THEN now()
                        ELSE EXCLUDED.completed_at
                    END
                """,
                (
                    concert.id,
                    status,
                    model,
                    Json(result, dumps=lambda value: json.dumps(value, ensure_ascii=False)),
                    completed,
                    MAX_AUTOMATIC_ATTEMPTS,
                    MAX_AUTOMATIC_ATTEMPTS,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def persist_error(conn, concert: Concert, model: str, error: Exception) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO concert_program_analysis
                (classical_concert_id, status, attempts, model, last_error, last_attempted_at)
            VALUES (%s, 'error', 1, %s, %s, now())
            ON CONFLICT (classical_concert_id) DO UPDATE SET
                status = CASE WHEN concert_program_analysis.attempts + 1 >= %s THEN 'failed' ELSE 'error' END,
                attempts = concert_program_analysis.attempts + 1,
                model = EXCLUDED.model,
                last_error = EXCLUDED.last_error,
                last_attempted_at = now(),
                completed_at = CASE WHEN concert_program_analysis.attempts + 1 >= %s THEN now() ELSE NULL END
            """,
            (concert.id, model, str(error), MAX_AUTOMATIC_ATTEMPTS, MAX_AUTOMATIC_ATTEMPTS),
        )
    conn.commit()


def acquire_lock(conn) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (ADVISORY_LOCK_NAME,))
        return cursor.fetchone()[0]


def release_lock(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (ADVISORY_LOCK_NAME,))


def run(
    *,
    concert_ids: list[int] | None = None,
    limit: int = DEFAULT_LIMIT,
    model: str = DEFAULT_MODEL,
    commit: bool = False,
    force: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> int:
    conn = get_connection()
    locked = False
    failures = 0
    try:
        if commit:
            locked = acquire_lock(conn)
            if not locked:
                raise RuntimeError("Another committed concert programme analysis is already running")
            expire_old_no_program(conn)
        concerts = select_concerts(conn, concert_ids, limit, force)
        if not concerts:
            print("No concerts eligible for programme analysis.")
            return 0
        with Codex(
            CodexConfig(codex_bin=os.getenv("CODEX_BIN"), cwd=str(Path.cwd()))
        ) as codex:
            validate_model(codex, model)
            for concert in concerts:
                print(f"Analyzing concert {concert.id}: {concert.title}")
                try:
                    result = run_agent(codex, concert, model, timeout_seconds)
                    validate_result(conn, concert, result)
                    print(json.dumps({"concert_id": concert.id, **result}, ensure_ascii=False, indent=2))
                    if commit:
                        persist_result(conn, concert, result, model)
                    else:
                        print("DRY RUN: no database changes made")
                except Exception as error:
                    failures += 1
                    print(f"Concert {concert.id} failed: {error}")
                    if commit:
                        persist_error(conn, concert, model, error)
        return failures
    finally:
        if locked:
            release_lock(conn)
        conn.close()


def scheduled_main() -> None:
    failures = run(commit=True)
    if failures:
        raise RuntimeError(f"{failures} concert programme analyses failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract composers and works with Codex.")
    parser.add_argument("--concert-id", type=int, action="append", dest="concert_ids")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--model", default=os.getenv("CONCERT_PROGRAM_CODEX_MODEL", DEFAULT_MODEL))
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    failures = run(
        concert_ids=args.concert_ids,
        limit=args.limit,
        model=args.model,
        commit=args.commit,
        force=args.force,
        timeout_seconds=args.timeout_seconds,
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
