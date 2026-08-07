import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.fairfield.co.uk/'
SOURCE = 'Fairfield Halls'
SITEMAP_URL = f'{SOURCE_URL}sitemap.xml'
CITY = 'Croydon'
DEFAULT_VENUE = 'Fairfield Halls'
GENRES = {'classical', 'opera'}

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
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def event_urls(session):
    # The sitemap retains event detail pages after they leave What's On, so it
    # exposes both the current catalogue and every still-published archive page.
    response = session.get(SITEMAP_URL, timeout=45)
    response.raise_for_status()
    return {
        url for url in re.findall(r'<loc>\s*(.*?)\s*</loc>', response.text)
        if url.startswith(f'{SOURCE_URL}events/')
    }


def metadata(soup):
    values = {}
    for item in soup.select('.c-meta__item'):
        key = clean_text(item.select_one('.c-meta__key .h-accessibility')).casefold()
        value = clean_text(item.select_one('.c-meta__value'))
        if key and value:
            values[key] = value
    return values


def parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed.date().isoformat(), parsed.strftime('%H:%M')


def detail_records(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.select_one('.c-page-header--event h1'))
    meta = metadata(soup)
    venue = meta.get('location', '') or clean_text(
        soup.select_one('.c-page-header--event .c-page-header__pretitle')
    ) or DEFAULT_VENUE
    genre = meta.get('genre', '') or meta.get('genres', '')
    genre_tokens = {token.strip().casefold() for token in genre.split(',')}
    if not title or not genre_tokens.intersection(GENRES):
        return []

    content = soup.select_one('.o-page-grid__content--sidebar')
    if content:
        for element in content.select('script, style, .h-accessibility'):
            element.decompose()
    description = clean_text(content) or None

    starts = set()
    for time_element in soup.select(
        '#site-main .c-page-header--event time[itemprop="startDate"], '
        '#site-main .c-meta time[itemprop="startDate"], '
        '#site-main .c-instance-list time[itemprop="startDate"]'
    ):
        parsed = parse_datetime(time_element.get('datetime'))
        if parsed:
            starts.add(parsed)

    # Some templates put the time in a separate metadata row even though the
    # header datetime is midnight or date-only.
    explicit_time = meta.get('time', '')
    time_match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', explicit_time, re.I)
    fallback_time = None
    if time_match:
        hour = int(time_match.group(1)) % 12
        if time_match.group(3).casefold() == 'pm':
            hour += 12
        fallback_time = f'{hour:02d}:{int(time_match.group(2) or 0):02d}'

    records = []
    for event_date, event_time in starts:
        if event_time == '00:00' and fallback_time:
            event_time = fallback_time
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': event_time,
            'venue': venue,
            'city': CITY,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(detail_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Fairfield Halls event detail',
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


class FairfieldCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='fairfield_co_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    FairfieldCoUkCrawler().run()


if __name__ == '__main__':
    main()
