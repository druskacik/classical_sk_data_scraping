<!-- crawler-factory-metadata
{"url":"https://palaciolibertad.gob.ar/","geographic_scope":"country","country_code":"AR","reason_code":"access_blocked","attempted_at":"2026-08-08","retry_after":"2026-09-07"}
-->

# Palacio Libertad crawler blocked

## Original URL

https://palaciolibertad.gob.ar/

The source is the Palacio Libertad cultural centre in Buenos Aires, Argentina.
It is a broad cultural calendar (music, theatre, dance, cinema, visual arts,
workshops, and other activities), so a future crawler should use country code
`AR` and `upload_target="potential"`.

## Why implementation is currently blocked

Cloudflare returns an HTTP 403 challenge page to automated clients before any
application content is served. The challenge also appears in a real Playwright
browser session and does not resolve into the underlying site. Consequently,
there is no stable response that a repository crawler can parse or use to
discover all current and past concerts.

Search-engine indexing confirms that the site still publishes an agenda and
individual `/events/.../` pages, including current events and older concerts.
However, search snippets are incomplete, externally generated, and unsuitable
as a universal or authoritative scrape source.

## Approaches attempted

- Loaded the home page with Playwright and inspected its network requests. The
  only dynamic traffic was Cloudflare challenge/Turnstile traffic; no calendar
  or event API request was exposed.
- Navigated directly to `/wp-json/` with Playwright. It returned the same 403
  challenge rather than WordPress API metadata.
- Requested the home page, `/robots.txt`, `/sitemap_index.xml`, the WordPress
  REST API, and representative REST collection/search routes with a normal HTTP
  client and browser-like user agent. Every source endpoint returned the same
  Cloudflare challenge.
- Tried the WordPress `rest_route` form and checked likely alternate hostnames
  and the former CCK domain; these did not provide an accessible origin.
- Inspected indexed agenda, category, archive, and individual event results to
  confirm that concerts exist and that the source is a mixed cultural calendar.
  Those results cannot provide exhaustive pagination or reliable detail bodies.

## What would unblock implementation

Any one of the following would permit a crawler to be built:

- allowlisting the production crawler's requests at Cloudflare;
- a public, ungated WordPress REST endpoint, calendar feed, sitemap, or data API;
- an official origin/alternate hostname that serves the same calendar without
  the interactive challenge; or
- a non-interactive access mechanism (such as a documented API credential or
  stable Cloudflare service token) available to the production service.

