from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit


REVIEW_FIELDS = [
    "candidate_id",
    "input_url",
    "decision",
    "canonical_url",
    "country_code",
    "classification",
    "confidence",
    "evidence_url",
    "notes",
]
SEED_FIELDS = ["url", "country_code", "canonical_url", "crawler_path", "priority", "notes"]
VALID_DECISIONS = {"include", "exclude", "review"}
VALID_CLASSIFICATIONS = {
    "concert_organization",
    "venue_or_presenter",
    "broad_event_source",
    "ticket_platform",
    "unrelated",
    "unreachable",
    "ambiguous",
}
VALID_CONFIDENCE = {"high", "medium", "low"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid HTTP(S) URL: {url!r}")
    host = parsed.hostname.lower().encode("idna").decode("ascii")
    if host.startswith("www."):
        host = host[4:]
    port = parsed.port
    netloc = host if not port or port in {80, 443} else f"{host}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def website_home_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    normalize_url(url)
    host = (parsed.hostname or "").lower().encode("idna").decode("ascii")
    port = parsed.port
    netloc = host if not port or port in {80, 443} else f"{host}:{port}"
    return urlunsplit(("https", netloc, "/", "", ""))


def crawler_slug(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    value = re.sub(r"[^a-z0-9]+", "_", host).strip("_")
    if not value:
        raise ValueError(f"Could not derive crawler slug from {url!r}")
    return value


def validate_review_rows(
    candidates: list[dict[str, str]], reviews: list[dict[str, str]]
) -> None:
    expected = {row["candidate_id"]: row for row in candidates}
    actual: dict[str, dict[str, str]] = {}
    for row in reviews:
        candidate_id = row["candidate_id"]
        if candidate_id in actual:
            raise ValueError(f"Duplicate review candidate_id: {candidate_id}")
        actual[candidate_id] = row
        if candidate_id not in expected:
            raise ValueError(f"Unexpected review candidate_id: {candidate_id}")
        if normalize_url(row["input_url"]) != normalize_url(expected[candidate_id]["url"]):
            raise ValueError(f"Input URL mismatch for {candidate_id}")
        if row["decision"] not in VALID_DECISIONS:
            raise ValueError(f"Invalid decision for {candidate_id}: {row['decision']}")
        if row["classification"] not in VALID_CLASSIFICATIONS:
            raise ValueError(
                f"Invalid classification for {candidate_id}: {row['classification']}"
            )
        if row["confidence"] not in VALID_CONFIDENCE:
            raise ValueError(f"Invalid confidence for {candidate_id}: {row['confidence']}")
        if row["decision"] == "include":
            normalize_url(row["canonical_url"])
            country = row["country_code"]
            if len(country) != 2 or not country.isalpha() or country != country.upper():
                raise ValueError(f"Invalid country code for {candidate_id}: {country!r}")
            if row["classification"] not in {
                "concert_organization",
                "venue_or_presenter",
                "broad_event_source",
            }:
                raise ValueError(f"Included {candidate_id} has unsuitable classification")
    missing = sorted(set(expected) - set(actual))
    if missing:
        raise ValueError(f"Missing {len(missing)} reviews: {', '.join(missing[:10])}")


def existing_urls(seed_dir: Path, *, exclude: set[Path] | None = None) -> set[str]:
    values: set[str] = set()
    excluded = {path.resolve() for path in (exclude or set())}
    for path in sorted(seed_dir.glob("*.csv")):
        if path.resolve() in excluded:
            continue
        for row in read_csv(path):
            for key in ("url", "canonical_url"):
                value = (row.get(key) or "").strip()
                if value:
                    values.add(normalize_url(website_home_url(value)))
    return values


def compile_rows(
    reviews: list[dict[str, str]],
    known_urls: set[str],
    *,
    include_medium_confidence: bool = False,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    seeds: list[dict[str, str]] = []
    remaining: list[dict[str, str]] = []
    claimed_urls = set(known_urls)
    claimed_paths: set[str] = set()
    for row in reviews:
        reason = ""
        if row["decision"] != "include":
            reason = row["decision"]
        elif row["confidence"] != "high" and not (
            include_medium_confidence and row["confidence"] == "medium"
        ):
            reason = f"{row['confidence']}_confidence"
        else:
            home_url = website_home_url(row["canonical_url"])
            normalized = normalize_url(home_url)
            crawler_path = f"crawlers/{row['country_code'].lower()}/{crawler_slug(home_url)}"
            if normalized in claimed_urls:
                reason = "duplicate_or_existing_url"
            elif crawler_path in claimed_paths:
                reason = "crawler_path_collision"
            else:
                claimed_urls.add(normalized)
                claimed_paths.add(crawler_path)
                notes = (
                    f"Discovered via Bachtrack {row['candidate_id']}; "
                    f"classification={row['classification']}; "
                    f"review_confidence={row['confidence']}; evidence={row['evidence_url']}; "
                    f"{row['notes']}"
                ).strip()
                seeds.append(
                    {
                        "url": home_url,
                        "country_code": row["country_code"],
                        "canonical_url": "",
                        "crawler_path": crawler_path,
                        "priority": "0",
                        "notes": notes,
                    }
                )
        if reason:
            remaining.append({**row, "compile_status": reason})
    seeds.sort(key=lambda row: (row["country_code"], row["url"]))
    return seeds, remaining


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile reviewed Bachtrack candidates into a seed.")
    parser.add_argument("--candidates", type=Path, default=Path("data/bachtrack_seed_review.csv"))
    parser.add_argument(
        "--reviews-dir", type=Path, default=Path("data/bachtrack_review_batches")
    )
    parser.add_argument("--seed-dir", type=Path, default=Path("seeds/crawler_sources"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("seeds/crawler_sources/0003_bachtrack_discovered_sources.csv"),
    )
    parser.add_argument(
        "--remaining-output",
        type=Path,
        default=Path("data/bachtrack_seed_review_remaining.csv"),
    )
    parser.add_argument(
        "--include-medium-confidence",
        action="store_true",
        help="Include medium-confidence agent decisions after explicit human approval.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidates = [
        row for row in read_csv(args.candidates) if row["deterministic_status"] == "needs_review"
    ]
    reviews: list[dict[str, str]] = []
    for path in sorted(args.reviews_dir.glob("review_*.csv")):
        rows = read_csv(path)
        reviews.extend(rows)
    validate_review_rows(candidates, reviews)
    seeds, remaining = compile_rows(
        reviews,
        existing_urls(args.seed_dir, exclude={args.output}),
        include_medium_confidence=args.include_medium_confidence,
    )
    write_csv(args.output, SEED_FIELDS, seeds)
    write_csv(args.remaining_output, REVIEW_FIELDS + ["compile_status"], remaining)
    confidence_label = (
        "high- and medium-confidence"
        if args.include_medium_confidence
        else "high-confidence"
    )
    print(f"Wrote {len(seeds)} {confidence_label} sources to {args.output}")
    print(f"Wrote {len(remaining)} non-seeded decisions to {args.remaining_output}")


if __name__ == "__main__":
    main()
