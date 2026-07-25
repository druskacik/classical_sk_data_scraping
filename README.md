# ClassicalBot

Crawlers for classical music concerts websites.

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
