# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Data scraping pipeline for classical music events. Crawlers scrape concert listings from cultural websites, store them in a PostgreSQL database, use Gemini AI to classify broad-source events, and use the Codex SDK to extract concert programmes.

## Commands

```bash
# Run all crawlers + analyzers (scheduled loop)
uv run python main.py

# Run a single crawler
uv run python -m crawlers.sk.filharmonia_sk.main

# Run a single analyzer
uv run python -m analyzers.analyze_potential_events
```

Package management uses `uv` (not pip).

## Architecture

### Pipeline flow

1. **Crawlers** (`crawlers/<country_code>/<site>/main.py`) — Each crawler has a `main()` function that scrapes a specific website, saves results to `data/<site>.csv`, and uploads to the DB via `upload_concerts()`.
2. **Analyzer: classify events** (`analyzers/analyze_potential_events.py`) — Uses Gemini to determine if events in the `potential_event` table are classical music, then copies confirmed ones to `classical_concert`.
3. **Analyzer: extract concert programmes** (`analyzers/analyze_concert_programs.py`) — Uses the Codex SDK to inspect concert sources, resolve composers and works, and populate the concert programme tables.

### Two upload paths

- **Direct crawlers** (filharmonia, snd, sfk, etc.) — Sites known to only list classical music. They call `upload_concerts()` which inserts directly into `classical_concert`.
- **Broad crawlers** (ticketportal, goout, predpredaj, etc.) — General event sites. They call `upload_potential_concerts()` which inserts into `potential_event` for later AI classification.

### Key shared modules

- `crawlers/classical.py` — `upload_concerts()` / `upload_potential_concerts()` for DB insertion, `Concert` class
- `crawlers/extractors.py` — City extraction from postal codes (uses `data/cities_*.csv`), date/time parsing
- `crawlers/formaters.py` — `format_date()` converts `dd.mm.yyyy` → `yyyy-mm-dd`

### Database tables

- `classical_concert` — Confirmed classical music events
- `potential_event` — Unclassified events awaiting AI analysis
- `composer` — Composer registry
- `classical_concert_composer` — Many-to-many join table

### Environment variables (.env)

`DB_NAME`, `DB_USER`, `DB_PASS`, `DB_HOST`, `DB_PORT`, `GEMINI_API_KEY`, `CODEX_HOME`

## Adding a new crawler

Create `crawlers/<country_code>/<site_domain>/main.py` with a `main()` function. It will be auto-discovered by `main.py`. Use `upload_concerts()` for classical-only sources or `upload_potential_concerts()` for general sources. Set `CrawlerConfig.country_code` to an ISO 3166-1 alpha-2 code and save a CSV backup to `data/<site>.csv`.
