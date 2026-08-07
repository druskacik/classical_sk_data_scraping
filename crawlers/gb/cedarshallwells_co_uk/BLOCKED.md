<!-- crawler-factory-metadata
{"url":"https://cedarshallwells.co.uk/","geographic_scope":"country","country_code":"GB","reason_code":"access_blocked","attempted_at":"2026-08-07","retry_after":"2026-09-06"}
-->

# Crawler blocked

## Original URL

https://cedarshallwells.co.uk/

## Why implementation is blocked

The site is protected by a Cloudflare managed challenge. Requests from the
Playwright browser receive HTTP 403 and remain on a "Performing security
verification" page, even after waiting for the challenge. The challenge does
not expose event data, and a production `requests`-based crawler would receive
the same non-parseable response.

## Approaches attempted

- Loaded the homepage in Playwright and inspected its network traffic. The only
  dynamic requests were Cloudflare challenge/Turnstile requests; no event API
  or structured concert-data request was made.
- Waited for the browser challenge to complete, but it remained on the HTTP 403
  verification page.
- Probed the WordPress REST API through both `/wp-json/` and `?rest_route=/`,
  including the `/wp-json/wp/v2/types` discovery endpoint. All returned the
  Cloudflare challenge rather than JSON.
- Probed the Yoast sitemap advertised by the publicly accessible `robots.txt`,
  as well as the standard WordPress sitemap and generic sitemap paths. All
  sitemap responses returned the challenge instead of XML.
- Probed the WordPress feed, likely event paths (`/events/` and `/whats-on/`),
  the `www` hostname, and plain HTTP. These routes were also blocked and did
  not provide parseable HTML or structured data.

## What would unblock implementation

Any stable, non-challenged source containing the event catalogue would be
sufficient, such as allowlisting crawler traffic, disabling the managed
challenge for public event/API/sitemap routes, exposing the WordPress REST API
or event feed, or providing a documented public events API. Once one of those
is available, the API should be preferred; otherwise the public event listing
and detail HTML can be parsed.
