<!-- crawler-factory-metadata
{"url":"https://www.jko.cz/","country_code":"CZ","reason_code":"no_current_events","attempted_at":"2026-07-31","retry_after":"2026-08-30"}
-->

# No current concert events

Original URL: https://www.jko.cz/

The public program currently contains no upcoming classical music concerts, so a
working crawler cannot be implemented and tested against current concert data.
The only future program entry published at the time of investigation is
“Začátek školního roku” on 1 September 2026, which is a school-year opening
rather than a concert. All other checked future months are empty.

## Approaches attempted

- Inspected the site and its program calendar with Playwright.
- Traced network requests and found
  `POST https://www.jko.cz/events/ajaxeventsdays`, an XHR endpoint that accepts
  `year` and `month`. It returns only day numbers that contain events, not
  structured event details.
- Queried the calendar API for current, past, and future months. It confirmed
  the single September event day and empty surrounding months.
- Inspected the HTML program route at
  `https://www.jko.cz/program?filterDate=YYYY-MM-01&search=1`. Event cards and
  detail links are server-rendered and would be parseable when concerts exist.
- Checked every month from August 2026 through January 2028. Only the
  non-concert school-year opening was present.

## What would unblock implementation

Publish at least one upcoming concert in the public program calendar, including
its event detail page. Once concert data is available, the server-rendered
monthly program HTML can be used to implement and test the crawler; the
`ajaxeventsdays` endpoint can help identify months containing events.
