<!-- crawler-factory-metadata
{"url":"https://www.jamu.cz/","country_code":"CZ","reason_code":"no_current_events","attempted_at":"2026-07-30","retry_after":"2026-08-29"}
-->

# Crawler blocked: no current events

## Original URL

https://www.jamu.cz/

The site's event source is the JAMU calendar at
https://www.jamu.cz/akce-a-projekty/kalendar-akci/. It provides a dedicated
`Koncert` filter, but that source currently exposes no concerts to scrape.

## Approaches attempted

- **API/network:** Inspected the homepage and calendar requests with
  Playwright. No event API or structured event-data request was made. The only
  non-page requests were static assets, cookie-consent translations, and
  analytics.
- **HTML calendar:** Inspected the server-rendered calendar and its dedicated
  concert route at
  `https://www.jamu.cz/akce-a-projekty/kalendar-akci/typ/koncert/`.
  The calendar uses ordinary HTML/HTMX navigation and contains no concert
  cards or concert detail links for the current month.
- **Adjacent/future months:** Followed the calendar's `mesic-konani` HTML routes
  and checked subsequent academic-season months through October 2026. The
  concert-filtered views also contained no event cards or detail links.
- **List view:** Identified the server-rendered list-view route
  `https://www.jamu.cz/akce-a-projekty/kalendar-akci/pohled/seznam/typ/koncert/`;
  it uses the same currently empty filtered event source.

## What would unblock implementation

Publication of at least one current or upcoming concert in the JAMU calendar
would allow its listing markup, detail-page fields, pagination, and programme
description to be implemented and verified. Retry after the new academic
season's programme has likely been published.
