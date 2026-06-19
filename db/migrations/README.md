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
