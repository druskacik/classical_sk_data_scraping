<!-- crawler-factory-metadata
{"url":"https://www.conwyclassicalmusic.co.uk/","geographic_scope":"country","country_code":"GB","reason_code":"access_blocked","attempted_at":"2026-08-08","retry_after":"2026-09-07"}
-->

# Crawler blocked

## Original URL

https://www.conwyclassicalmusic.co.uk/

## Why implementation is blocked

The site publishes a classical-music festival programme in Conwy, Wales, but all
direct requests currently receive HTTP 401 and a StackProtect security page. The
page requires a Google reCAPTCHA token and submits it with JavaScript before the
actual site can be accessed. This affects both listing and concert-detail URLs,
so a production crawler cannot retrieve the event catalogue or descriptions
reliably without attempting to bypass an interactive anti-bot control.

Search-engine indexing confirms that the site has current 2026 concerts and
older detail pages, but indexed snippets and cached extracts are neither a
complete catalogue nor a stable scrapeable source suitable for a crawler.

## Approaches attempted

- Checked the homepage and `/event/` catalogue, plus known individual event
  detail URLs. Direct HTTP clients received the same verification page.
- Checked likely structured sources including `/wp-json/`, the WordPress event
  REST route, `?rest_route=...`, `/sitemap.xml`, `/wp-sitemap.xml`, feeds, and
  `robots.txt`. StackProtect intercepts all of them before source data is
  returned.
- Tested the `www` and apex hosts over HTTPS, HTTP redirects, and direct origin-IP
  resolution. None exposed an unprotected source.
- Tested a JavaScript-capable headless Chromium session. The reCAPTCHA challenge
  did not yield accessible event HTML in the automated environment.
- Inspected the HTML that is available before verification. It contains only the
  security form and reCAPTCHA scripts, with no embedded event data or usable API
  endpoint.

## What would unblock implementation

Any stable, non-interactive source containing the full programme would unblock
the crawler, such as public access to the event HTML, a REST/JSON endpoint or
feed exempted from StackProtect, a downloadable programme, or allow-listing the
production crawler. Once available, the crawler can use the event catalogue and
detail pages to collect dates, times, the default St Mary's Church venue (with
the separately identified Royal Cambrian Academy lecture venue), and full
programme descriptions.
