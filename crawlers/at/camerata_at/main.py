import json
import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://camerata.at/de'
CONCERTS_URL = f'{SOURCE_URL}/konzerte'
SOURCE = 'CAMERATA Salzburg'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def translated(value, language='de'):
    if isinstance(value, dict):
        return clean_text(value.get(language) or value.get('en'))
    return clean_text(value)


def programme_text(event):
    works = []
    event_data = event.get('event') or {}
    for item in event_data.get('eventWorks') or []:
        project_work = item.get('projectWork') or {}
        if project_work.get('pause'):
            continue
        work = project_work.get('work') or {}
        composer = translated(work.get('translatedComposerName'))
        name = translated(work.get('translatedNameExt'))
        line = ': '.join(part for part in (composer, name) if part)
        if line and line not in works:
            works.append(line)
    return '\n'.join(works)


def description_text(event):
    parts = []
    content = event.get('content') or {}
    translations = content.get('textLongTranslations') or {}
    for value in (
        translations.get('de'),
        content.get('textLong'),
        event.get('info'),
        event.get('programm_alt_text'),
        translated(event.get('programmAltTextTranslations')),
    ):
        text = clean_text(value)
        if text and text not in parts:
            parts.append(text)

    programme = programme_text(event)
    if programme:
        parts.append(f'Programm\n{programme}')
    return '\n\n'.join(parts) or None


def make_record(event):
    title = translated(event.get('titleTranslations')) or clean_text(event.get('title'))
    start = event.get('start_time') or ''
    location = event.get('location') or {}
    venue = (
        translated(location.get('printNames'))
        or clean_text(location.get('printName'))
        or translated(location.get('Names'))
    )
    room = translated(location.get('Rooms'))
    if room and room.lower() not in venue.lower():
        venue = f'{venue}, {room}' if venue else room
    city = clean_text(location.get('place'))
    country_code = clean_text(location.get('country')).upper()
    path = clean_text(event.get('url_alias'))

    try:
        start_at = datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return None

    if (
        not title
        or not path
        or not venue
        or not city
        or not re.fullmatch(r'[A-Z]{2}', country_code)
    ):
        return None

    return {
        'title': title,
        'date': start_at.date().isoformat(),
        'url': urljoin(SOURCE_URL, f'/de{path}'),
        'time_from': start_at.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description_text(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def listing_events(html):
    soup = BeautifulSoup(html, 'html.parser')
    component = soup.select_one('concerts-landingpage[concerts]')
    if component is None:
        raise ValueError('Concert catalogue data was not found in the page')
    catalogue = json.loads(component['concerts'])
    return [
        event
        for month in catalogue.values()
        for event in (month.get('events') or [])
    ]


class CamerataAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='camerata_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
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
        response = requests.get(CONCERTS_URL, headers=HEADERS, timeout=60)
        response.raise_for_status()
        events = listing_events(response.text)
        records = []
        for event in events:
            record = make_record(event)
            if record:
                records.append(record)
            else:
                log_message(
                    'Skipped incomplete CAMERATA concert',
                    event='crawler_item_skipped',
                    level='warning',
                    url=urljoin(SOURCE_URL, clean_text(event.get('url_alias'))),
                    error_type='IncompleteEventData',
                    error_message='Required date, title, URL, venue, city, or country is missing',
                )
        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    CamerataAtCrawler().run()


if __name__ == '__main__':
    main()
