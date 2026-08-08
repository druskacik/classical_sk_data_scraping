import re
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.chichesterchamberconcerts.com/'
SITEMAP_URL = f'{SOURCE_URL}sitemap.xml'
SOURCE = 'Chichester Chamber Concerts'
VENUE = 'Assembly Room'
CITY = 'Chichester'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

MONTHS = (
    'January|February|March|April|May|June|July|August|September|October|'
    'November|December'
)
DATE_RE = re.compile(
    rf'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*'
    rf'(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTHS})[,]?\s+(20\d{{2}})\b',
    re.IGNORECASE,
)
TIME_RE = re.compile(r'\b(\d{1,2})(?:[:.](\d{2}))\s*(am|pm)?\b', re.IGNORECASE)
EVENT_PATH_RE = re.compile(
    rf'^/\d{{1,2}}(?:st|nd|rd|th)-({MONTHS.lower()})-20\d{{2}}/?$',
    re.IGNORECASE,
)


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
    urls = []
    for node in soup.select('url > loc'):
        url = clean_text(node)
        path = urlparse(url).path
        if EVENT_PATH_RE.match(path) or path.rstrip('/').endswith(
            '/family-concert-june-30th-2018'
        ):
            urls.append(url)
    return list(dict.fromkeys(urls))


def parse_date(text):
    match = DATE_RE.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(' '.join(match.groups()), '%d %B %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(text):
    date_match = DATE_RE.search(text)
    nearby_text = text[date_match.end() : date_match.end() + 40] if date_match else text[:80]
    match = TIME_RE.search(nearby_text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    period = (match.group(3) or '').lower()
    if minute > 59 or hour > (12 if period else 23):
        return None
    if period == 'pm' and hour != 12:
        hour += 12
    elif period == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def parse_title(soup, page_text):
    for node in soup.select('main h1, main h2, main h3, main .sqsrte-large'):
        title = clean_text(node).split('\n', 1)[0]
        if title and not DATE_RE.search(title) and title.upper() != 'PROGRAMME:':
            return title

    document_title = clean_text(soup.title)
    if document_title:
        return re.split(r'\s+[—|]\s+', document_title, maxsplit=1)[0].strip()

    date_match = DATE_RE.search(page_text)
    if date_match:
        remainder = page_text[date_match.end() :].strip(' ,\n')
        return remainder.split('\n', 1)[0].strip()
    return None


def parse_event(content, url):
    soup = BeautifulSoup(content, 'html.parser')
    main = soup.select_one('main#page, main')
    if not main:
        return None

    page_text = clean_text(main)
    event_date = parse_date(page_text)
    title = parse_title(soup, page_text)
    if not title or not event_date:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': parse_time(page_text),
        'venue': VENUE,
        'city': CITY,
        'country_code': 'GB',
        'description': page_text or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class ChichesterChamberConcertsComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='chichesterchamberconcerts_com',
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
        session = requests.Session()
        session.headers.update(HEADERS)
        records = []
        for url in event_urls(session):
            try:
                record = parse_event(get_response(session, url).content, url)
                if record:
                    records.append(record)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Chichester Chamber Concerts event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    ChichesterChamberConcertsComCrawler().run()


if __name__ == '__main__':
    main()
