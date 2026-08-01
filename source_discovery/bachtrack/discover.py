from __future__ import annotations

import argparse
import csv
import html
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import tldextract
from bs4 import BeautifulSoup


BASEROOT = "https://bachtrack.com"
SEARCH_ENDPOINT = f"{BASEROOT}/json/search/get-results/1200/listing"
USER_AGENT = "classical-bot-source-discovery/1.0"

CATEGORIES = {
    "concerts": "1",
    "opera": "2",
    "dance": "3",
    "kids": "4",
    "masterclasses": "8",
}


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


@dataclass(frozen=True)
class BachtrackSource:
    listing_id: str
    category: str
    customer_id: str
    customer_name: str
    ticket_url: str
    source_url: str
    source_status: str
    source_domain: str
    source_registered_domain: str
    event_url: str
    event_title: str
    venue: str
    city: str


@dataclass(frozen=True)
class DiscoveredWebsite:
    url: str
    domain: str
    registered_domain: str
    listing_count: int
    customer_ids: str
    customer_names: str
    categories: str
    statuses: str
    example_target_url: str
    example_bachtrack_event_url: str


def registered_domain(url: str) -> str:
    parsed = urlparse(url)
    extracted = tldextract.extract(parsed.netloc)
    return ".".join(part for part in [extracted.domain, extracted.suffix] if part)


def website_url(url: str) -> str:
    """Return the website origin for a resolved event or ticket URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), "/", "", "", ""))


def clean_text(value: str | None) -> str:
    return " ".join(html.unescape(value or "").split())


def fetch_results(session: requests.Session, category_id: str, startrow: int) -> dict:
    url = f"{SEARCH_ENDPOINT}/category={category_id};startrow={startrow}"
    response = session.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("result") != "OK":
        raise RuntimeError(f"Bachtrack returned non-OK result for {url}: {payload.get('result')}")
    return payload["data"]


def source_name_from_label(label: str | None) -> str:
    label = clean_text(label)
    if ":" in label:
        return label.split(":", 1)[0].strip()
    if "| id " in label:
        return label.split("| id ", 1)[0].strip()
    return label


def parse_results_html(category: str, text: str) -> list[dict]:
    soup = BeautifulSoup(text, "html.parser")
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    cities_by_listing_id = {
        item.get("data-id"): clean_text(city.get_text(" ", strip=True))
        for item in soup.select("li[data-id]")
        if (city := item.select_one(".listing-ms-city"))
    }

    for anchor in soup.select("a.listing-buy-tickets[href]"):
        listing_id = anchor.get("data-id_listing") or ""
        ticket_href = anchor.get("href") or ""
        if not listing_id or "SearchMobile" in ticket_href:
            continue

        key = (listing_id, ticket_href)
        if key in seen:
            continue
        seen.add(key)

        container = anchor.find_parent(attrs={"data-id": listing_id})
        event_anchor = container.select_one("a.listing-more-info[href]") if container else None
        title_el = container.select_one(".li-shortform-title") if container else None
        venue_el = container.select_one("h2.li-shortform-venue") if container else None

        rows.append(
            {
                "listing_id": listing_id,
                "category": category,
                "customer_id": anchor.get("data-id_customer") or "",
                "customer_name": source_name_from_label(anchor.get("data-label")),
                "ticket_url": urljoin(BASEROOT, ticket_href),
                "event_url": urljoin(BASEROOT, event_anchor["href"]) if event_anchor else "",
                "event_title": clean_text(title_el.get_text(" ", strip=True) if title_el else ""),
                "venue": clean_text(venue_el.get_text(" ", strip=True) if venue_el else ""),
                "city": cities_by_listing_id.get(listing_id, ""),
            }
        )

    return rows


def resolve_ticket_url(session: requests.Session, ticket_url: str) -> tuple[str, str]:
    try:
        response = session.get(ticket_url, timeout=30, allow_redirects=True, stream=True)
        response.close()
        return response.url, str(response.status_code)
    except requests.RequestException as exc:
        return "", exc.__class__.__name__


def discover_sources(
    categories: list[str],
    resolve_targets: bool,
    max_pages: int | None,
    delay: float,
    quiet: bool,
) -> list[BachtrackSource]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    sources: list[BachtrackSource] = []
    started_at = time.monotonic()
    for category in categories:
        category_id = CATEGORIES[category]
        startrow = 0
        pages = 0
        total = 0
        category_sources_before = len(sources)

        if not quiet:
            print(f"Scanning {category} listings...", flush=True)

        while True:
            page_started_at = time.monotonic()
            data = fetch_results(session, category_id, startrow)
            rows = parse_results_html(category, data.get("text", ""))
            total = int(data.get("total") or total)
            count = int(data.get("count") or 0)

            if not quiet:
                page_number = pages + 1
                page_limit = f"/{max_pages}" if max_pages is not None else ""
                print(
                    f"[{category}] page {page_number}{page_limit}: "
                    f"Bachtrack rows {startrow + count}/{total}, "
                    f"ticket links {len(rows)}",
                    flush=True,
                )

            for row in rows:
                if resolve_targets:
                    source_url, source_status = resolve_ticket_url(session, row["ticket_url"])
                else:
                    source_url, source_status = "", ""
                parsed_source = urlparse(source_url)
                sources.append(
                    BachtrackSource(
                        **row,
                        source_url=source_url,
                        source_status=source_status,
                        source_domain=parsed_source.netloc.lower(),
                        source_registered_domain=registered_domain(source_url) if source_url else "",
                    )
                )
                if resolve_targets and delay:
                    time.sleep(delay)

            pages += 1
            startrow += count
            if not quiet:
                category_sources = len(sources) - category_sources_before
                elapsed = time.monotonic() - started_at
                eta = ""
                if total and startrow:
                    if max_pages is None:
                        expected_rows = total
                    else:
                        expected_rows = min(total, max_pages * 50)
                    remaining_rows = max(0, expected_rows - startrow)
                    seconds_per_row = (time.monotonic() - page_started_at) / max(count, 1)
                    eta = f", rough ETA {format_duration(remaining_rows * seconds_per_row)}"
                unique_customers = len({source.customer_id for source in sources if source.customer_id})
                unique_domains = len(
                    {source.source_registered_domain for source in sources if source.source_registered_domain}
                )
                print(
                    f"[{category}] collected {category_sources} ticket-linked listings "
                    f"({len(sources)} total), unique customers {unique_customers}, "
                    f"unique domains {unique_domains}, elapsed {format_duration(elapsed)}{eta}",
                    flush=True,
                )
            if not data.get("count") or startrow >= total:
                break
            if max_pages is not None and pages >= max_pages:
                break
            if delay:
                time.sleep(delay)

        if not quiet:
            print(
                f"Finished {category}: {len(sources) - category_sources_before} ticket-linked listings "
                f"from {pages} pages.",
                flush=True,
            )

    return sources


def write_listings_csv(path: Path, sources: list[BachtrackSource]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(BachtrackSource.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for source in sources:
            writer.writerow(source.__dict__)


def summarize_websites(sources: list[BachtrackSource]) -> list[DiscoveredWebsite]:
    grouped: dict[str, list[BachtrackSource]] = {}
    for source in sources:
        url = website_url(source.source_url)
        if url:
            grouped.setdefault(url, []).append(source)

    websites: list[DiscoveredWebsite] = []
    for url, matches in sorted(grouped.items()):
        domains = {source.source_domain for source in matches if source.source_domain}
        registered_domains = {
            source.source_registered_domain
            for source in matches
            if source.source_registered_domain
        }
        websites.append(
            DiscoveredWebsite(
                url=url,
                domain=sorted(domains)[0] if domains else "",
                registered_domain=(
                    sorted(registered_domains)[0] if registered_domains else ""
                ),
                listing_count=len(matches),
                customer_ids=" | ".join(
                    sorted({source.customer_id for source in matches if source.customer_id})
                ),
                customer_names=" | ".join(
                    sorted({source.customer_name for source in matches if source.customer_name})
                ),
                categories=" | ".join(sorted({source.category for source in matches})),
                statuses=" | ".join(
                    sorted({source.source_status for source in matches if source.source_status})
                ),
                example_target_url=matches[0].source_url,
                example_bachtrack_event_url=matches[0].event_url,
            )
        )
    return websites


def write_websites_csv(path: Path, websites: list[DiscoveredWebsite]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(DiscoveredWebsite.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for website in websites:
            writer.writerow(website.__dict__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover source sites used by Bachtrack listings."
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=sorted(CATEGORIES),
        help="Bachtrack category to scan. Can be passed multiple times. Defaults to concerts.",
    )
    parser.add_argument(
        "--all-categories",
        action="store_true",
        help="Scan concerts, opera, dance, kids, and masterclasses.",
    )
    parser.add_argument(
        "--resolve-ticket-targets",
        action="store_true",
        help="Follow Bachtrack ticket links to resolve the external source URLs.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Limit pages per category. Each page contains up to 50 listings.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Delay between requests in seconds. Defaults to 0.25.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/bachtrack_source_urls.csv"),
        help=(
            "Deduplicated website CSV output path. "
            "Defaults to data/bachtrack_source_urls.csv."
        ),
    )
    parser.add_argument(
        "--listings-output",
        type=Path,
        help="Optionally write the raw ticket-linked listing and redirect evidence to this CSV.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output and print only the final summary.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.resolve_ticket_targets:
        raise SystemExit(
            "Website URL discovery requires --resolve-ticket-targets so Bachtrack's "
            "ticket links can be followed to their destinations."
        )
    categories = sorted(CATEGORIES) if args.all_categories else (args.category or ["concerts"])
    sources = discover_sources(
        categories=categories,
        resolve_targets=args.resolve_ticket_targets,
        max_pages=args.max_pages,
        delay=args.delay,
        quiet=args.quiet,
    )
    websites = summarize_websites(sources)
    write_websites_csv(args.output, websites)
    if args.listings_output:
        write_listings_csv(args.listings_output, sources)

    unique_customers = {source.customer_id for source in sources if source.customer_id}
    unique_domains = {source.source_registered_domain for source in sources if source.source_registered_domain}
    print(f"Wrote {len(websites)} unique website URLs to {args.output}")
    if args.listings_output:
        print(f"Wrote {len(sources)} ticket-linked listings to {args.listings_output}")
    print(f"Unique Bachtrack customers: {len(unique_customers)}")
    if args.resolve_ticket_targets:
        print(f"Unique resolved source domains: {len(unique_domains)}")


if __name__ == "__main__":
    main()
