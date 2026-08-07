import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.festival-piano.com/'
SITEMAP_URL = f'{SOURCE_URL}wp-sitemap-posts-fpr_spectacle-1.xml'
SOURCE = "Festival International de Piano de La Roque d'Anthéron"

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9',
}

MONTHS = {
    'janvier': 1,
    'février': 2,
    'mars': 3,
    'avril': 4,
    'mai': 5,
    'juin': 6,
    'juillet': 7,
    'août': 8,
    'septembre': 9,
    'octobre': 10,
    'novembre': 11,
    'décembre': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, parser='html.parser'):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, parser)


def spectacle_urls(session):
    soup = get_soup(session, SITEMAP_URL, 'xml')
    return list(dict.fromkeys(
        clean_text(location)
        for location in soup.select('url > loc')
        if '/fpr_spectacle/' in clean_text(location)
    ))


def parse_date(url, date_text):
    slug_match = re.search(r'/fpr_spectacle/(\d{2})-(\d{2})-(\d{2})(?:[-_/]|$)', url)
    text_match = re.search(
        r'(\d{1,2})\s+(' + '|'.join(MONTHS) + r')',
        date_text.lower(),
    )
    if not slug_match or not text_match:
        return None

    year = 2000 + int(slug_match.group(3))
    day = int(text_match.group(1))
    month = MONTHS[text_match.group(2)]
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_time(date_text):
    match = re.search(r'(\d{1,2})\s*h\s*(\d{2})?', date_text.lower())
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2) or "00"}'


def parse_location(element):
    if not element:
        return None, None

    city_element = element.select_one(':scope > div')
    city = clean_text(city_element)
    location_parts = []
    for child in element.contents:
        if getattr(child, 'name', None) == 'div':
            continue
        text = clean_text(child)
        if text:
            location_parts.append(text)
    location = clean_text(' '.join(location_parts))

    if ' - ' in location:
        listed_city, venue = location.split(' - ', 1)
        city = city or clean_text(listed_city)
    else:
        venue = ''

    # An event sometimes repeats the venue as a suffix after the city.  The
    # explicit location line is authoritative; records without a venue are
    # skipped rather than using the city as a venue placeholder.
    return clean_text(venue) or None, city or None


def parse_detail(session, url):
    soup = get_soup(session, url)
    body = soup.select_one('.event-body')
    if not body:
        return None

    title = clean_text(body.select_one('.event-body-distribution h1'))
    if not title:
        title = clean_text(body.select_one('.event-body-distribution h2'))
    practical = body.select_one('.event-body-pratical')
    headings = practical.select('h3') if practical else []
    if len(headings) < 2:
        return None

    date_text = clean_text(headings[0])
    event_date = parse_date(url, date_text)
    venue, city = parse_location(headings[1])
    if not title or not event_date or not venue or not city:
        return None

    description = clean_text(body.select_one('.event-body-text-primary')) or None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': parse_time(date_text),
        'venue': venue,
        'city': city,
        'country_code': 'FR',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = spectacle_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(parse_detail, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape festival concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda row: (row['date'], row['time_from'] or '', row['title'], row['url']),
    )


class FestivalPianoComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='festival_piano_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    FestivalPianoComCrawler().run()


if __name__ == '__main__':
    main()
