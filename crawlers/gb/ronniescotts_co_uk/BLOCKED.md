<!-- crawler-factory-metadata
{"url":"https://www.ronniescotts.co.uk/","geographic_scope":"country","country_code":"GB","reason_code":"access_blocked","attempted_at":"2026-08-07","retry_after":"2026-09-06"}
-->

# Crawler blocked

Original URL: https://www.ronniescotts.co.uk/

Ronnie Scott's publishes events for its London venue, but its Cloudflare
challenge currently rejects automated access with HTTP 403. The challenge did
not resolve in a real Playwright browser, so neither the event catalogue nor
event detail pages can be scraped reliably from this environment.

## Approaches attempted

- Opened the homepage with Playwright and inspected its network traffic. Only
  Cloudflare challenge requests were available; the application did not load
  far enough to expose an event API response.
- Inspected the publicly reachable JavaScript bundle. It reveals that the
  event catalogue uses a structured HTML fragment endpoint at
  `/find-a-show?loadmore=1`, requested with the `X-LoadOMatic: yes` header and
  paginated with a `page` parameter.
- Requested that fragment endpoint with its expected AJAX headers and tried
  ordinary `/find-a-show` listing variants. Cloudflare returned HTTP 403 for
  all of them.
- Tried the site's sitemap and direct HTML routes. The sitemap was also
  challenged. Only a generic 404 page shell was reachable, and it contains no
  concert records or detail data.

## What would unblock implementation

Allowlisting the crawler's production egress IP or otherwise granting
non-interactive access through Cloudflare would make the discovered paginated
listing endpoint and linked event detail pages scrapeable. A documented public
event feed or API supplied by Ronnie Scott's would also unblock the crawler.
