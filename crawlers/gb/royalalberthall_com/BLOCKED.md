<!-- crawler-factory-metadata
{"url":"https://www.royalalberthall.com/","geographic_scope":"country","country_code":"GB","reason_code":"access_blocked","attempted_at":"2026-08-07","retry_after":"2026-09-06"}
-->

# Royal Albert Hall crawler blocked

Original URL: https://www.royalalberthall.com/

A working crawler cannot currently be implemented because the site's Imperva/Incapsula protection blocks this environment before it serves any event content. The events listing and individual event pages return an interstitial HTTP 403 response rather than scrapeable application HTML.

## Approaches attempted

- **API/network:** Loaded the events calendar with Playwright and inspected its network requests. The initial document request returned HTTP 403, so the site's application never loaded and made no event API requests that could be reconstructed. The only subsequent request was an Incapsula challenge/telemetry request.
- **HTML listing and detail pages:** Tried both `https://www.royalalberthall.com/tickets/events/` and a known event URL (`https://www.royalalberthall.com/tickets/events/2026/aaron-tveit`) with Playwright. Both returned the same Incapsula HTTP 403 interstitial without event HTML.
- **Sitemap/archive discovery:** Checked the site's robots declaration and attempted its advertised sitemap route as well as `/sitemap.xml`. The robots file is available, but both sitemap routes are replaced by the Incapsula interstitial, so they cannot supply current or archived event URLs.
- **Direct HTTP:** Repeated the listing, sitemap, and detail probes with normal browser-style and crawler user agents. Changing the user agent did not provide event content; protected routes continued to return the Incapsula page.

## What would unblock implementation

Implementation would be possible if the site permits this crawler environment through its bot protection, or if Royal Albert Hall exposes a stable public event feed/API or an unprotected sitemap containing event pages. Access to a successful browser session that reaches the application would also allow its network traffic and rendered HTML to be inspected and a supported parser to be built.
