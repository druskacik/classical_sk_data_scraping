import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://halle.co.uk/'
SOURCE = 'The Hallé'
CONCERTS_URL = urljoin(SOURCE_URL, 'concerts/')
PAST_CONCERTS_URL = urljoin(SOURCE_URL, 'past-concerts/')
AJAX_URL = urljoin(SOURCE_URL, 'wp-admin/admin-ajax.php')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        'Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

VENUE_CITIES = {
    'bridgewater hall': 'Manchester',
    "hallé st peter's": 'Manchester',
    'halle st peter': 'Manchester',
    'royal albert hall': 'London',
    'barbican': 'London',
    'roundhay park': 'Leeds',
    'octagon': 'Buxton',
    'sheffield city hall': 'Sheffield',
    'victoria hall': 'Stoke-on-Trent',
    'de montfort hall': 'Leicester',
    'royal concert hall': 'Nottingham',
    'the glasshouse': 'Gateshead',
    'sage gateshead': 'Gateshead',
}
CITY_NAMES = (
    'Manchester', 'London', 'Buxton', 'Leeds', 'Sheffield', 'Nottingham',
    'Leicester', 'Liverpool', 'Salford', 'Gateshead', 'Stoke-on-Trent',
    'Blackburn', 'Bradford', 'Birmingham', 'Edinburgh', 'Glasgow',
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def canonical_url(url):
    parts = urlsplit(urljoin(SOURCE_URL, url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/') + '/', '', ''))


def get_soup(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def links_from(soup):
    return {
        canonical_url(link['href'])
        for link in soup.select('a[href*="/event/"]')
        if urlsplit(urljoin(SOURCE_URL, link['href'])).netloc == 'halle.co.uk'
    }


def discover_urls(session):
    urls = set()

    # The current calendar's "Load more" control calls this HTML fragment API.
    soup = get_soup(session, CONCERTS_URL)
    urls.update(links_from(soup))
    page = 2
    while soup.select_one('[data-pagination-button]'):
        soup = get_soup(session, AJAX_URL, params={
            'action': 'load_events_page',
            'event_page': page,
            'featured_count': 1,
            'ajax': 'true',
        })
        page_urls = links_from(soup)
        if not page_urls or page_urls.issubset(urls):
            break
        urls.update(page_urls)
        page += 1

    # Past concerts use ordinary numbered pages and remain fully scrapeable.
    page = 1
    while True:
        url = PAST_CONCERTS_URL if page == 1 else urljoin(PAST_CONCERTS_URL, f'page/{page}/')
        soup = get_soup(session, url)
        page_urls = links_from(soup)
        if not page_urls or page_urls.issubset(urls):
            break
        urls.update(page_urls)
        if not soup.select_one('[data-pagination-button]'):
            break
        page += 1
    return urls


def resolve_city(venue):
    for city in CITY_NAMES:
        if re.search(rf'\b{re.escape(city)}\b', venue, re.I):
            return city
    folded = venue.casefold()
    for venue_name, city in VENUE_CITIES.items():
        if venue_name in folded:
            return city
    return None


def description_from(soup):
    parts = []
    for node in soup.select('.content--intro, .article-content .content--text, .post-details'):
        text = clean_text(node)
        if not text or text in parts:
            continue
        # Ticket prices are not useful programme-extraction input.
        text = re.split(r'\nTicket (?:Information|information)\b', text, maxsplit=1)[0].strip()
        if text:
            parts.append(text)
    return '\n\n'.join(parts) or None


def displayed_dates(value):
    """Expand strings such as 'Thu 29 Oct & Sun 1 Nov 2026'."""
    matches = re.findall(
        r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})'
        r'(?:\s+([A-Z][a-z]{2}))?(?:\s+(\d{4}))?',
        value,
    )
    month = year = None
    dates = []
    for day, found_month, found_year in reversed(matches):
        month = found_month or month
        year = found_year or year
        if not month or not year:
            continue
        try:
            dates.append(datetime.strptime(f'{day} {month} {year}', '%d %b %Y').date())
        except ValueError:
            continue
    return list(reversed(dates))


def detail_records(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.select_one('h1'))
    venue = clean_text(soup.select_one('.event-venues'))
    city = resolve_city(venue)
    if not title or not venue or not city:
        return []

    starts = []
    seen = set()
    for node in soup.select('time[datetime]'):
        value = node.get('datetime', '').strip()
        try:
            start = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            continue
        key = (start.date(), start.time().replace(tzinfo=None))
        if key not in seen:
            seen.add(key)
            starts.append(start)

    description = description_from(soup)
    records = [{
        'title': title,
        'date': start.date().isoformat(),
        'url': url,
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    } for start in starts]
    if records:
        return records

    # Booking links (and their precise times) disappear from archived pages,
    # but the event's displayed calendar date remains available.
    return [{
        'title': title,
        'date': date.isoformat(),
        'url': url,
        'time_from': None,
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    } for date in displayed_dates(clean_text(soup.select_one('.event-dates')))]


class HalleCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='halle_co_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        urls = discover_urls(session)
        records = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(detail_records, session, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    records.extend(future.result())
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape Hallé concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(records, key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['venue']
        ))


def main():
    HalleCoUkCrawler().run()


if __name__ == '__main__':
    main()
