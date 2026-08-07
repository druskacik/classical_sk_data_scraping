<!-- crawler-factory-metadata
{"url":"https://www.sco.org.uk/","geographic_scope":"country","country_code":"GB","reason_code":"access_blocked","attempted_at":"2026-08-07","retry_after":"2026-09-06"}
-->

# Scottish Chamber Orchestra crawler blocked

## Original URL

https://www.sco.org.uk/

## Why a crawler cannot currently be implemented

The Scottish Chamber Orchestra website publishes a scrapeable concert calendar
and individual event pages, but its Cloudflare protection returns an HTTP 403
challenge page to non-interactive clients. The production crawler environment
therefore cannot reliably discover or retrieve the concert data. Building from
search-engine copies would be incomplete and stale, and would not be a viable
production crawler.

The source is a UK-based, classical-only orchestra calendar. Its events include
touring performances, but that does not make the source multi-country; the
resolved crawler geography is GB.

## Approaches attempted

- Inspected the public What's On and Calendar pages through an indexed browser
  view. This confirmed current concerts, per-performance dates and detail URLs.
- Looked for a discoverable structured API or JSON endpoint in the accessible
  page and search representations. No usable event API was exposed.
- Requested the What's On listing, dated calendar route, sitemap, and alternate
  hostname/request variants with a normal HTTP client. Cloudflare returned 403
  challenge pages or did not provide usable site HTML.
- Retried the listing, dated calendar, and sitemap with a Chromium-impersonating
  HTTP/TLS client. Cloudflare still returned 403 challenge pages.
- Considered parsing the server-rendered calendar and event detail HTML, but the
  origin HTML is unavailable to the production-compatible client. Indexed
  snippets do not consistently expose each performance's venue, city, and full
  programme description.

## What would unblock implementation

Any stable machine-readable event API or feed, permission for the crawler's
production traffic through Cloudflare, or a production-supported browser client
capable of completing the site's challenge would allow the calendar and event
detail pages to be scraped reliably.
