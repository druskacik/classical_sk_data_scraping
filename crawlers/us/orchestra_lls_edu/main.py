import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Tag

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://orchestra.lls.edu/'
UPCOMING_URL = f'{SOURCE_URL}upcomingconcerts/'
PAST_URL = f'{SOURCE_URL}pastconcerts/'
SOURCE = 'The LLS Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    )
}

DATE_TIME_RE = re.compile(
    r'^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*'
    r'([A-Z][a-z]+ \d{1,2}, \d{4}),\s*(\d{1,2})(?::(\d{2}))?\s*([AP]M)$',
    re.IGNORECASE,
)
HEADING_DATE_RE = re.compile(
    r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|'
    r'Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?' 
    r'\s+\d{1,2},\s+\d{4}',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value):
    value = value.replace('.', '')
    for fmt in ('%B %d, %Y', '%b %d, %Y'):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def content_sections(soup):
    content = soup.select_one('#main-content')
    if not content:
        return []
    sections = []
    heading = None
    nodes = []
    for node in content.children:
        if not isinstance(node, Tag):
            continue
        if node.name == 'h2':
            if heading is not None:
                sections.append((heading, nodes))
            heading = clean_text(node.get_text(' ', strip=True))
            nodes = []
        elif heading is not None:
            nodes.append(node)
    if heading is not None:
        sections.append((heading, nodes))
    return sections


def upcoming_records(soup):
    sections = content_sections(soup)
    if not sections:
        return []

    schedule_nodes = sections[0][1]
    schedule_text = clean_text('\n'.join(node.get_text('\n', strip=True) for node in schedule_nodes))
    venue_match = re.search(r'Venue:\s*([^\n]+)', schedule_text, re.IGNORECASE)
    venue_line = venue_match.group(1) if venue_match else ''
    venue = clean_text(venue_line.split(' - ', 1)[0])
    city_match = re.search(r'[-,]\s*(Los Angeles)\s*,\s*CA\b', venue_line, re.IGNORECASE)
    city = city_match.group(1) if city_match else None
    if not venue or not city:
        return []

    lines = [line for line in schedule_text.splitlines() if clean_text(line)]
    records = []
    current = None
    for line in lines:
        line = clean_text(line)
        match = DATE_TIME_RE.match(line)
        if match:
            if current:
                records.append(current)
            event_date = parse_date(match.group(1))
            if not event_date:
                current = None
                continue
            hour = int(match.group(2)) % 12 + (12 if match.group(4).upper() == 'PM' else 0)
            current = {
                'date': event_date,
                'time_from': f'{hour:02d}:{int(match.group(3) or 0):02d}',
                'program': [],
            }
        elif current and not line.lower().startswith(('upcoming concerts', 'venue:')):
            current['program'].append(line)
    if current:
        records.append(current)

    detail_by_date = {}
    for heading, nodes in sections[1:]:
        date_match = HEADING_DATE_RE.search(heading)
        event_date = parse_date(date_match.group(0)) if date_match else None
        if event_date:
            detail_by_date[event_date] = (
                heading.split('|', 1)[0].strip(),
                clean_text('\n'.join(node.get_text('\n', strip=True) for node in nodes)),
            )

    output = []
    for item in records:
        program = [line for line in item['program'] if line.upper() != 'TBA']
        if not program:
            continue
        detail = detail_by_date.get(item['date'])
        title = detail[0] if detail else f'LLS Orchestra: {program[0]}'
        description_parts = ['Program\n' + '\n'.join(program)]
        if detail and detail[1]:
            description_parts.append(detail[1])
        output.append({
            'title': title,
            'date': item['date'],
            'url': UPCOMING_URL,
            'time_from': item['time_from'],
            'venue': venue,
            'city': city,
            'description': clean_text('\n\n'.join(description_parts)),
        })
    return output


def past_location(heading, description):
    text = f'{heading} {description}'.lower()
    if 'broadstage' in text:
        return 'The BroadStage', 'Santa Monica'
    if 'colburn' in text or 'zipper hall' in text:
        return 'Zipper Hall at the Colburn School', 'Los Angeles'
    if 'loyola law school' in text:
        return 'Loyola Law School', 'Los Angeles'
    return None, None


def past_records(soup):
    records = []
    for heading, nodes in content_sections(soup):
        date_match = HEADING_DATE_RE.search(heading)
        event_date = parse_date(date_match.group(0)) if date_match else None
        description = clean_text('\n'.join(node.get_text('\n', strip=True) for node in nodes))
        venue, city = past_location(heading, description)
        if not event_date or not venue or not city:
            continue
        title_place = heading[:date_match.start()].rstrip(' ,|-')
        records.append({
            'title': f'LLS Orchestra at {title_place or venue}',
            'date': event_date,
            'url': PAST_URL,
            'time_from': None,
            'venue': venue,
            'city': city,
            'description': description or None,
        })
    return records


def homepage_archive_record(soup):
    for heading in soup.select('main h2'):
        text = clean_text(heading.get_text(' ', strip=True))
        if 'Walt Disney Concert Hall' not in text:
            continue
        date_match = HEADING_DATE_RE.search(text)
        event_date = parse_date(date_match.group(0)) if date_match else None
        if event_date:
            return {
                'title': 'LLS Orchestra at Walt Disney Concert Hall',
                'date': event_date,
                'url': SOURCE_URL,
                'time_from': None,
                'venue': 'Walt Disney Concert Hall',
                'city': 'Los Angeles',
                'description': None,
            }
    return None


class OrchestraLlsEduCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='orchestra_lls_edu',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        records = []
        for url, parser in ((UPCOMING_URL, upcoming_records), (PAST_URL, past_records)):
            try:
                records.extend(parser(get_soup(session, url)))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert page',
                    event='crawler_page_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
        try:
            archive = homepage_archive_record(get_soup(session, SOURCE_URL))
            if archive:
                records.append(archive)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape homepage archive',
                event='crawler_page_failed',
                level='warning',
                url=SOURCE_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
        return sorted(records, key=lambda record: (record['date'], record['time_from'] or '', record['title']))

    def transform(self, df):
        # Preserve the optional values as None rather than pandas NaN values.
        return df.astype(object).where(df.notna(), None)


def main():
    OrchestraLlsEduCrawler().run()


if __name__ == '__main__':
    main()
