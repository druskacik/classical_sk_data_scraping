import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://oxfordpianofestival.com/'
SITEMAP_URL = f'{SOURCE_URL}event-sitemap.xml'
SOURCE = 'Oxford Piano Festival'
CITY = 'Oxford'

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
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_event_urls(session):
    response = session.get(SITEMAP_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'xml')
    urls = []
    for entry in soup.find_all('url'):
        location = entry.find('loc')
        url = clean_text(location)
        if '/event/' in url:
            urls.append(url)
    return list(dict.fromkeys(urls))


def is_concert(title):
    title_lower = title.lower()
    excluded_terms = ('masterclass', "what's it all about", 'what’s it all about')
    return not any(term in title_lower for term in excluded_terms)


def parse_event(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    title = clean_text(soup.select_one('.section-event-info .event-title'))
    subtitle = clean_text(soup.select_one('.section-event-info .event-subtitle'))
    description = clean_text(soup.select_one('.section-event-info .event-description')) or None
    if not title or not subtitle or not is_concert(title):
        return None

    parts = [part.strip() for part in subtitle.split('|')]
    if len(parts) < 3:
        return None
    try:
        event_date = datetime.strptime(parts[0], '%d %b %Y').date().isoformat()
    except ValueError:
        return None

    time_match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', parts[1])
    venue = clean_text('|'.join(parts[2:]))
    if not venue:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': f'{int(time_match.group(1)):02d}:{time_match.group(2)}' if time_match else None,
        'venue': venue,
        'city': CITY,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = get_event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(parse_event, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
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
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class OxfordPianoFestivalComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='oxfordpianofestival_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='classical',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
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
    OxfordPianoFestivalComCrawler().run()


if __name__ == '__main__':
    main()
