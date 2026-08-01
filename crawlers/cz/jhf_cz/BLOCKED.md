<!-- crawler-factory-metadata
{"url":"https://www.jhf.cz/","country_code":"CZ","reason_code":"no_current_events","attempted_at":"2026-08-01","retry_after":"2026-08-31"}
-->

# Crawler blocked

- Original URL: https://www.jhf.cz/
- The URL currently serves JHF Heřmanovice spol. s r. o., a road-construction,
  stone-mining, and machinery-services company. It exposes no classical music
  concerts or event listings.
- The rendered HTML was inspected with Playwright. The page contains company
  services, contact information, and project text, but no concert calendar or
  event detail links.
- Playwright network requests were inspected after loading the page. No
  concert/event API or other structured event feed was present.
- Because the source currently exposes no concerts, there is no parseable
  listing or detail source from which to implement the requested crawler.
- Implementation would be unblocked by providing the correct classical music
  website URL, or by the site adding a public concert listing/API.
