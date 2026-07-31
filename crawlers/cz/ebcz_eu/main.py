import re
from datetime import datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.ebcz.eu/'
SOURCE = 'Czech Ensemble Baroque'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def clean_text(value):
    if not value:
        return ''
    text = unescape(str(value)).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_date(value):
    """Return the first exact date in a schedule value, including date ranges."""
    value = re.sub(r"(?<!\d)(\d{1,2}\.\s*\d{1,2}\.)\s*[–-]\s*\d{1,2}\.\s*\d{1,2}\.\s*(20\d{2})\b", r"\1 \2", value)
    match = re.search(r"(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\b", value)
    if not match:
        return None
    try:
        parsed = datetime(
            int(match.group(3)),
            int(match.group(2)),
            int(match.group(1)),
        )
    except ValueError:
        return None
    return parsed.strftime('%Y-%m-%d')


def parse_time(value):
    match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', value)
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def extract_title_and_venue(node):
    if not node:
        return None, None

    venue_node = node.select_one('i')
    venue = clean_text(venue_node.get_text(' ', strip=True)) if venue_node else ''

    title_soup = BeautifulSoup(str(node), 'html.parser')
    for tag in title_soup.select('i'):
        tag.decompose()
    title = clean_text(title_soup.get_text(' ', strip=True))
    return title or None, venue or None


def extract_concert(node):
    schedule_node = node.select_one('.termin')
    action_node = node.select_one('.akce')
    city_node = node.select_one('.misto')
    if not schedule_node or not action_node:
        return None

    schedule = clean_text(schedule_node.get_text(' ', strip=True))
    title, venue = extract_title_and_venue(action_node)
    if not title:
        return None

    program_node = node.select_one('.program')
    program = clean_text(program_node.get_text('\n', strip=True)) if program_node else ''

    description_parts = []
    if parse_date(schedule) is None:
        description_parts.append(f'Termín: {schedule}')
    if venue:
        description_parts.append(f'Místo konání: {venue}')
    if program:
        description_parts.append(program)

    return {
        'title': title,
        'date': parse_date(schedule),
        'url': BASE_URL,
        'time_from': parse_time(schedule),
        'venue': venue,
        'city': clean_text(city_node.get_text(' ', strip=True)) or None if city_node else None,
        'country_code': 'CZ',
        'description': clean_text('\n\n'.join(description_parts)) or None,
        'source_url': BASE_URL,
        'source': SOURCE,
    }


def get_concerts():
    response = requests.get(BASE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    # The server omits a charset and requests otherwise assumes ISO-8859-1.
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, 'html.parser')

    concerts = []
    for node in soup.select('.koncert'):
        concert = extract_concert(node)
        if concert:
            concerts.append(concert)
    return concerts


class EbczCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ebcz_eu',
        source=SOURCE,
        source_url=BASE_URL,
        country_code='CZ',
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
        dedupe_subset=['title', 'date', 'city'],
    )

    def scrape(self):
        return get_concerts()


def main():
    EbczCrawler().run()


if __name__ == '__main__':
    main()
