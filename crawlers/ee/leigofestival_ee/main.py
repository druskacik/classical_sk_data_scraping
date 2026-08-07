import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://leigofestival.ee/'
PROGRAM_URL = urljoin(SOURCE_URL, 'kava-ja-paketid/')
SOURCE = 'Leigo Järvemuusika festival'
VENUE = 'Leigo talu'
CITY = 'Palupera'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'et-EE,et;q=0.9,en;q=0.7',
}

MONTHS = {
    'jaanuar': 1,
    'veebruar': 2,
    'märts': 3,
    'aprill': 4,
    'mai': 5,
    'juuni': 6,
    'juuli': 7,
    'august': 8,
    'september': 9,
    'oktoober': 10,
    'november': 11,
    'detsember': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def node_text(parent, selector, separator=' '):
    node = parent.select_one(selector)
    return clean_text(node.get_text(separator, strip=True)) if node else ''


def page_year(soup):
    hero = node_text(soup, '.page-hero__eyebrow')
    match = re.search(r'\b(20\d{2})\b', hero)
    return int(match.group(1)) if match else None


def section_date(section, year):
    day_text = node_text(section, '.day-section__num')
    month_text = node_text(section, '.day-section__month').lower()
    if not year or not day_text or month_text not in MONTHS:
        return None
    try:
        return date(year, MONTHS[month_text], int(day_text)).isoformat()
    except ValueError:
        return None


def start_time(value):
    match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', clean_text(value))
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def feature_descriptions(section):
    descriptions = {}
    for feature in section.select('.event-feature[id]'):
        description = node_text(feature, '.event-feature__desc', separator='\n')
        extra_times = node_text(feature, '.event-feature__times', separator='\n')
        descriptions[feature.get('id')] = clean_text(
            '\n\n'.join(part for part in (description, extra_times) if part)
        ) or None
    return descriptions


def parse_program(soup):
    year = page_year(soup)
    records = []
    for section in soup.select('section.day-section'):
        event_date = section_date(section, year)
        if not event_date:
            continue
        descriptions = feature_descriptions(section)
        section_id = section.get('id') or ''

        for item in section.select('.program > .program__item'):
            title = node_text(item, '.program__name')
            time_from = start_time(node_text(item, '.program__time'))
            href = item.get('href') if item.name == 'a' else None
            url = urljoin(PROGRAM_URL, href) if href else f'{PROGRAM_URL}#{section_id}'
            feature_id = href[1:] if href and href.startswith('#') else None
            note = node_text(item, '.program__note', separator='\n')
            detail = descriptions.get(feature_id)
            description = clean_text('\n\n'.join(part for part in (note, detail) if part)) or None

            if not title or not time_from or not url:
                continue
            records.append(
                {
                    'title': title,
                    'date': event_date,
                    'url': url,
                    'time_from': time_from,
                    'venue': VENUE,
                    'city': CITY,
                    'country_code': 'EE',
                    'description': description,
                    'source_url': SOURCE_URL,
                    'source': SOURCE,
                }
            )
    return records


def get_concerts():
    response = requests.get(PROGRAM_URL, headers=HEADERS, timeout=30)
    try:
        response.raise_for_status()
    except requests.RequestException as error:
        log_message(
            'Failed to fetch Leigo festival programme',
            event='crawler_fetch_failed',
            level='error',
            url=PROGRAM_URL,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        raise
    return sorted(
        parse_program(BeautifulSoup(response.text, 'html.parser')),
        key=lambda record: (record['date'], record['time_from'], record['title']),
    )


class LeigofestivalEeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='leigofestival_ee',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='EE',
        upload_target='potential',
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
    LeigofestivalEeCrawler().run()


if __name__ == '__main__':
    main()
