---
name: search-production-logs
description: Query ClassicalBot production runtime logs in VictoriaLogs with LogSQL. Use when investigating crawler, analyzer, crawler-factory, deployment, exception, failure, or recent production behavior; do not use for database data analysis.
---

# Search Production Logs

Use the read-only VictoriaLogs query endpoint. Keep database analysis on
`agent_utils/search_db.py`.

Require `VICTORIALOGS_URL` to contain the private VictoriaLogs base URL,
including the scheme and port but no trailing slash. If it is unset, report
that production log access is not configured and stop instead of guessing an
endpoint.

## Query logs

Start with a narrow window and a small response:

```bash
curl --silent --show-error --fail-with-body \
  "${VICTORIALOGS_URL:?VICTORIALOGS_URL is not configured}/select/logsql/query" \
  --data-urlencode 'query=_time:15m' \
  --data-urlencode 'limit=20'
```

Treat an empty response as a successful query with no matches. Broaden the
window in this order when needed: `1h`, `24h`, then `7d`. Start with 20 results
and increase the limit only when the investigation requires it.

## Select the production service

Use the `app` field as the primary container filter. It is present on both
structured application records and plain container logs:

```logsql
_time:24h app:="classical-bot"
_time:24h app:="classical-crawler-factory"
```

- Select `classical-bot` for scheduled crawlers, event classification, concert
  programme analysis, and the normal scraper deployment cycle.
- Select `classical-crawler-factory` for Codex crawler generation, validation,
  pull requests, factory scheduling, and factory deployment activity.

Structured application records also carry the same name in `service`, but
prefer `app` when the investigation may include unstructured records. Combine
the service filter with structured fields when useful:

```logsql
_time:24h app:="classical-bot" level:="error"
_time:7d app:="classical-bot" event:="crawler_failed" crawler:="filharmonia_sk"
_time:24h app:="classical-crawler-factory" level:="error"
```

Use structured fields when available:

```logsql
_time:24h schema:="classical_bot.log.v1"
_time:24h schema:="classical_bot.log.v1" level:="error"
_time:7d event:="crawler_failed"
_time:24h crawler:="filharmonia_sk"
```

## Handle failures and results

- Distinguish no matches from DNS, Tailnet, TLS, and non-2xx HTTP failures.
- Report access failures clearly; do not claim production is healthy when the
  query could not run.
- Summarize the relevant timestamp, level, service, event, crawler, and message
  fields instead of pasting a large response.
- Include enough raw detail to support the conclusion
