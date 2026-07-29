# Crawler factory deployment

The crawler factory is a separate scheduled CapRover worker. Its long-running
supervisor starts one crawler-creation batch each day, waits for every Codex
process to finish, and only then asks CapRover to deploy a newer `master`
commit. It therefore remains decoupled from the normal crawler runner and does
not interrupt its own agents during automatic updates.

Each batch clones `master` into a temporary directory, atomically claims at
most five due sources from PostgreSQL, commits generated `main.py` or
`BLOCKED.md` results, opens one pull request, and enables squash auto-merge.
Codex runs with the
`workspace-write` sandbox inside the disposable clone.

Codex is trusted to investigate, implement, and test each crawler. The worker
does not repeat the live scrape. It preserves a result confined to the expected
new crawler directory even when the Codex process exits unsuccessfully or
reaches its generation timeout.

The required GitHub check is deliberately limited to deterministic repository
safety: generated-file scope, protection of existing crawlers, file size,
UTF-8, likely-secret detection, and Python syntax. Live availability, record
quality, pagination, and runtime duration are not merge gates.

## Runtime behavior

The defaults baked into `Dockerfile.crawler-factory` are:

- run one batch daily at `06:00` in `Europe/Prague`;
- process at most five URLs per batch;
- give each Codex generation 60 minutes;
- check `master` for a newer commit every five minutes while idle.

The supervisor stores `last_factory_attempt_date` in
`/var/lib/crawler-factory/service-state.json` before starting a batch. A
container restart therefore does not spend the daily quota twice. If the
container starts after 06:00 and no attempt is recorded for that date, it runs
the missed batch immediately.

The batch runs as a synchronous child process. Update checks cannot execute
while that child is active. After the batch exits, the supervisor immediately
checks for an update and then resumes five-minute checks. A successful
deployment request for the same commit is suppressed for 30 minutes; failed
Git or webhook calls are logged and retried without stopping the service.

## CapRover deployment

### 1. Create a separate app

Create a new CapRover app such as `crawler-factory`. Keep it separate from the
existing crawler-runner app, run exactly one instance, and do not expose it as
an HTTP web application. The deployment webhook is managed by CapRover itself;
the factory container does not listen on a public port.

In the factory app's **Deployment** tab:

1. Configure the repository as
   `github.com/druskacik/classical_sk_data_scraping`.
2. Configure the branch as `master`.
3. Set the captain-definition path to
   `./captain-definition-crawler-factory`.
4. Configure repository authentication or a deploy key as usual.
5. Save the settings and copy the deployment webhook shown by CapRover.

Do **not** add this factory webhook to the GitHub repository's push webhooks.
An ordinary push could then replace the container while Codex is running. The
factory supervisor is the only component that should call this webhook
automatically.

### 2. Configure persistent directories

In **App Configs > Persistent Directories**, create three separate persistent
volume mappings:

| Container path | Purpose |
|---|---|
| `/var/lib/crawler-factory` | daily schedule state, worker lock, and run reports |
| `/factory-home` | isolated GitHub CLI home |
| `/factory-codex-home` | Codex authentication and configuration |

Use CapRover-managed volume labels and keep the app at one instance. These
directories survive image rebuilds, deployments, and container replacement.

### 3. Configure environment variables

Set these in **App Configs > Environmental Variables**:

| Variable | Required/default | Purpose |
|---|---|---|
| `GH_TOKEN` | required | GitHub contents and pull-request write access, without branch-protection bypass |
| `CRAWLER_FACTORY_DEPLOY_WEBHOOK` | required for auto-update | private webhook copied from this CapRover app |
| `CRAWLER_FACTORY_REPOSITORY` | repository URL | repository cloned by the worker and checked for updates |
| `CRAWLER_FACTORY_SCHEDULE_TIME` | `06:00` | daily local time in 24-hour `HH:MM` format |
| `CRAWLER_FACTORY_TIMEZONE` | `Europe/Prague` | IANA timezone used by the scheduler |
| `CRAWLER_FACTORY_UPDATE_INTERVAL_MINUTES` | `5` | idle update-check interval |
| `CRAWLER_FACTORY_MAX_URLS` | `5` | daily URL limit |
| `CRAWLER_FACTORY_TIMEOUT_MINUTES` | `60` | per-URL Codex timeout |
| `DB_HOST` | required | PostgreSQL host containing the crawler source registry |
| `DB_NAME` | required | PostgreSQL database name |
| `DB_USER` | required | PostgreSQL user |
| `DB_PASS` | required | PostgreSQL password |
| `DB_PORT` | `5432` in normal deployments | PostgreSQL port |

CapRover supplies `CAPROVER_GIT_COMMIT_SHA` to the Docker build for a Git-based
deployment. `Dockerfile.crawler-factory` copies that build argument into the
running container, where the supervisor compares it with the current `master`
SHA. Do not configure it manually in CapRover's environmental variables,
because a manually entered value would become stale after deployment.
The supervisor normalizes and validates both commit SHAs and records each
successfully requested target SHA in its persistent service state. A container
restart therefore cannot request the same deployment repeatedly.

All numeric settings must be positive integers. An invalid schedule, timezone,
or numeric setting stops startup with an explicit error. If either the
CapRover webhook or deployed commit SHA is unavailable, crawler creation still
runs but automatic updates are disabled with a warning.

### 4. Deploy and authenticate

Trigger the first deployment from the CapRover dashboard. Once the service is
running, authenticate Codex in the persistent Codex home. One option is to SSH
to the CapRover server, locate the factory container, and run:

```bash
docker ps --filter name=captain--crawler-factory
docker exec -it <container-id> codex login --device-auth
```

The exact service-name fragment follows the CapRover app name. Authentication
is retained in `/factory-codex-home`, so later deployments do not require
another login.

Inspect the app logs after authentication. Startup should report the daily
schedule and update interval. Before 06:00 it waits; after 06:00 it runs
immediately if no attempt has yet been recorded for that day.

## GitHub settings

Protect `master`, disallow force pushes and direct worker pushes, enable
auto-merge, and require the `crawler-factory-validation` check. No approving
review is required for factory PRs.

The worker token must be able to push `crawler-factory/*` branches and create
and merge pull requests, but it must not bypass the `master` ruleset. The first
authenticated batch calls `gh auth setup-git`, pushes a
`crawler-factory/YYYY-MM-DD-<run-id>` branch, creates a PR, and requests squash
auto-merge with branch deletion.

When that PR merges, `master` changes. The idle supervisor notices the new SHA
on its next check, calls the private CapRover webhook, and CapRover replaces the
container with an image built from the new commit.

## Operations and troubleshooting

### Inspect activity

Use the CapRover app logs for scheduling, batch exit status, update checks, and
deployment requests. Detailed generation reports remain under:

```text
/var/lib/crawler-factory/runs/<run-id>/
```

The persistent scheduler state and worker lock are:

```text
/var/lib/crawler-factory/service-state.json
/var/lib/crawler-factory/factory.lock
```

Source status, retry dates, URL aliases, crawler paths, runs, and attempts live
in PostgreSQL. `service-state.json` contains the date on which the supervisor
last attempted its daily batch.

### Apply the initial source seed

Apply migrations before deploying the DB-backed factory:

```bash
uv run alembic upgrade head
```

Then import the versioned legacy URL seed against production:

```bash
uv run python -m seeds.import_crawler_sources \
  seeds/crawler_sources/0001_legacy_builder_urls.csv \
  --prod
```

The importer is transactional and records the filename and SHA-256 checksum.
Reapplying the identical file is a no-op; changing an applied file is rejected,
so corrections and additional source batches must use a new numbered CSV.
Existing `main.py` and `BLOCKED.md` directories are reconciled without Codex
calls. The legacy Salvator URL is stored as an alias of
`farnostsalvator.cz` and owns `crawlers/cz/farnostsalvator_cz`.

Apply every seed in filename order. `0002_existing_crawlers.csv` registers the
historically hand-maintained Slovak crawlers and any other existing crawler
that was absent from the original builder list:

```bash
uv run python -m seeds.import_crawler_sources \
  seeds/crawler_sources/0002_existing_crawlers.csv \
  --prod
```

### Run a manual batch

Running the worker module directly inside the existing container does not
affect the supervisor's daily-attempt marker:

```bash
docker exec -it <container-id> \
  python -m automation.run_crawler_factory \
    --max-urls 1
```

Use `--source-id ID` to target a due registry row. `--url URL` remains
available for manual discovery/debugging and idempotently ingests the URL
before selection; add `--country-code XX` when the country cannot be inferred.
Inside the deployed container, the repository defaults to
`CRAWLER_FACTORY_REPOSITORY`. Pass `--repository` only to override it or when
running outside that configured environment.

The worker's file lock prevents this command from overlapping an already
active scheduled batch.

### Force an update

Use **Deploy Now** in CapRover or invoke the private deployment webhook
manually. A manually initiated deployment is outside the supervisor's idle
guard and can interrupt an active batch, so first confirm in the logs that no
daily batch is running.

### Recover from a failed update

If a CapRover build fails, the existing container remains on its old deployed
SHA. The supervisor retries a successfully accepted webhook request after 30
minutes. A rejected webhook or failed Git check is retried after the normal
five-minute interval. Fix the build on `master` or use CapRover's deployment
history to redeploy or roll back.

### Local image and one-shot rehearsal

Build the image locally with:

```bash
docker build -f Dockerfile.crawler-factory -t classical-crawler-factory .
```

For a one-shot local rehearsal without the supervisor or a pushed PR:

```bash
uv run python -m automation.run_crawler_factory \
  --repository /path/to/local/bare-repository.git \
  --url https://example.cz/ \
  --country-code CZ \
  --max-urls 1 \
  --no-push \
  --keep-workspace
```
