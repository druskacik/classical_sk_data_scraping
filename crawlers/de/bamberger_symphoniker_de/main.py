from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.bamberger-symphoniker.de/'
CALENDAR_URL = urljoin(SOURCE_URL, 'programm-tickets/konzertuebersicht.html')
SOURCE = 'Bamberger Symphoniker'
LOCAL_TIMEZONE = ZoneInfo('Europe/Berlin')

# Older API rows have no title or detail URL; 2007 is the first year containing
# complete, scrapeable concert records. One extra year includes the next season.
FIRST_ARCHIVE_YEAR = 2007
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}


def fetch_month(year, month):
    response = requests.get(
        CALENDAR_URL,
        params={
            'type': '42534537567',
            'tx_pxconcert_konzertkalender[action]': 'getConcertsByFilter',
            'tx_pxconcert_konzertkalender[controller]': 'ConcertCalendar',
            'tx_pxconcert_konzertkalender[catUid]': '0',
            'tx_pxconcert_konzertkalender[month]': str(month),
            'tx_pxconcert_konzertkalender[year]': str(year),
            'tx_pxconcert_konzertkalender[langID]': '0',
        },
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError('Calendar API returned a non-list response')
    return payload


def parse_place(value):
    """The feed consistently puts the city first, then the venue."""
    place = ' '.join(str(value or '').split())
    if not place:
        return None, None
    if ',' in place:
        city, venue = place.split(',', 1)
    elif ' - ' in place:
        city, venue = place.split(' - ', 1)
    else:
        return None, None
    city, venue = city.strip(), venue.strip(' ,-')
    return (city, venue) if city and venue else (None, None)


def build_description(event):
    parts = []
    people = []
    for person in event.get('person') or []:
        name = ' '.join(
            value.strip()
            for value in (person.get('vorname', ''), person.get('nachname', ''))
            if value and value.strip()
        )
        instrument = str(person.get('instrument') or '').strip()
        if name:
            people.append(f'{name} — {instrument}' if instrument else name)
    if people:
        parts.append('Mitwirkende:\n' + '\n'.join(people))

    works = []
    for work in event.get('werk') or []:
        composer = ' '.join(
            str(value).strip()
            for value in (
                work.get('komponistvorname', ''),
                work.get('komponistnachname', ''),
            )
            if value and str(value).strip()
        )
        work_name = str(work.get('werkname') or '').strip()
        line = ': '.join(value for value in (composer, work_name) if value)
        if line:
            works.append(line)
    if works:
        parts.append('Programm:\n' + '\n'.join(works))
    return '\n\n'.join(parts) or None


def parse_event(event):
    title = ' '.join(str(event.get('title') or '').split())
    detail_link = str(event.get('detailLink') or '').strip()
    city, venue = parse_place(event.get('place'))
    timestamp = event.get('date')
    if not title or not detail_link or not city or not venue or not timestamp:
        return None
    try:
        starts_at = datetime.fromtimestamp(int(timestamp), tz=LOCAL_TIMEZONE)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return {
        'title': title,
        'date': starts_at.date().isoformat(),
        'url': urljoin(SOURCE_URL, detail_link),
        'time_from': None if event.get('hidetime') else starts_at.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'DE',
        'description': build_description(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    current_year = datetime.now(LOCAL_TIMEZONE).year
    months = [
        (year, month)
        for year in range(FIRST_ARCHIVE_YEAR, current_year + 2)
        for month in range(1, 13)
    ]
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(fetch_month, year, month): (year, month)
            for year, month in months
        }
        for future in as_completed(futures):
            year, month = futures[future]
            try:
                events = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to fetch calendar month',
                    event='crawler_page_failed',
                    level='warning',
                    url=CALENDAR_URL,
                    year=year,
                    month=month,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            records.extend(record for event in events if (record := parse_event(event)))

    unique = {record['url']: record for record in records}
    return sorted(
        unique.values(),
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class BambergerSymphonikerDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bamberger_symphoniker_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    BambergerSymphonikerDeCrawler().run()


if __name__ == '__main__':
    main()
