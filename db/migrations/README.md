# Database Migrations

Alembic owns the scraper database schema from this repository.

For an existing database that was created from the old frontend Knex migrations:

```bash
uv run alembic stamp 20260619000100
uv run alembic upgrade head
```

Then run `sql/backfill_country_code.sql` manually and inspect the unresolved rows it returns.

For a fresh database:

```bash
uv run alembic upgrade head
```

Crawler-source rows are application data rather than migration data. After
upgrading a production database to `20260727000100`, apply the initial
versioned seed explicitly:

```bash
uv run python -m seeds.import_crawler_sources \
  seeds/crawler_sources/0001_legacy_builder_urls.csv \
  --prod

uv run python -m seeds.import_crawler_sources \
  seeds/crawler_sources/0002_existing_crawlers.csv \
  --prod
```
