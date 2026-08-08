import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.bhco.co.uk/'
SITEMAP_URL = f'{SOURCE_URL}event-pages-sitemap.xml'
SOURCE = 'Brandon Hill Chamber Orchestra'
DEFAULT_VENUE = "St George's Bristol"
DEFAULT_CITY = 'Bristol'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def event_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    return list(
        dict.fromkeys(
            clean_text(node)
            for node in soup.select('url > loc')
            if '/event-details/' in clean_text(node)
        )
    )


def parse_datetime(value):
    match = re.match(
        r'^(\d{1,2} [A-Za-z]+ \d{4}),\s*(\d{1,2}:\d{2})'
        r'(?:\s*[–-]\s*(\d{1,2}:\d{2}))?',
        value,
    )
    if not match:
        return None
    try:
        event_date = datetime.strptime(match.group(1), '%d %b %Y').date().isoformat()
        datetime.strptime(match.group(2), '%H:%M')
        if match.group(3):
            datetime.strptime(match.group(3), '%H:%M')
    except ValueError:
        return None
    return event_date, match.group(2), match.group(3)


def parse_location(value):
    parts = [part.strip() for part in value.split(',') if part.strip()]
    city = next(
        (part for part in parts if re.search(r'\bBristol\b', part, re.IGNORECASE)),
        DEFAULT_CITY,
    )
    city = DEFAULT_CITY if re.search(r'\bBristol\b', city, re.IGNORECASE) else city

    first = parts[0] if parts else ''
    if first and first.lower() not in {'bristol', 'uk', 'united kingdom'}:
        venue = first
    else:
        venue = DEFAULT_VENUE
    return venue, city


def parse_event(content, url):
    soup = BeautifulSoup(content, 'html.parser')
    title = clean_text(soup.select_one('[data-hook="event-title"]'))
    date_and_time = parse_datetime(clean_text(soup.select_one('[data-hook="event-full-date"]')))
    location = clean_text(soup.select_one('[data-hook="event-full-location"]'))
    if not title or not date_and_time or not location:
        return None

    summary = clean_text(soup.select_one('[data-hook="event-description"]'))
    about = clean_text(
        soup.select_one(
            '[data-hook="about-section-text"], [data-hook="about-section-wrapper"]'
        )
    )
    description_parts = []
    for part in (summary, about):
        if part and part not in description_parts:
            description_parts.append(part)

    venue, city = parse_location(location)
    event_date, time_from, time_to = date_and_time
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'time_to': time_to,
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': '\n\n'.join(description_parts) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_event, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = future.result()
                if record:
                    records.append(record)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape BHCO event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'] or '', record['title']),
    )


def fetch_event(session, url):
    # Wix occasionally returns a successful but partially rendered response.
    # Retry once when the required event widget fields are absent.
    for _ in range(2):
        record = parse_event(get_response(session, url).content, url)
        if record:
            return record
    return None


class BhcoCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bhco_co_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='classical',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'time_to',
            'venue',
            'city',
            'country_code',
            'description',
            'source_url',
            'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    BhcoCoUkCrawler().run()


if __name__ == '__main__':
    main()
