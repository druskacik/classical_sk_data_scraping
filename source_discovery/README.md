# Source discovery

This package contains reproducible research workflows for finding new external
sources. It is separate from runtime crawlers, scheduled automation, and the
small reusable tools in `agent_utils`.

## Bachtrack

Discover ticket destinations and write deduplicated website origins:

```bash
uv run python -m source_discovery.bachtrack.discover \
  --all-categories \
  --resolve-ticket-targets \
  --output data/bachtrack_source_urls.csv \
  --listings-output data/bachtrack_source_listings.csv
```

Prepare normalized review batches:

```bash
uv run python -m source_discovery.bachtrack.prepare_review
```

Compile reviewed candidates into the numbered crawler-source seed:

```bash
uv run python -m source_discovery.bachtrack.compile_seed \
  --include-medium-confidence
```

The files under `data/bachtrack_*` are generated discovery and review evidence.
The finalized immutable output belongs under `seeds/crawler_sources/`.

## MusicBrainz

Download artists matching MusicBrainz's classical tag:

```bash
uv run python -m source_discovery.musicbrainz.download_classical_artists
```

Enrich a downloaded artist CSV with official-homepage and other URL relations:

```bash
uv run python -m source_discovery.musicbrainz.download_classical_artists \
  --enrich-urls-from data/musicbrainz_classical_artists.csv
```

## ClassicalConcertMap

Discover organization homepages and compile a new crawler-source seed:

```bash
uv run python -m source_discovery.classicalconcertmap \
  --discovery-output data/classicalconcertmap_org_sources.csv \
  --seed-output seeds/crawler_sources/0004_classicalconcertmap_discovered_sources.csv
```

