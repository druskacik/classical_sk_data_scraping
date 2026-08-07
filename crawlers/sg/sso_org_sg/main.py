import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.sso.org.sg/'
SOURCE = 'Singapore Symphony Orchestra'
API_URL = 'https://web-api.sso.org.sg/gql'

# Public read-only token used by the website's browser GraphQL client.
API_TOKEN = 'HGNBA9SY7kUTf4CyGQcGqQO9HxPivM9O'

HEADERS = {
    'Authorization': f'Bearer {API_TOKEN}',
    'Content-Type': 'application/json',
    'Origin': SOURCE_URL.rstrip('/'),
    'Referer': SOURCE_URL,
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
}

CALENDAR_QUERY = '''
query CrawlerCalendar {
  yearlyCalendar {
    events {
      ... on events_events_Entry {
        title
        uri
        eventSchedule(site: "default") {
          ... on eventSchedule_BlockType {
            start: startDateTime @formatDateTime(
              format: "Y-m-d H:i", timezone: "Asia/Singapore"
            )
            venue { title }
          }
        }
        eventProgramme(site: "default") {
          ... on eventProgramme_intermission_BlockType { typeHandle }
          ... on eventProgramme_musicalWork_BlockType {
            typeHandle
            workTitle
            composers { title }
            composerNote
            highlightLabel
          }
        }
        performers {
          ... on performers_BlockType {
            performers { title }
            role
          }
        }
      }
    }
  }
}
'''

CHINA_VENUES = {
    'Changsha Concert Hall': 'Changsha',
    'Guangzhou Xinghai Concert Hall': 'Guangzhou',
    'Jaguar Shanghai Symphony Hall': 'Shanghai',
    'National Centre for the Performing Arts (Beijing)': 'Beijing',
    'Shenzhen Concert Hall': 'Shenzhen',
    'Wuhan Qintai Concert Hall': 'Wuhan',
    'Xiamen Banlam Grand Theater': 'Xiamen',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_calendar(session):
    response = session.post(API_URL, json={'query': CALENDAR_QUERY}, timeout=90)
    response.raise_for_status()
    payload = response.json()
    if payload.get('errors'):
        raise ValueError(f'GraphQL returned {len(payload["errors"])} error(s)')
    return (payload.get('data') or {}).get('yearlyCalendar') or []


def resolve_location(venue):
    if venue in CHINA_VENUES:
        return CHINA_VENUES[venue], 'CN'
    # Apart from the explicitly named China-tour halls, the calendar is an
    # institutional Singapore programme and its venue names identify local
    # halls and spaces.
    return 'Singapore', 'SG'


def programme_description(event):
    parts = []
    performers = []
    for block in event.get('performers') or []:
        role = clean_text(block.get('role'))
        for performer in block.get('performers') or []:
            name = clean_text(performer.get('title'))
            if name:
                performers.append(f'{name} — {role}' if role else name)
    if performers:
        parts.append('Performers\n' + '\n'.join(dict.fromkeys(performers)))

    works = []
    for block in event.get('eventProgramme') or []:
        if block.get('typeHandle') == 'intermission':
            works.append('Intermission')
            continue
        title = clean_text(block.get('workTitle'))
        composers = [clean_text(item.get('title')) for item in block.get('composers') or []]
        composers = [item for item in composers if item]
        if title:
            work = f'{", ".join(composers)}: {title}' if composers else title
            note = clean_text(block.get('composerNote'))
            highlight = clean_text(block.get('highlightLabel'))
            suffix = '; '.join(item for item in (highlight, note) if item)
            works.append(f'{work} ({suffix})' if suffix else work)
    if works:
        parts.append('Programme\n' + '\n'.join(works))
    return '\n\n'.join(parts) or None


def event_records(event):
    title = clean_text(event.get('title'))
    uri = clean_text(event.get('uri'))
    if not title or not uri:
        return []
    url = urljoin(SOURCE_URL, uri)
    description = programme_description(event)
    records = []
    for occurrence in event.get('eventSchedule') or []:
        venue_items = occurrence.get('venue') or []
        venue = clean_text(venue_items[0].get('title')) if venue_items else ''
        if not venue:
            continue
        try:
            start = datetime.strptime(occurrence.get('start') or '', '%Y-%m-%d %H:%M')
            event_date = date(start.year, start.month, start.day).isoformat()
        except (TypeError, ValueError):
            continue
        city, country_code = resolve_location(venue)
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': start.strftime('%H:%M'),
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        calendar = fetch_calendar(session)
    except (requests.RequestException, ValueError) as error:
        log_message(
            'Failed to fetch Singapore Symphony Orchestra calendar',
            event='crawler_fetch_failed', level='error', url=API_URL,
            error_type=type(error).__name__, error_message=str(error),
        )
        raise

    records = []
    seen_events = set()
    for day in calendar:
        for event in day.get('events') or []:
            # yearlyCalendar repeats the complete event for each date on which
            # it occurs, while eventSchedule itself contains all occurrences.
            event_key = event.get('uri') or event.get('title')
            if not event_key or event_key in seen_events:
                continue
            seen_events.add(event_key)
            records.extend(event_records(event))
    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class SsoOrgSgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='sso_org_sg',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='SG',
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
    SsoOrgSgCrawler().run()


if __name__ == '__main__':
    main()
