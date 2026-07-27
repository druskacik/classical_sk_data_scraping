<!-- crawler-factory-metadata
{"url":"https://www.suksymphony.cz/","country_code":"CZ","reason_code":"no_current_events","attempted_at":"2026-07-27","retry_after":"2026-08-26"}
-->

# No current concerts

The source currently exposes no upcoming concerts. As of 2026-07-27, the
concerts page contains only events dated from April through December 2025.
Creating `main.py` would therefore produce no current records, so this source
is marked for retry instead.

## Investigation

- Original URL: https://www.suksymphony.cz/
- Network requests from both the homepage and the concerts page were inspected
  with Playwright. No dedicated event API or client-side event request was
  made; the listings are rendered as WordPress/Elementor page content.
- The WordPress REST API was tested at
  `https://suksymphony.cz/wp-json/wp/v2/pages?slug=koncerty&_fields=id,link,modified,title,content`.
  It returns structured page metadata and parseable rendered HTML, but the page
  was last modified on 2025-06-30 and all listed dates are in 2025.
- The public HTML at `https://suksymphony.cz/koncerty/` was inspected as a
  fallback. It contains the same past events and no pagination, archive link,
  or upcoming 2026 programme.

## What would unblock implementation

Publication of at least one current or upcoming concert on the concerts page
or in its WordPress REST representation would provide live data against which
the parser can be implemented and verified. Retry after the orchestra updates
its programme.
