<!-- crawler-factory-metadata
{"url":"https://orchestra.rte.ie/","geographic_scope":"country","country_code":"IE","reason_code":"access_blocked","attempted_at":"2026-08-07","retry_after":"2026-09-06"}
-->

# RTÉ Orchestra crawler blocked

## Original URL

https://orchestra.rte.ie/

## Why implementation is currently blocked

The site publishes current, scrapeable-looking concert pages, but all tested
routes are protected by a Cloudflare Turnstile challenge. Both a real Playwright
browser session and ordinary HTTP requests receive an HTTP 403 challenge page.
The browser remains on the challenge after waiting, so it cannot expose the
application's own requests or event markup. A production `requests` crawler
would therefore parse a Cloudflare page rather than the concert catalogue.

The source is the Ireland-based RTÉ Concert Orchestra and its event catalogue is
country-scoped to Ireland, so the resolved country code is `IE`.

## Approaches attempted

- Loaded the home page with Playwright and inspected its full network request
  list. Only Cloudflare challenge and Turnstile traffic was available; no event
  API request was made before the challenge stopped the application loading.
- Waited for the browser challenge to resolve, but it remained an HTTP 403 page.
- Tried the canonical home page and `/whats-on/` with direct HTTP requests.
- Probed likely WordPress structured-data routes: `/wp-json/`,
  `/wp-json/wp/v2/types`, and `/wp-sitemap.xml`. Each returned the same HTTP 403
  Cloudflare challenge HTML instead of JSON or XML.
- Checked `/robots.txt`; it too is behind the same challenge.
- Confirmed through indexed public search results that `/whats-on/` and
  `/events/<id>/<slug>/` pages contain current event dates, times, venues, and
  descriptions. Those indexed copies are not a stable or complete source from
  which to build a universal production crawler.
- Tried the `www.orchestra.rte.ie` hostname as a possible alternate origin; it
  does not resolve.

## What would unblock implementation

Any stable machine-readable route that is permitted through Cloudflare would
unblock the crawler, such as an event JSON/feed endpoint, a sitemap and event
HTML allowlisted for non-browser clients, or documented API access. A browser
environment in which the Turnstile challenge succeeds would also allow the
site's application requests and HTML structure to be investigated, after which
the underlying endpoint could be tested for production use.
