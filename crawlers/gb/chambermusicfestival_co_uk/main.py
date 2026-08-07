import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.chambermusicfestival.co.uk/'
ARCHIVE_URL = urljoin(SOURCE_URL, 'archive/')
SOURCE = 'Highgate International Chamber Music Festival'
CITY = 'London'

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


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def festival_pages(session):
    """Return every annual programme exposed by the current site and archive."""
    pages = set()
    for index_url in (SOURCE_URL, ARCHIVE_URL):
        soup = get_soup(session, index_url)
        for link in soup.select('a[href]'):
            href = urljoin(index_url, link.get('href'))
            path_part = href.rstrip('/').rsplit('/', 1)[-1]
            if 'festival' in path_part.lower() and re.search(r'20\d{2}', path_part):
                pages.add(href.split('#', 1)[0])
    return sorted(pages)


def page_year(soup, url):
    heading = clean_text(soup.select_one('h1'))
    match = re.search(r'20\d{2}', f'{heading} {url}')
    return int(match.group()) if match else None


def parse_datetime(value, year):
    if not value or not year:
        return None, None
    normalized = re.sub(r'(?<=\d)(st|nd|rd|th)\b', '', value, flags=re.I)
    match = re.search(
        r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*,?\s*'
        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*'
        r'(\d{1,2})\s+([A-Za-z]+)',
        normalized,
        re.I,
    )
    if not match:
        return None, None
    hour, minute, meridiem, day, month = match.groups()
    try:
        event_date = datetime.strptime(f'{day} {month} {year}', '%d %B %Y').date()
    except ValueError:
        return None, None
    hour = int(hour) % 12 + (12 if meridiem.lower() == 'pm' else 0)
    return event_date.isoformat(), f'{hour:02d}:{int(minute or 0):02d}'


def parse_concert(node, year, page_url):
    title = clean_text(node.select_one('.concert-title'))
    date_text = clean_text(node.select_one('p.uk-text-lead'))
    event_date, time_from = parse_datetime(date_text, year)

    venue = ''
    for paragraph in node.select('p'):
        text = clean_text(paragraph)
        match = re.match(r'Venue\s*:\s*(.+)', text, re.I)
        if match:
            venue = match.group(1).strip()
            break

    if not title or not event_date or not venue:
        return None

    booking_link = node.select_one('a[href].cta')
    url = urljoin(page_url, booking_link['href']) if booking_link else page_url

    # Archive layouts changed over time, but programme prose consistently lives
    # in paragraphs inside the concert block. Retain works, notes and artists.
    description_parts = []
    for paragraph in node.select('p'):
        text = clean_text(paragraph)
        if not text or paragraph.select_one('.concert-title'):
            continue
        if 'uk-text-lead' in (paragraph.get('class') or []):
            continue
        if re.match(r'(?:Venue\s*:|£|Approx\. finish time\s*:)', text, re.I):
            continue
        description_parts.append(text)
    description = clean_text('\n\n'.join(description_parts))

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': CITY,
        'country_code': 'GB',
        'description': description or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    for url in festival_pages(session):
        try:
            soup = get_soup(session, url)
            year = page_year(soup, url)
            for node in soup.select('.concert'):
                record = parse_concert(node, year, url)
                if record:
                    records.append(record)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape festival page',
                event='crawler_page_failed',
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


class ChamberMusicFestivalCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='chambermusicfestival_co_uk',
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
    ChamberMusicFestivalCoUkCrawler().run()


if __name__ == '__main__':
    main()
