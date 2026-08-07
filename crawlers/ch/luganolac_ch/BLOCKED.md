<!-- crawler-factory-metadata
{"url":"https://www.luganolac.ch/lac/home","geographic_scope":"country","country_code":"CH","reason_code":"access_blocked","attempted_at":"2026-08-06","retry_after":"2026-09-05"}
-->

# Crawler blocked by Cloudflare

## Original URL

https://www.luganolac.ch/lac/home

The source is LAC Lugano Arte e Cultura, a mixed cultural venue based in
Lugano, Switzerland. A future crawler should therefore use country code `CH`
and `upload_target="potential"`.

## Why implementation is currently blocked

All direct requests are intercepted by a Cloudflare managed challenge and
return HTTP 403 with a "Just a moment..." page. This occurs in a real
Playwright browser as well as with ordinary HTTP requests, so a production
crawler cannot currently load either listings or event detail pages.

The site does have scrapeable concerts in principle: search-engine indexing
shows current season pages and individual programme pages, including both
future and past events. Shipping a parser based only on search snippets would
not be complete or reliable and would not provide a source the production
crawler can request.

## Approaches attempted

- Loaded the original page with Playwright and inspected its network traffic.
  The only non-static traffic was the blocked document request and Cloudflare's
  challenge endpoint; no application API request was reached.
- Tried direct HTTP access with browser-like headers to the home page, likely
  programme/calendar paths, `robots.txt`, and `sitemap.xml`. Every request
  returned the same HTTP 403 challenge document.
- Checked indexed Italian, English, German, and individual event URLs to
  confirm that the site publishes current and archived programme content.
  Those URLs are also protected when requested directly, leaving no usable
  HTML fallback.

## What would unblock implementation

Any stable machine-readable endpoint or HTML route that is exempt from the
Cloudflare challenge would unblock the crawler. Alternatively, LAC could
allowlist the crawler's production egress address or provide an official
calendar feed/API. Once access is available, network inspection should be
repeated first to identify the site's structured event source before building
an HTML parser.
