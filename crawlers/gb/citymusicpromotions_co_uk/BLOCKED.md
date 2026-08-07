<!-- crawler-factory-metadata
{"url":"https://citymusicpromotions.co.uk/","geographic_scope":"country","country_code":"GB","reason_code":"access_blocked","attempted_at":"2026-08-07","retry_after":"2026-09-06"}
-->

# City Music Promotions crawler blocked

Original URL: https://citymusicpromotions.co.uk/

City Music Promotions is a UK classical-concert promoter, so the resolved
geography is country-level GB. Its website's **Buy tickets** page contains no
event data of its own; it embeds the following Eventim Light shop as an iframe:

`https://www.eventim-light.com/uk/a/63c82ca274fb184f4eebf902/iframe/`

The source currently cannot be implemented as a working, testable crawler
because Eventim's Akamai edge denies access from the crawler environment. The
iframe and individual event URLs fail in Playwright with
`net::ERR_HTTP2_PROTOCOL_ERROR`, while direct HTTP requests receive an Akamai
403 Access Denied response. Future City Music Promotions events are still
externally indexed, so this is an access problem rather than an empty calendar.

## Approaches attempted

- Loaded the home page and **Buy tickets** page with Playwright and inspected
  all network requests. No event API or structured event request was made by
  the WordPress site; the only catalogue request was the Eventim Light iframe.
- Navigated to the iframe and a known individual Eventim event URL with
  Playwright. Both failed before any event-data network calls could be
  inspected.
- Requested the iframe, shop root, known event page, JavaScript embed, and
  plausible API paths over HTTP with browser-like headers and a referring page.
  Eventim's `.com` host returned Akamai 403 responses.
- Tried Eventim's `.co.uk` hostname. It returned the generic EVENTIM.Light
  organiser-marketing website rather than this promoter's ticket shop.
- Inspected the site's WordPress REST API and XML sitemap. They expose ordinary
  pages and news posts, but no concert post type, event catalogue, or retained
  archive containing the required date, venue, and city fields.
- Inspected the rendered and downloaded Buy tickets HTML. It contains only the
  iframe URL, not server-rendered fallback event cards or JSON data.

## What would unblock implementation

Implementation can proceed when the Eventim Light `.com` shop is reachable
from the crawler environment, or when City Music Promotions provides an
alternate public event feed/API or server-rendered listing. Once reachable,
the iframe's network requests should be inspected first for a structured
catalogue endpoint; otherwise its listing and detail HTML can be parsed.
