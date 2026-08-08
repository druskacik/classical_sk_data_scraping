# ClassicalBot

Crawlers for classical music concerts websites.

## Production services

The repository deploys two independent CapRover services:

- `classical-bot` is the normal concert pipeline. It is built from the default
  `Dockerfile`, applies database migrations, and starts `python main.py`. The
  app supervises the daily crawler/classifier scheduler and a continuous
  programme analyzer as independent components, so scraping does not wait for
  programme extraction. Its persistent runtime state is stored under
  `/var/lib/classical-bot`.
- `classical-crawler-factory` creates and validates crawler changes with Codex.
  Its CapRover deployment uses `captain-definition-crawler-factory`, which
  selects `Dockerfile.crawler-factory`, and starts
  `python -m automation.run_crawler_factory_service`. It keeps its scheduler,
  worker, GitHub CLI, and Codex state separate from the normal pipeline.

The factory can publish crawler changes, but it does not run the production
concert-scraping pipeline. See `automation/README.md` for its deployment and
runtime details.

The in-app programme analyzer defaults to batches of 100 concerts with
concurrency 4. It immediately continues after a full batch and waits five
minutes after draining the eligible queue. Fatal batches back off for fifteen
minutes; stalled batch processes are terminated without stopping the daily
scraper scheduler. Deployments normally wait for the active analyzer batch to
finish, with a one-hour maximum drain period.

Structure:

`crawlers/{country_code}/` - crawlers for given country
`crawlers/{country_code}/{url}.py` - crawler for given url

## Env:

```
API_URL=
DB_HOST=
DB_NAME=classical_sk
DB_USER=
DB_PASS=
DB_PORT=5432
HTTP_PROXY=
HTTPS_PROXY=
PYTHONUNBUFFERED=1
CODEX_HOME=/app/.codex
RUN_JOBS_ON_STARTUP=false
```

# Run crawlers

```
uv run python -m crawlers.sk.filharmonia_sk.main
```

## Codex resumes:

musicbrainz:
codex resume 019fb970-7f0f-7081-bc57-9556b294591b
