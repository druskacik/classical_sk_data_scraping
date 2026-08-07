<!-- crawler-factory-metadata
{"url":"https://www.kkl-luzern.ch/","geographic_scope":"country","country_code":"CH","reason_code":"access_blocked","attempted_at":"2026-08-06","retry_after":"2026-09-05"}
-->

# KKL Luzern crawler blocked

## Original URL

https://www.kkl-luzern.ch/

## Why implementation is currently blocked

The KKL Luzern website publishes a mixed programme of concerts, tours, comedy,
talks, and other events, but all tested first-party pages currently return a
Vercel Security Checkpoint with HTTP 429 to automated clients. The checkpoint
also blocks a real Playwright browser session, so no complete event catalogue or
event-detail HTML can be obtained and validated. Shipping selectors inferred
only from search-engine snippets would produce an untested and unreliable
production crawler.

The organization and venue are based in Luzern, Switzerland, so the resolved
country code is `CH`. If access becomes possible, this broad cultural source
must use `upload_target="potential"`.

## Approaches attempted

- Opened the homepage with Playwright and inspected its network requests. The
  response was HTTP 429 and the only subsequent traffic was to Vercel's
  `challenge.v2.wasm` and `request-challenge` endpoints; no event API request
  was made.
- Requested the homepage, `/events`, `/en/events`, `/robots.txt`, and
  `/sitemap.xml` directly with browser and crawler user agents. Every first-party
  request returned the same checkpoint instead of source HTML, XML, or JSON.
- Probed likely programme paths and API discovery paths. These were also handled
  by the checkpoint and exposed no structured endpoint.
- Checked indexed event-list and event-detail results. They confirm that current
  concerts and detailed fields such as date, start time, hall, and description
  exist, but search results are incomplete, externally mediated, and do not
  provide a dependable full-catalogue interface or archive.
- Tested alternate host/language access and an HTML text proxy. The alternate
  access was denied or only reproduced the Vercel checkpoint.

## What would unblock implementation

Any of the following would allow the crawler to be implemented and tested:

- KKL Luzern permits the crawler environment to access its public event pages;
- a stable, documented event API/feed or sitemap is supplied;
- Vercel bot protection is adjusted to allow read-only access to the public
  programme and event-detail routes; or
- representative raw event-list and event-detail responses are provided along
  with a first-party endpoint that remains accessible in production.
