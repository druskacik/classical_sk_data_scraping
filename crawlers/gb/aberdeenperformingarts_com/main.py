import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.aberdeenperformingarts.com/'
SITEMAP_URL = f'{SOURCE_URL}sitemap-posttype-event.xml'
SOURCE = 'Aberdeen Performing Arts'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# The calendar includes a handful of partner venues outside Aberdeen. Keep
# explicit mappings so the home-city default is never applied to those events.
VENUE_CITIES = {
    'Aberdeen Art Gallery': 'Aberdeen',
    'Bon Accord Baths': 'Aberdeen',
    'Breakneck Comedy': 'Aberdeen',
    'Cowdray Hall': 'Aberdeen',
    'Fountainhall at the Cross Church': 'Aberdeen',
    "His Majesty's Theatre": 'Aberdeen',
    'Kings Pavilion': 'Aberdeen',
    'Lemon Tree': 'Aberdeen',
    'MH Big Sky Studio': 'Aberdeen',
    'Music Hall': 'Aberdeen',
    'P&J Arena': 'Aberdeen',
    "St Andrew's Cathedral": 'Aberdeen',
    "St Machar's Cathedral": 'Aberdeen',
    'Station House Media Unit': 'Aberdeen',
    'The Anatomy Rooms': 'Aberdeen',
    'The Terrace': 'Aberdeen',
    'The Tunnels': 'Aberdeen',
    'Tivoli Theatre': 'Aberdeen',
    'Wild Goose (Victorian Toilets)': 'Aberdeen',
    'Bervie Brow Research Station': 'Stonehaven',
    'Forvie Visitors Centre': 'Newburgh',
    'Mill of Benholm': 'Johnshaven',
}

MONTHS = {
    month.lower(): number
    for number, month in enumerate(
        ('', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
         'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
    )
    if month
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
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
    return sorted({
        clean_text(node)
        for node in soup.select('url > loc')
        if '/whats-on/' in clean_text(node)
    })


def parse_dates(value):
    """Return every date explicitly printed in a single-date or range label."""
    text = clean_text(value)
    matches = list(re.finditer(
        r'\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|'
        r'Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\s+'
        r'(\d{1,2})\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?',
        text,
        flags=re.IGNORECASE,
    ))
    if not matches:
        return []
    fallback_year = next(
        (int(match.group(3)) for match in reversed(matches) if match.group(3)),
        None,
    )
    results = []
    for match in matches:
        month = MONTHS.get(match.group(2)[:3].lower())
        year = int(match.group(3)) if match.group(3) else fallback_year
        if not month or not year:
            continue
        try:
            parsed = date(year, month, int(match.group(1))).isoformat()
        except ValueError:
            continue
        if parsed not in results:
            results.append(parsed)
    return results


def parse_time(value):
    text = clean_text(value).replace('.', ':')
    match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b', text, re.I)
    if not match:
        return None
    try:
        return datetime.strptime(
            f'{match.group(1)}:{match.group(2) or "00"}{match.group(3)}',
            '%I:%M%p',
        ).strftime('%H:%M')
    except ValueError:
        return None


def page_description(soup):
    parts = []
    for container in soup.select(
        '.c-container__blocks .o-layout__item.u-3\\/4\\@desktop .c-col-text-area'
    ):
        text = clean_text(container)
        if text and text not in parts:
            parts.append(text)
    if not parts:
        for container in soup.select('.c-container__blocks .c-col-text-area'):
            if container.select_one('.c-important-info'):
                continue
            text = clean_text(container)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def detail_records(session, url):
    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
    title = clean_text(soup.select_one('.c-event-info__title'))
    date_text = clean_text(soup.select_one('.c-event-info__detail--date'))
    venue = clean_text(soup.select_one('.c-event-info__detail--venue'))
    city = VENUE_CITIES.get(venue)
    dates = parse_dates(date_text)
    if not title or not dates or not venue or not city:
        return []

    time_from = parse_time(soup.select_one('.c-event-info__detail--time'))
    description = page_description(soup)
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date in dates
    ]


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
                    'Failed to scrape Aberdeen Performing Arts event',
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


class AberdeenPerformingArtsComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='aberdeenperformingarts_com',
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
    AberdeenPerformingArtsComCrawler().run()


if __name__ == '__main__':
    main()
