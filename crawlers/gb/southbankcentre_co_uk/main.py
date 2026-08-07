import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.southbankcentre.co.uk/'
SOURCE = 'Southbank Centre'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on/')
CITY = 'London'
LOOKAHEAD_DAYS = 730

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

MONTHS = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, start, end):
    response = session.get(
        LISTING_URL,
        params={
            'artform-filter': 'classical-music',
            'start-date': start.isoformat(),
            'end-date': end.isoformat(),
        },
        timeout=45,
    )
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def listing_cards(session, start, end):
    soup = get_soup(session, start, end)
    return soup.select('.c-event-card')


def parse_dates(value):
    text = clean_text(value).replace('—', '–')
    matches = list(re.finditer(
        r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+'
        r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
        r'(?:\s+(\d{4}))?',
        text,
    ))
    if not matches:
        return []

    inherited_year = next(
        (int(match.group(3)) for match in reversed(matches) if match.group(3)),
        None,
    )
    if inherited_year is None:
        return []

    parsed = []
    for match in matches:
        try:
            parsed.append(date(
                int(match.group(3) or inherited_year),
                MONTHS[match.group(2)],
                int(match.group(1)),
            ))
        except ValueError:
            return []

    if '–' in text and len(parsed) == 2:
        start, end = parsed
        if end < start or (end - start).days > 31:
            # Long date ranges on this mixed arts site are exhibitions rather
            # than individual concert performances.
            return []
        return [
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)
        ]
    return sorted({item.isoformat() for item in parsed})


def parse_time(value):
    match = re.search(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', clean_text(value), re.I)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def card_records(card):
    artform = clean_text(card.select_one('.c-event-card__primary-artform')).casefold()
    if artform != 'classical music':
        return []

    link = card.select_one('.c-event-card__cover-link[href]')
    title = clean_text(card.select_one('.c-event-card__title'))
    date_text = clean_text(card.select_one('.c-event-card__daterange'))
    venue = clean_text(card.select_one('.c-event-card__location'))
    url = urljoin(SOURCE_URL, link.get('href')) if link else ''
    dates = parse_dates(date_text)
    if not title or not url or not venue or not dates:
        return []

    description_parts = []
    for selector in (
        '.c-event-card__listing-details',
        '.c-event-card__performers',
        '.c-event-card__repertoire',
    ):
        text = clean_text(card.select_one(selector))
        if text and text not in description_parts:
            description_parts.append(text)
    description = '\n\n'.join(description_parts) or None
    time_from = parse_time(date_text)

    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': CITY,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date in dates
    ]


def get_concerts():
    start = date.today()
    end = start + timedelta(days=LOOKAHEAD_DAYS)
    records = []
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]

    # Cloudflare currently rejects the site's /page/2/ URLs. The public date
    # filter remains available, so query individual dates concurrently to get
    # beyond the first listing page without dropping later performances.
    def scrape_day(day):
        session = requests.Session()
        session.headers.update(HEADERS)
        return listing_cards(session, day, day)

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(scrape_day, day): day for day in days}
        for future in as_completed(futures):
            day = futures[future]
            try:
                for card in future.result():
                    records.extend(card_records(card))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Southbank Centre listing date',
                    event='crawler_item_failed',
                    level='warning',
                    url=LISTING_URL,
                    listing_date=day.isoformat(),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    unique = {
        (record['url'], record['date'], record['time_from'], record['venue']): record
        for record in records
    }
    return sorted(
        unique.values(),
        key=lambda item: (item['date'], item['time_from'] or '', item['title']),
    )


class SouthbankCentreCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='southbankcentre_co_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    SouthbankCentreCoUkCrawler().run()


if __name__ == '__main__':
    main()
