import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://hochuli-konzert.ch/'
SOURCE = 'Hochuli Konzert AG'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.7',
}

LOCATION_MAP = {
    'Tonhalle Zürich': ('Tonhalle Zürich', 'Zürich'),
    'Kirche St.Peter Zürich': ('Kirche St. Peter Zürich', 'Zürich'),
    'Kartause Ittingen': ('Kartause Ittingen', 'Warth-Weiningen'),
    # The archive confirms that the recurring Auffahrtskonzerte series in
    # Münsterlingen takes place in the Klosterkirche.
    'Münsterlingen': ('Klosterkirche Münsterlingen', 'Münsterlingen'),
    'Klosterkirche Münsterlingen': ('Klosterkirche Münsterlingen', 'Münsterlingen'),
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def description_for(element):
    detail = element.select_one('.searchContent')
    if detail:
        return clean_text(detail) or None

    sections = element.select('.text.border, .text.archivetext')
    parts = [clean_text(section) for section in sections]
    return '\n\n'.join(part for part in parts if part) or None


def make_record(element):
    # Current entries have no data-art; archived non-concert items are marked
    # Kulturreise and must not enter the concert pipeline.
    if element.get('data-art') not in (None, 'Konzert'):
        return None

    date_value = (element.get('data-date') or '')[:8]
    try:
        event_date = date(
            int(date_value[:4]), int(date_value[4:6]), int(date_value[6:8])
        ).isoformat()
    except (TypeError, ValueError):
        return None

    date_label = clean_text(element.select_one('.eventdatum'))
    # A range is an event/festival aggregate rather than an individual concert.
    if re.search(r'\d{1,2}\.\s*[–-]', date_label):
        return None

    title = clean_text(element.select_one('.eventname'))
    time_from = clean_text(element.select_one('.time')) or None
    if time_from and not re.fullmatch(r'[0-2]\d:[0-5]\d', time_from):
        time_from = None

    location = clean_text(element.select_one('.location'))
    venue_city = LOCATION_MAP.get(location)
    if not title or not venue_city:
        return None

    detail_link = element.select_one('a.link[href]')
    url = urljoin(SOURCE_URL, detail_link['href']) if detail_link else SOURCE_URL
    venue, city = venue_city
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'CH',
        'description': description_for(element),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    response = requests.get(SOURCE_URL, headers=HEADERS, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    records = []
    for element in soup.select('.eventpreview'):
        record = make_record(element)
        if record:
            records.append(record)

    log_message(
        'Hochuli calendar parsed',
        event='crawler_scrape_completed',
        url=SOURCE_URL,
        record_count=len(records),
    )
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class HochuliKonzertChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='hochuli_konzert_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
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
    HochuliKonzertChCrawler().run()


if __name__ == '__main__':
    main()
