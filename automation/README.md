# Crawler factory deployment

The crawler factory is a separate scheduled worker. It clones `master` into a
temporary directory, attempts at most five hardcoded URLs, commits generated
`main.py` or `BLOCKED.md` results, opens one pull request, and enables squash
auto-merge. Codex runs with the workspace-write sandbox inside that disposable
clone; the local builder retains its existing full-access default.

Codex is trusted to investigate, implement, and test the crawler. The worker
does not repeat the live scrape. It preserves any result confined to the
expected new crawler directory, including a usable result left behind when the
Codex process exits unsuccessfully or reaches its generation timeout.

The required GitHub check is deliberately limited to deterministic repository
safety: generated-file scope, protection of existing crawlers, file size,
UTF-8, likely-secret detection, and Python syntax. Live availability, record
quality, pagination, and runtime duration are not merge gates.

## Build and configure

```bash
docker build -f Dockerfile.crawler-factory -t classical-crawler-factory .
```

Provide these secrets at runtime:

- `GH_TOKEN`: a GitHub token with repository contents and pull-request write
  access, but no branch-protection bypass.
- A persistent Codex home mounted at `/factory-codex-home`.

Persist `/var/lib/crawler-factory` for retry state and run reports. The
container uses a separate `/factory-home` for GitHub CLI configuration so the
Codex child cannot read it.

Example invocation:

```bash
docker run --rm \
  -e GH_TOKEN \
  -v crawler-factory-state:/var/lib/crawler-factory \
  -v crawler-factory-home:/factory-home \
  -v crawler-factory-codex:/factory-codex-home \
  classical-crawler-factory
```

Run the container daily at 06:00 Europe/Prague with host cron or a systemd
timer. The worker also holds a non-blocking file lock, so overlapping runs exit
without doing work.

## GitHub settings

Protect `master`, disallow direct pushes and force pushes, enable auto-merge,
and require the `crawler-factory-validation` check. No approving review is
required for factory PRs.

The first authenticated run calls `gh auth setup-git`, pushes a
`crawler-factory/YYYY-MM-DD-<run-id>` branch, creates a PR, and requests squash
auto-merge with branch deletion.

For a local rehearsal without pushing:

```bash
uv run python -m automation.run_crawler_factory \
  --repository /path/to/local/bare-repository.git \
  --url https://example.cz/ \
  --max-urls 1 \
  --no-push \
  --keep-workspace
```
