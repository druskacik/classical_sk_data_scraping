#!/usr/bin/env python3
"""
Run a read-only SQL query against the project Postgres database and print results.
Uses DB_NAME, DB_USER, DB_PASS, DB_HOST, DB_PORT from environment (.env).
"""
import argparse
import csv
import os
import sys
from pathlib import Path

# Load .env from project root so script works from any cwd
import dotenv
dotenv.load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import psycopg2
from psycopg2 import sql


def get_connection():
    """Build a read-only Postgres connection from env."""
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME", "classical_sk"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", ""),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
    )
    # Enforce read-only: no INSERT/UPDATE/DELETE/DDL
    conn.set_session(readonly=True)
    return conn


def main():
    parser = argparse.ArgumentParser(
        description="Run a read-only SQL query and print results (CSV to stdout)."
    )
    parser.add_argument(
        "--query",
        "-q",
        required=True,
        help="SQL query to run (SELECT only; connection is read-only).",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["csv", "table"],
        default="csv",
        help="Output format: csv (default) or table.",
    )
    args = parser.parse_args()

    try:
        conn = get_connection()
    except Exception as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            # Use sql.SQL().format(sql.Identifier(...)) only for identifiers;
            # the query itself we pass as a string (user-provided). Read-only
            # session already prevents writes; we do not use format for the query.
            cur.execute(args.query)
            rows = cur.fetchall()
            colnames = [d[0] for d in cur.description] if cur.description else []
    except Exception as e:
        print(f"Query error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    if args.format == "csv":
        out = csv.writer(sys.stdout)
        if colnames:
            out.writerow(colnames)
        out.writerows(rows)
    else:
        # Simple table: column names then rows
        if colnames:
            print("\t".join(colnames))
            print("\t".join("-" * len(c) for c in colnames))
        for row in rows:
            print("\t".join(str(v) if v is not None else "" for v in row))


if __name__ == "__main__":
    main()
