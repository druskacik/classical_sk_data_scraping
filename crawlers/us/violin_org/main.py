import re
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://violin.org/'
SOURCE = 'International Violin Competition of Indianapolis'
API_URL = 'https://violin.org/wp-json/wp/v2/pages'
SCHEDULE_SLUGS = ('event-schedule-2026', 'event-schedule-2022', 'event-schedule-2018')
CITY = 'Indianapolis'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
}

VENUES = (
    'Eugene and Marilyn Glick Indiana History Center',
    'Frank and Katrina Basile Theater',
    'Schrott Center for the Arts at Butler University',
    'Hilbert Circle Theatre',
    'Scottish Rite Cathedral Theater',
    'Indianapolis Central Library',
    'Newfields',
)
VENUE_ALIASES = (
    ('Howard L. Schrott Center for the Arts', 'Schrott Center for the Arts at Butler University'),
    ('Scottish Rite Cathedral', 'Scottish Rite Cathedral Theater'),
)
MONTHS = {
    month.lower(): number
    for number, month in enumerate(
        ('', 'January', 'February', 'March', 'April', 'May', 'June',
         'July', 'August', 'September', 'October', 'November', 'December')
    )
    if month
}
DATE_PART = r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})'
TIME_RE = re.compile(r'(?<!\d)(\d{1,2}):?(\d{2})?\s*([AP])\.?M\.?', re.I)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_time(value):
    match = TIME_RE.search(value or '')
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).upper() == 'P':
        hour += 12
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def date_span(value, year):
    matches = list(re.finditer(DATE_PART, value, re.I))
    if not matches:
        return []
    dates = []
    for match in matches[:2]:
        try:
            dates.append(date(year, MONTHS[match.group(1).lower()], int(match.group(2))))
        except ValueError:
            return []
    if len(dates) == 1:
        short_range = re.search(r'\b\d{1,2}\s*[-–]\s*(\d{1,2})\b', value)
        if short_range:
            try:
                dates.append(date(year, dates[0].month, int(short_range.group(1))))
            except ValueError:
                return []
        else:
            return dates
    if dates[1] < dates[0] or (dates[1] - dates[0]).days > 31:
        return []
    return [dates[0] + timedelta(days=offset) for offset in range((dates[1] - dates[0]).days + 1)]


def venue_from_text(value):
    for marker, venue in VENUE_ALIASES:
        if marker.lower() in value.lower():
            return venue
    for venue in VENUES:
        if venue.lower() in value.lower():
            # Basile Theater is the actual room when both it and its parent
            # History Center appear in a schedule entry.
            if venue == 'Eugene and Marilyn Glick Indiana History Center' and \
                    'Frank and Katrina Basile Theater'.lower() in value.lower():
                continue
            return venue
    return None


def schedule_rows(html):
    soup = BeautifulSoup(html, 'html.parser')
    for row in soup.select('.row'):
        columns = row.find_all('div', class_=lambda value: value and 'col' in value.split(), recursive=False)
        if len(columns) < 2:
            continue
        date_text = clean_text(columns[0].get_text(' ', strip=True))
        if re.search(DATE_PART, date_text, re.I):
            yield date_text, columns[1]


def legacy_table_records(html, year, page_url):
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for row in soup.select('tr'):
        cells = row.find_all(['td', 'th'], recursive=False)
        if len(cells) != 2:
            continue
        date_text = clean_text(cells[0].get_text(' ', strip=True))
        description = clean_text(cells[1].decode_contents())
        lower = description.lower()
        title = next((name for marker, name in (
            ('opening ceremonies', 'Opening Ceremonies'),
            ('preliminaries', 'Preliminaries'),
            ('semi-finals', 'Semi-Finals'),
            ('donor appreciation concert', 'Donor Appreciation Concert'),
            ('classical finals', 'Classical Finals'),
            ('lifestructures finals', 'Finals'),
            ('gala awards ceremony', 'Gala Awards Ceremony and Reception'),
        ) if marker in lower), None)
        venue = venue_from_text(description)
        dates = date_span(date_text, year)
        if not title or not venue or not dates:
            continue
        values = [parse_time(match.group(0)) for match in TIME_RE.finditer(date_text)]
        starts = [value for value in values[::2] if value] or [None]
        for event_date in dates:
            for start in starts:
                records.append({
                    'title': title,
                    'date': event_date.isoformat(),
                    'url': page_url,
                    'time_from': start,
                    'venue': venue,
                    'city': CITY,
                    'country_code': 'US',
                    'description': description or None,
                })
    return records


def row_records(date_text, body, year, page_url):
    description = clean_text(body.decode_contents())
    lines = [clean_text(line) for line in body.stripped_strings]
    lines = [line for line in lines if line]
    title = lines[0] if lines else ''
    venue = venue_from_text(description)
    dates = date_span(date_text, year)
    if not title or not venue or not dates:
        return []

    # Some preliminary-round rows publish a separate timetable for each day.
    day_entries = re.findall(
        r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+(?:Sept(?:ember)?|Oct(?:ober)?)\s+(\d{1,2})'
        r'(.*?)(?=(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+(?:Sept(?:ember)?|Oct(?:ober)?)\s+\d{1,2}|$)',
        description.replace('\n', ' '), re.I,
    )
    sessions = []
    if day_entries:
        date_lookup = {(item.month, item.day): item for item in dates}
        month = dates[0].month
        for day, timetable in day_entries:
            event_date = date_lookup.get((month, int(day)))
            if not event_date or 'no recital' in timetable.lower():
                continue
            starts = [parse_time(match.group(0)) for match in TIME_RE.finditer(timetable)]
            # Time ranges alternate start/end; only their starts are sessions.
            sessions.extend((event_date, value) for value in starts[::2] if value)
    else:
        time_lines = [line for line in lines if TIME_RE.search(line)]
        starts = []
        for line in time_lines:
            values = [parse_time(match.group(0)) for match in TIME_RE.finditer(line)]
            starts.extend(value for value in values[::2] if value)
        starts = list(dict.fromkeys(starts)) or [None]
        sessions = [(event_date, start) for event_date in dates for start in starts]

    return [{
        'title': title,
        'date': event_date.isoformat(),
        'url': page_url,
        'time_from': start,
        'venue': venue,
        'city': CITY,
        'country_code': 'US',
        'description': description or None,
    } for event_date, start in sessions]


def fetch_page(session, slug):
    response = session.get(API_URL, params={'slug': slug, 'context': 'view'}, timeout=45)
    response.raise_for_status()
    pages = response.json()
    return pages[0] if pages else None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    for slug in SCHEDULE_SLUGS:
        try:
            page = fetch_page(session, slug)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch IVCI schedule',
                event='crawler_page_failed',
                level='warning',
                url=f'{SOURCE_URL}{slug}/',
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        if not page:
            continue
        year_match = re.search(r'20\d{2}', slug)
        if not year_match:
            continue
        page_url = page.get('link') or f'{SOURCE_URL}{slug}/'
        html = (page.get('content') or {}).get('rendered', '')
        for date_text, body in schedule_rows(html):
            records.extend(row_records(date_text, body, int(year_match.group()), page_url))
        records.extend(legacy_table_records(html, int(year_match.group()), page_url))

    unique = {(item['title'], item['date'], item['time_from'], item['venue']): item for item in records}
    return sorted(unique.values(), key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class ViolinOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='violin_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    ViolinOrgCrawler().run()


if __name__ == '__main__':
    main()
