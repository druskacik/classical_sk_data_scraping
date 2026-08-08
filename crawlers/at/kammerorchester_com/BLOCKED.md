<!-- crawler-factory-metadata
{"url":"https://www.kammerorchester.com/","geographic_scope":"country","country_code":"AT","reason_code":"access_blocked","attempted_at":"2026-08-08","retry_after":"2026-09-07"}
-->

# Crawler blocked by Vercel Security Checkpoint

The original source is https://www.kammerorchester.com/, the website of the
Wiener KammerOrchester in Austria. Although the orchestra tours internationally,
the source is an Austrian organization, so its resolved crawler geography is AT.

A crawler cannot currently be implemented because all direct requests to the
site return HTTP 429 with a Vercel Security Checkpoint instead of concert data.
The checkpoint also prevents a normal browser session from reaching the site,
and its challenge request fails. A production crawler would therefore receive
only the checkpoint page and could not reliably locate or parse concerts.

The following approaches were attempted:

- Loaded the original URL with Playwright and inspected its network requests.
  The only non-static requests were the blocked document request and Vercel's
  security challenge; no concert API request was exposed.
- Waited for the browser challenge to complete, but the page remained on the
  checkpoint with HTTP 429.
- Requested both `www` and apex-domain homepages directly with browser-like
  headers; both returned the same checkpoint.
- Probed `/de/concerts`, `/sitemap.xml`, `/robots.txt`, and a Next.js RSC-style
  route. None exposed an API or parseable source response.
- Checked indexed public search results. They confirm that current and touring
  concerts exist, but cached search snippets are incomplete, unstable, and not
  a suitable source for a universal production crawler.

Implementation would be unblocked by the site allowing non-interactive crawler
requests, by successful passage through the Vercel checkpoint, or by the
publisher exposing a stable public concerts feed/API (JSON, iCalendar, RSS, or
equivalent) that is not protected by the checkpoint.
