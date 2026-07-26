<!-- crawler-factory-metadata
{"url":"https://www.camerata2018.cz/","country_code":"CZ","reason_code":"no_current_events","attempted_at":"2026-07-26","retry_after":"2026-08-25"}
-->

# Crawler blocked: no current events

Original URL: https://www.camerata2018.cz/

The website currently exposes no concerts. Its homepage is a temporary
placeholder saying that a new Camerata 2018 website is being prepared and
asking visitors to return later.

## Approaches attempted

- Inspected the homepage and its network requests with Playwright. The page
  made no event-related XHR or fetch requests and exposed no structured event
  feed.
- Inspected the WordPress REST API at `/wp-json/wp/v2/types`. It exposes only
  standard WordPress content types and no event or concert custom post type.
- Queried `/wp-json/wp/v2/posts`. The sole result is the default “Hello
  world!” WordPress post, with no concert data.
- Queried `/wp-json/wp/v2/pages`. The sole public page is the placeholder
  homepage, with no dates, venues, programmes, concert links, or event
  listings to parse.
- Inspected the rendered homepage HTML. It contains only the construction
  notice and a Facebook profile link, so there is no HTML-based concert
  inventory to crawl.

## What would unblock implementation

Implementation can proceed once the new website publishes concert listings,
or once Camerata 2018 exposes another stable public source containing its
concert dates and details. At that point, the WordPress REST API should be
checked again for a new event content type before falling back to HTML
parsing.
