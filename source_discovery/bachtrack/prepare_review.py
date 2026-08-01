from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit


TICKETING_REGISTERED_DOMAINS = {
    "atgtickets.com",
    "bilety24.pl",
    "billetweb.fr",
    "churchsuite.com",
    "eventbrite.co.uk",
    "eventbrite.com",
    "eventbrite.fr",
    "eventim-light.com",
    "salesforce-sites.com",
    "secutix.com",
    "sympla.com.br",
    "ticketline.pt",
    "ticketsource.co.uk",
    "ticketsource.com",
    "tickettailor.com",
    "tix.se",
    "urbtix.hk",
    "vivaticket.com",
}

OUTPUT_FIELDS = [
    "candidate_id",
    "url",
    "registered_domain",
    "listing_count",
    "customer_names",
    "categories",
    "statuses",
    "example_target_url",
    "example_bachtrack_event_url",
    "ticket_platform_hint",
    "deterministic_status",
    "deterministic_note",
]


def hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def normalize_source_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Source URL must be an HTTP(S) URL: {url!r}")
    host = parsed.hostname.lower().encode("idna").decode("ascii")
    if host.startswith("www."):
        host = host[4:]
    port = parsed.port
    netloc = host if not port or port in {80, 443} else f"{host}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def host_without_www(url: str) -> str:
    host = hostname(url)
    return host[4:] if host.startswith("www.") else host


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def existing_normalized_urls(seed_dir: Path) -> set[str]:
    urls: set[str] = set()
    for path in sorted(seed_dir.glob("*.csv")):
        for row in read_rows(path):
            for key in ("url", "canonical_url"):
                value = (row.get(key) or "").strip()
                if value:
                    urls.add(normalize_source_url(value))
    return urls


def prepare_rows(source_rows: list[dict[str, str]], existing_urls: set[str]) -> list[dict[str, str]]:
    by_host: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in source_rows:
        by_host[host_without_www(row["url"])].append(row)

    prepared: list[dict[str, str]] = []
    candidate_number = 0
    for host, matches in sorted(by_host.items()):
        matches.sort(key=lambda row: (-int(row["listing_count"]), row["url"]))
        primary = matches[0]
        normalized = normalize_source_url(primary["url"])
        status = "needs_review"
        note = ""
        if host == "bachtrack.com":
            status = "internal"
            note = "Bachtrack redirect did not reach an external website"
        elif normalized in existing_urls:
            status = "existing_seed"
            note = "URL is already present in an existing crawler-source seed"
        elif len(matches) > 1:
            aliases = ", ".join(row["url"] for row in matches[1:])
            note = f"Merged hostname aliases: {aliases}"

        if status == "needs_review":
            candidate_number += 1
            candidate_id = f"BT{candidate_number:04d}"
        else:
            candidate_id = ""

        prepared.append(
            {
                "candidate_id": candidate_id,
                "url": primary["url"],
                "registered_domain": primary["registered_domain"],
                "listing_count": str(sum(int(row["listing_count"]) for row in matches)),
                "customer_names": " | ".join(
                    sorted(
                        {
                            name.strip()
                            for row in matches
                            for name in row["customer_names"].split("|")
                            if name.strip()
                        }
                    )
                ),
                "categories": " | ".join(
                    sorted(
                        {
                            category.strip()
                            for row in matches
                            for category in row["categories"].split("|")
                            if category.strip()
                        }
                    )
                ),
                "statuses": " | ".join(
                    sorted(
                        {
                            value.strip()
                            for row in matches
                            for value in row["statuses"].split("|")
                            if value.strip()
                        }
                    )
                ),
                "example_target_url": primary["example_target_url"],
                "example_bachtrack_event_url": primary["example_bachtrack_event_url"],
                "ticket_platform_hint": (
                    "yes" if primary["registered_domain"] in TICKETING_REGISTERED_DOMAINS else "no"
                ),
                "deterministic_status": status,
                "deterministic_note": note,
            }
        )
    return prepared


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_batches(directory: Path, rows: list[dict[str, str]], batch_count: int) -> None:
    if batch_count < 1:
        raise ValueError("batch_count must be positive")
    review_rows = [row for row in rows if row["deterministic_status"] == "needs_review"]
    for index in range(batch_count):
        write_rows(directory / f"batch_{index + 1}.csv", review_rows[index::batch_count])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare Bachtrack websites for human or agent review.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/bachtrack_source_urls.csv"),
    )
    parser.add_argument(
        "--seed-dir",
        type=Path,
        default=Path("seeds/crawler_sources"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/bachtrack_seed_review.csv"),
    )
    parser.add_argument(
        "--batches-dir",
        type=Path,
        default=Path("data/bachtrack_review_batches"),
    )
    parser.add_argument("--batch-count", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = prepare_rows(read_rows(args.input), existing_normalized_urls(args.seed_dir))
    write_rows(args.output, rows)
    write_batches(args.batches_dir, rows, args.batch_count)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["deterministic_status"]] += 1
    print(f"Wrote {len(rows)} normalized websites to {args.output}")
    print(f"Wrote {args.batch_count} review batches to {args.batches_dir}")
    print(", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


if __name__ == "__main__":
    main()
