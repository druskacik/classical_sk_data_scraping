#!/usr/bin/env python3
"""Read-only composer/work lookup commands for the concert programme agent."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

import dotenv
import jellyfish

if __package__:
    from agent_utils.search_db import get_connection
else:
    from search_db import get_connection


dotenv.load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def find_composers(name: str, limit: int = 10) -> list[dict]:
    target = normalize(name)
    if not target:
        return []
    with get_connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.id, c.name, c.normalized_name, a.alias, a.normalized_alias
            FROM composer c
            LEFT JOIN composer_alias a ON a.composer_id = c.id
            """
        )
        candidates: dict[int, dict] = {}
        for composer_id, canonical_name, normalized_name, alias, normalized_alias in cursor.fetchall():
            searchable_names = [normalized_name or canonical_name]
            if alias:
                searchable_names.append(normalized_alias or alias)
            score = 0.0
            for candidate_name in searchable_names:
                candidate = normalize(candidate_name)
                candidate_score = jellyfish.jaro_winkler_similarity(target, candidate)
                if target == candidate:
                    candidate_score = 1.0
                elif target in candidate or candidate in target:
                    candidate_score = max(candidate_score, 0.95)
                score = max(score, candidate_score)
            if score >= 0.55 and score > candidates.get(composer_id, {}).get("score", -1):
                candidates[composer_id] = {
                    "id": composer_id,
                    "name": canonical_name,
                    "score": round(score, 4),
                }
    return sorted(candidates.values(), key=lambda item: (-item["score"], item["name"]))[:limit]


def list_works(composer_id: int) -> list[dict]:
    with get_connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, title, catalogue_number FROM work WHERE composer_id = %s ORDER BY title, id",
            (composer_id,),
        )
        return [
            {"id": row[0], "title": row[1], "catalogue_number": row[2]}
            for row in cursor.fetchall()
        ]


def find_works(composer_id: int, query: str, limit: int = 15) -> list[dict]:
    target = normalize(query)
    candidates = []
    for work in list_works(composer_id):
        searchable = " ".join(filter(None, [work["title"], work["catalogue_number"]]))
        score = jellyfish.jaro_winkler_similarity(target, normalize(searchable))
        if target in normalize(searchable) or normalize(searchable) in target:
            score = max(score, 0.95)
        if score >= 0.45:
            candidates.append({**work, "score": round(score, 4)})
    return sorted(candidates, key=lambda item: (-item["score"], item["title"]))[:limit]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    composer = subparsers.add_parser("find-composer")
    composer.add_argument("--name", required=True)
    composer.add_argument("--limit", type=int, default=10)
    works = subparsers.add_parser("find-works")
    works.add_argument("--composer-id", type=int, required=True)
    works.add_argument("--query", required=True)
    works.add_argument("--limit", type=int, default=15)
    all_works = subparsers.add_parser("list-works")
    all_works.add_argument("--composer-id", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "find-composer":
        result = find_composers(args.name, args.limit)
    elif args.command == "find-works":
        result = find_works(args.composer_id, args.query, args.limit)
    else:
        result = list_works(args.composer_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
