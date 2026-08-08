from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

import requests


API_URL = "https://musicbrainz.org/ws/2/artist/"
DEFAULT_OUTPUT = Path("data/musicbrainz_classical_artists.csv")
DEFAULT_URL_OUTPUT = Path("data/musicbrainz_classical_artists_with_urls.csv")
DEFAULT_USER_AGENT = (
    "classical-bot-source-discovery/1.0 "
    "(https://github.com/druskacik/classical_bot)"
)
PAGE_SIZE = 100
FIELDNAMES = [
    "musicbrainz_id",
    "name",
    "sort_name",
    "type",
    "gender",
    "country",
    "area",
    "area_id",
    "begin_area",
    "end_area",
    "begin_date",
    "end_date",
    "ended",
    "disambiguation",
    "search_score",
    "classical_tag_score",
    "tags_json",
]
URL_FIELDNAMES = [
    "official_homepages",
    "url_relations_json",
]


def fetch_page(
    session: requests.Session,
    offset: int,
    *,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    params = {
        "query": "tag:classical",
        "limit": PAGE_SIZE,
        "offset": offset,
        "fmt": "json",
    }
    for attempt in range(retries + 1):
        try:
            response = session.get(API_URL, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == retries:
                raise
            time.sleep(2**attempt)

    raise AssertionError("unreachable")


def fetch_artist_urls(
    session: requests.Session,
    musicbrainz_id: str,
    *,
    timeout: float,
    retries: int,
) -> list[dict[str, Any]]:
    params = {"inc": "url-rels", "fmt": "json"}
    for attempt in range(retries + 1):
        try:
            response = session.get(
                f"{API_URL}{musicbrainz_id}",
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json().get("relations") or []
        except (requests.RequestException, ValueError):
            if attempt == retries:
                raise
            time.sleep(2**attempt)

    raise AssertionError("unreachable")


def url_fields(relations: list[dict[str, Any]]) -> dict[str, str]:
    normalized = []
    official_homepages = []
    for relation in relations:
        resource = (relation.get("url") or {}).get("resource")
        if not resource:
            continue
        relation_type = relation.get("type") or ""
        normalized.append(
            {
                "url": resource,
                "type": relation_type,
                "type_id": relation.get("type-id") or "",
                "begin": relation.get("begin") or "",
                "end": relation.get("end") or "",
                "ended": relation.get("ended") or False,
            }
        )
        if relation_type == "official homepage" and not relation.get("ended"):
            official_homepages.append(resource)

    return {
        "official_homepages": "|".join(official_homepages),
        "url_relations_json": json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def tag_score(tags: list[dict[str, Any]], wanted_tag: str) -> int | None:
    for tag in tags:
        if tag.get("name", "").casefold() == wanted_tag.casefold():
            count = tag.get("count")
            return int(count) if count is not None else None
    return None


def nested_name(artist: dict[str, Any], field: str) -> str:
    value = artist.get(field) or {}
    return str(value.get("name") or "")


def artist_to_row(artist: dict[str, Any]) -> dict[str, Any]:
    tags = artist.get("tags") or []
    life_span = artist.get("life-span") or {}
    area = artist.get("area") or {}
    return {
        "musicbrainz_id": artist.get("id", ""),
        "name": artist.get("name", ""),
        "sort_name": artist.get("sort-name", ""),
        "type": artist.get("type", ""),
        "gender": artist.get("gender", ""),
        "country": artist.get("country", ""),
        "area": area.get("name", ""),
        "area_id": area.get("id", ""),
        "begin_area": nested_name(artist, "begin-area"),
        "end_area": nested_name(artist, "end-area"),
        "begin_date": life_span.get("begin", ""),
        "end_date": life_span.get("end", ""),
        "ended": life_span.get("ended", ""),
        "disambiguation": artist.get("disambiguation", ""),
        "search_score": artist.get("score", ""),
        "classical_tag_score": tag_score(tags, "classical"),
        "tags_json": json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
    }


def download(
    output: Path,
    *,
    delay: float,
    timeout: float,
    retries: int,
    max_pages: int | None,
    user_agent: str,
    quiet: bool,
) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})

    written = 0
    offset = 0
    page_number = 0
    total = None
    with output.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writeheader()

        while total is None or offset < total:
            payload = fetch_page(session, offset, timeout=timeout, retries=retries)
            artists = payload.get("artists") or []
            total = int(payload.get("count") or 0)
            page_number += 1

            for artist in artists:
                writer.writerow(artist_to_row(artist))
            csv_file.flush()

            written += len(artists)
            offset += len(artists)
            if not quiet:
                print(f"Page {page_number}: wrote {written}/{total} artists", flush=True)

            if not artists or (max_pages is not None and page_number >= max_pages):
                break
            if delay:
                time.sleep(delay)

    return written


def enrich_urls(
    input_path: Path,
    output: Path,
    *,
    delay: float,
    timeout: float,
    retries: int,
    max_artists: int | None,
    user_agent: str,
    quiet: bool,
) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})

    with input_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames or "musicbrainz_id" not in reader.fieldnames:
            raise ValueError(f"{input_path} has no musicbrainz_id column")
        fieldnames = list(reader.fieldnames)
        for fieldname in URL_FIELDNAMES:
            if fieldname not in fieldnames:
                fieldnames.append(fieldname)

        with output.open("w", encoding="utf-8", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fieldnames)
            writer.writeheader()
            written = 0
            for row in reader:
                if max_artists is not None and written >= max_artists:
                    break
                if written and delay:
                    time.sleep(delay)
                relations = fetch_artist_urls(
                    session,
                    row["musicbrainz_id"],
                    timeout=timeout,
                    retries=retries,
                )
                row.update(url_fields(relations))
                writer.writerow(row)
                output_file.flush()
                written += 1
                if not quiet:
                    print(f"Enriched {written} artists", flush=True)

    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download all MusicBrainz artists matched by the classical tag search. "
            "Tag scores are retained so false positives can be filtered later."
        )
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--enrich-urls-from",
        type=Path,
        metavar="CSV",
        help=(
            "Instead of downloading the search results, enrich an existing artist CSV "
            "with MusicBrainz URL relationships."
        ),
    )
    parser.add_argument(
        "--url-output",
        type=Path,
        default=DEFAULT_URL_OUTPUT,
        help="Output used with --enrich-urls-from.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.1,
        help="Seconds between API requests. Defaults to 1.1 to respect MusicBrainz limits.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Stop after this many 100-row pages; useful for testing.",
    )
    parser.add_argument(
        "--max-artists",
        type=int,
        help="Limit URL enrichment to this many artists; useful for testing.",
    )
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.delay < 0 or args.timeout <= 0 or args.retries < 0:
        raise SystemExit("--delay and --retries must be non-negative; --timeout must be positive")
    if args.max_pages is not None and args.max_pages <= 0:
        raise SystemExit("--max-pages must be positive")
    if args.max_artists is not None and args.max_artists <= 0:
        raise SystemExit("--max-artists must be positive")

    if args.enrich_urls_from:
        written = enrich_urls(
            args.enrich_urls_from,
            args.url_output,
            delay=args.delay,
            timeout=args.timeout,
            retries=args.retries,
            max_artists=args.max_artists,
            user_agent=args.user_agent,
            quiet=args.quiet,
        )
        print(f"Enriched {written} MusicBrainz artists to {args.url_output}")
        return

    written = download(
        args.output,
        delay=args.delay,
        timeout=args.timeout,
        retries=args.retries,
        max_pages=args.max_pages,
        user_agent=args.user_agent,
        quiet=args.quiet,
    )
    print(f"Wrote {written} MusicBrainz artists to {args.output}")


if __name__ == "__main__":
    main()
