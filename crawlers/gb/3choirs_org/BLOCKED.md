<!-- crawler-factory-metadata
{"url":"https://3choirs.org/","geographic_scope":"country","country_code":"GB","reason_code":"access_blocked","attempted_at":"2026-08-07","retry_after":"2026-09-06"}
-->

# Crawler blocked

## Original URL

https://3choirs.org/

## Why implementation is currently blocked

The source publishes scrapeable concert pages, including a current Three Choirs
Festival programme, but Cloudflare blocks automated access from the crawler
environment. Both a real Playwright browser session and direct HTTP requests
receive an HTTP 403 "Just a moment..." challenge page instead of event data.
A production crawler built against search-engine snippets or cached copies would
not be reliable or complete, so no `main.py` has been created.

The festival is based in the United Kingdom and its current programme is in
Gloucester, so the resolved crawler geography is country scope with ISO country
code `GB`.

## Approaches attempted

- Loaded the canonical home page with Playwright and inspected its network
  requests. The only non-static traffic was the blocked document request and
  Cloudflare challenge/Turnstile traffic; no event API request was exposed.
- Tried direct HTTP access to the home page, `/whats-on/`, `robots.txt`,
  `sitemap.xml`, `wp-sitemap.xml`, `/wp-json/`, and `/wp-json/wp/v2`. Every route
  returned the same Cloudflare HTTP 403 challenge HTML rather than source data.
- Investigated indexed event and archive pages. Search results confirm current
  event detail pages under `/events/` and filtered/calendar listings under
  `/whats-on/`, but those HTML pages remain inaccessible to the crawler runtime.
- Investigated whether a separate public ticketing/XML feed could provide the
  catalogue. No verified Three Choirs endpoint was discoverable or accessible.

## What would unblock implementation

Any one of the following would allow a crawler to be implemented:

- allowlisting the production crawler's egress traffic in Cloudflare;
- a public, stable event API/feed or sitemap that is exempt from the challenge;
- removal or relaxation of the challenge for read-only event listing and detail
  pages; or
- documented credentials or an approved access method for automated catalogue
  retrieval.
