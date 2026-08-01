"""Read-only lookup helpers for the canonical city registry."""

import argparse
import json

from agent_utils.search_db import get_connection
from crawlers.cities import normalize_city_key


def find_cities(name: str) -> list[dict]:
    key = normalize_city_key(name)
    if not key:
        return []
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT c.id, c.english_name, c.local_name, c.country_code,
                       c.external_source, c.external_id, a.alias
                FROM city c JOIN city_alias a ON a.city_id = c.id
                WHERE a.normalized_alias = %s
                   OR a.normalized_alias LIKE '%%' || %s || '%%'
                ORDER BY CASE WHEN a.normalized_alias = %s THEN 0 ELSE 1 END,
                         c.english_name, c.id
                LIMIT 20
                """,
                (key, key, key),
            )
            columns = [item.name for item in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_city(city_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.id, c.english_name, c.local_name, c.country_code,
                       c.external_source, c.external_id, c.source_url,
                       COALESCE(array_agg(a.alias ORDER BY a.alias)
                                FILTER (WHERE a.id IS NOT NULL), '{}') aliases
                FROM city c LEFT JOIN city_alias a ON a.city_id = c.id
                WHERE c.id = %s GROUP BY c.id
                """,
                (city_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [item.name for item in cursor.description]
            return dict(zip(columns, row))
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    find_parser = subparsers.add_parser("find-city")
    find_parser.add_argument("--name", required=True)
    get_parser = subparsers.add_parser("get-city")
    get_parser.add_argument("--id", required=True, type=int)
    args = parser.parse_args()
    result = find_cities(args.name) if args.command == "find-city" else get_city(args.id)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
