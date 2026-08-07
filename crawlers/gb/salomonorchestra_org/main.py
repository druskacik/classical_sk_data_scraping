import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.salomonorchestra.org/'
CONCERTS_URL = f'{SOURCE_URL}concerts'
SOURCE = 'Salomon Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

DATE_RE = re.compile(
    r'^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+'
    r'(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]+)\s+(\d{4})'
    r'\s*,\s*(\d{1,2})[.:](\d{2})\s*(am|pm)$',
    re.IGNORECASE,
)
def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date_time(value):
    match = DATE_RE.match(clean_text(value))
    if not match:
        return None, None
    try:
        event_date = datetime.strptime(
            f'{match.group(1)} {match.group(2)} {match.group(3)}', '%d %B %Y'
        ).date().isoformat()
        event_time = datetime.strptime(
            f'{match.group(4)}:{match.group(5)} {match.group(6)}', '%I:%M %p'
        ).strftime('%H:%M')
    except ValueError:
        return None, None
    return event_date, event_time


def parse_location(value):
    text = re.sub(r'^at\s+', '', clean_text(value), flags=re.IGNORECASE)
    if not text or 'London' not in text:
        return None, None
    # Location lines are "Venue, [street,] London POSTCODE". Everything from
    # the first comma is address/city data and must not leak into the venue.
    venue = text.split(',', 1)[0].strip()
    if not venue or venue.casefold() == 'london':
        return None, None
    return venue, 'London'


def event_title(lines):
    composers = []
    for line in lines:
        if ' - ' not in line:
            continue
        name = line.split(' - ', 1)[0].strip()
        if name.casefold() in {'conductor', 'soloist', 'choir'} or not name:
            continue
        if name not in composers:
            composers.append(name)
    if composers:
        return f'{SOURCE}: {", ".join(composers)}'
    return f'{SOURCE} concert'


def programme_sections(soup):
    for container in soup.select('[data-testid="richTextElement"]'):
        paragraphs = [clean_text(item) for item in container.find_all('p')]
        paragraphs = [item for item in paragraphs if item]
        if not any(DATE_RE.match(item) for item in paragraphs):
            continue
        section = []
        for paragraph in paragraphs:
            if DATE_RE.match(paragraph):
                if section:
                    yield section
                section = [paragraph]
            elif section:
                section.append(paragraph)
        if section:
            yield section


def parse_section(lines):
    event_date, event_time = parse_date_time(lines[0])
    location = next((line for line in lines[1:] if line.lower().startswith('at ')), '')
    venue, city = parse_location(location)
    if not event_date or not venue or not city:
        return None
    detail_lines = [line for line in lines[1:] if line != location]
    return {
        'title': event_title(detail_lines),
        'date': event_date,
        'url': CONCERTS_URL,
        'time_from': event_time,
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': '\n'.join(detail_lines) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    try:
        response = requests.get(CONCERTS_URL, headers=HEADERS, timeout=45)
        response.raise_for_status()
    except requests.RequestException as error:
        log_message(
            'Failed to scrape concert schedule',
            event='crawler_page_failed',
            level='error',
            url=CONCERTS_URL,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        raise

    soup = BeautifulSoup(response.content, 'html.parser')
    records = [parse_section(section) for section in programme_sections(soup)]
    records = [record for record in records if record]
    return sorted(records, key=lambda row: (row['date'], row['time_from'] or '', row['title']))


class SalomonOrchestraOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='salomonorchestra_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    SalomonOrchestraOrgCrawler().run()


if __name__ == '__main__':
    main()
