<!-- crawler-factory-metadata
{"url":"https://www.msobrno.cz/","country_code":"CZ","reason_code":"access_blocked","attempted_at":"2026-07-30","retry_after":"2026-08-29"}
-->

# Crawler blocked

## Original URL

https://www.msobrno.cz/

## Why the crawler cannot currently be implemented

The requested website is not reachable because its hostname does not resolve in
DNS. Both `www.msobrno.cz` and the bare domain `msobrno.cz` fail before an HTTP
connection can be made. Consequently, there is no current source response from
which to discover concerts or implement and verify stable extraction logic.

## Approaches attempted

- Opened the original HTTPS URL with Playwright; navigation failed with
  `net::ERR_NAME_NOT_RESOLVED`.
- Opened the bare-domain HTTPS variant with Playwright to check whether only the
  `www` record was affected; it failed with the same DNS error.
- Inspected Playwright's captured network requests. The document request itself
  failed, so there were no API/XHR responses, scripts, or structured data
  endpoints available to reconstruct.
- Searched public web indexing for pages and concerts under `site:msobrno.cz`,
  the exact domain name, and the likely organization name. No indexed pages from
  the requested website were available to establish a current listing or detail
  page structure.
- Checked hostname resolution independently in the execution environment; no
  address was returned for either hostname.

## What would unblock implementation

Restoring DNS and HTTP access for `www.msobrno.cz` (or providing the current
official source URL and access details) would allow the network API and HTML
structure to be investigated. The source must also expose at least one current
concert so the crawler can be implemented and tested against real listing and
detail data.
