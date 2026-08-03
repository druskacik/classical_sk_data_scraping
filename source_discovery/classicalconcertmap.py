from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ORGS_JSON_URL = "https://classicalconcertmap.com/data/orgs.json"
CONCERTS_API_URL = "https://classicalconcertmap.com/api/concerts"
DEFAULT_USER_AGENT = "classical-bot-source-discovery/1.0"

SEED_FIELDS = [
    "url",
    "country_code",
    "scope_hint",
    "canonical_url",
    "crawler_path",
    "priority",
    "notes",
]
DEFAULT_OVERRIDES_PATH = Path(__file__).with_name("classicalconcertmap_overrides.csv")


@dataclass(frozen=True)
class DiscoveredOrgSource:
    org_id: int
    name: str
    org_type: str
    event_count: int
    country_code: str
    sample_event_url: str
    homepage_url: str


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


def fetch_orgs(user_agent: str = DEFAULT_USER_AGENT) -> list[dict]:
    req = urllib.request.Request(ORGS_JSON_URL, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_sample_concert(org_id: int, user_agent: str = DEFAULT_USER_AGENT) -> dict | None:
    endpoint = f"{CONCERTS_API_URL}?org={org_id}&limit=1"
    req = urllib.request.Request(endpoint, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            concerts = data.get("concerts", [])
            if concerts:
                return concerts[0]
    except Exception as exc:
        logger.warning(f"Error fetching concert for org {org_id}: {exc}")
    return None


def discover_sources(
    max_orgs: int | None = None,
    delay_seconds: float = 0.05,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[DiscoveredOrgSource]:
    orgs = fetch_orgs(user_agent=user_agent)
    logger.info(f"Loaded {len(orgs)} organizations from {ORGS_JSON_URL}")

    if max_orgs is not None:
        orgs = orgs[:max_orgs]

    try:
        from tqdm import tqdm
        iterator = tqdm(enumerate(orgs, start=1), total=len(orgs), desc="Discovering org sources")
        use_tqdm = True
    except ImportError:
        iterator = enumerate(orgs, start=1)
        use_tqdm = False

    discovered: list[DiscoveredOrgSource] = []
    for idx, org in iterator:
        org_id = org["id"]
        name = org.get("name", "")
        org_type = org.get("org_type", "")
        count = org.get("count", 0)

        concert = fetch_sample_concert(org_id, user_agent=user_agent)
        if concert and concert.get("url"):
            event_url = concert["url"]
            country_code = (concert.get("country_code") or "").upper()
            try:
                home_url = website_home_url(event_url)
                discovered.append(
                    DiscoveredOrgSource(
                        org_id=org_id,
                        name=name,
                        org_type=org_type,
                        event_count=count,
                        country_code=country_code,
                        sample_event_url=event_url,
                        homepage_url=home_url,
                    )
                )
            except Exception as exc:
                logger.warning(f"Failed parsing URL {event_url} for org {org_id}: {exc}")

        if not use_tqdm and (idx % 50 == 0 or idx == len(orgs)):
            logger.info(f"Processed {idx}/{len(orgs)} orgs... Discovered: {len(discovered)}")

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    return discovered


def write_discovery_csv(sources: list[DiscoveredOrgSource], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "org_id",
        "name",
        "org_type",
        "event_count",
        "country_code",
        "sample_event_url",
        "homepage_url",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for s in sources:
            writer.writerow({
                "org_id": s.org_id,
                "name": s.name,
                "org_type": s.org_type,
                "event_count": s.event_count,
                "country_code": s.country_code,
                "sample_event_url": s.sample_event_url,
                "homepage_url": s.homepage_url,
            })
    logger.info(f"Saved {len(sources)} discovered org sources to {output_path}")


def load_overrides(overrides_path: Path) -> dict[str, dict[str, str]]:
    if not overrides_path.exists():
        return {}

    with overrides_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    overrides: dict[str, dict[str, str]] = {}
    for row in rows:
        url = website_home_url(row.get("url", ""))
        country_code = row.get("country_code", "").strip().upper()
        scope_hint = row.get("scope_hint", "").strip().lower() or "country"
        if scope_hint not in {"country", "multi_country"}:
            raise ValueError(f"Invalid scope_hint {scope_hint!r} for {url}")
        if scope_hint == "country" and len(country_code) != 2:
            raise ValueError(f"Country override for {url} requires a two-letter country_code")
        if scope_hint == "multi_country" and country_code:
            raise ValueError(f"Multi-country override for {url} must not set country_code")
        if url in overrides:
            raise ValueError(f"Duplicate ClassicalConcertMap override for {url}")
        overrides[url] = {
            "country_code": country_code,
            "scope_hint": scope_hint,
            "notes": row.get("notes", "").strip(),
        }
    return overrides


def compile_seed_csv(
    discovery_path: Path,
    seed_output_path: Path,
    overrides_path: Path = DEFAULT_OVERRIDES_PATH,
) -> None:
    with discovery_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    overrides = load_overrides(overrides_path)

    # Deduplicate by canonical normalized homepage URL
    seen_urls: set[str] = set()
    seed_rows: list[dict[str, str]] = []

    for row in rows:
        homepage = row.get("homepage_url", "").strip()
        country_code = row.get("country_code", "").strip().upper()
        if not homepage or len(country_code) != 2:
            continue

        try:
            norm_url = website_home_url(homepage)
        except Exception:
            continue

        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)

        override = overrides.get(norm_url)
        scope_hint = ""
        override_note = ""
        if override:
            country_code = override["country_code"]
            scope_hint = override["scope_hint"]
            override_note = override["notes"]

        c_slug = crawler_slug(norm_url)
        c_path = (
            f"crawlers/common/{c_slug}"
            if scope_hint == "multi_country"
            else f"crawlers/{country_code.lower()}/{c_slug}"
        )

        name = row.get("name", "")
        org_id = row.get("org_id", "")
        event_url = row.get("sample_event_url", "")
        notes = f"Discovered via ClassicalConcertMap org {org_id} ({name}); evidence={event_url}"
        if override_note:
            notes = f"{notes}; reviewed_override={override_note}"

        seed_rows.append({
            "url": norm_url,
            "country_code": country_code,
            "scope_hint": scope_hint,
            "canonical_url": "",
            "crawler_path": c_path,
            "priority": "0",
            "notes": notes,
        })

    missing_overrides = set(overrides) - seen_urls
    if missing_overrides:
        missing = ", ".join(sorted(missing_overrides))
        raise ValueError(f"Overrides do not match discovery rows: {missing}")

    # Sort deterministically by country_code, then url
    seed_rows.sort(key=lambda r: (r["country_code"], r["url"]))

    seed_output_path.parent.mkdir(parents=True, exist_ok=True)
    with seed_output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SEED_FIELDS)
        writer.writeheader()
        writer.writerows(seed_rows)

    logger.info(f"Compiled {len(seed_rows)} seed entries into {seed_output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover classical concert sources from classicalconcertmap.com"
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=DEFAULT_OVERRIDES_PATH,
        help="Reviewed country and scope corrections applied during compilation",
    )
    parser.add_argument(
        "--discovery-output",
        type=Path,
        default=Path("data/classicalconcertmap_org_sources.csv"),
        help="Path for raw discovered org sources CSV",
    )
    parser.add_argument(
        "--seed-output",
        type=Path,
        default=Path("seeds/crawler_sources/0004_classicalconcertmap_discovered_sources.csv"),
        help="Path for final compiled seed CSV",
    )
    parser.add_argument(
        "--max-orgs",
        type=int,
        default=None,
        help="Limit number of orgs to process (for testing)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Delay in seconds between API requests",
    )
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="Skip discovery fetching and compile seed directly from existing discovery CSV",
    )

    args = parser.parse_args()

    if not args.compile_only:
        sources = discover_sources(
            max_orgs=args.max_orgs,
            delay_seconds=args.delay,
        )
        write_discovery_csv(sources, args.discovery_output)

    compile_seed_csv(args.discovery_output, args.seed_output, args.overrides)


if __name__ == "__main__":
    main()
