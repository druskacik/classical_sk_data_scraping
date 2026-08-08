import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.kammermusikfest.at/'
SOURCE = 'Kammermusikfest Lockenhaus'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'januar': 1,
    'februar': 2,
    'märz': 3,
    'april': 4,
    'mai': 5,
    'juni': 6,
    'juli': 7,
    'august': 8,
    'september': 9,
    'oktober': 10,
    'november': 11,
    'dezember': 12,
}


def clean_text(element):
    if element is None:
        return ''
    text = element.get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value):
    match = re.search(r'(\d{1,2})\.\s*([A-Za-zÄÖÜäöüß]+)\s+(20\d{2})', value)
    if not match:
        return None
    month = MONTHS.get(match.group(2).lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def parse_location(article):
    address = article.select_one('.eventlist-meta-address')
    if address is None:
        return None

    map_link = address.select_one('a[href*="maps.google.com"]')
    venue = clean_text(address)
    venue = re.sub(r'\s*\(Karte\)\s*$', '', venue).strip()
    if not venue:
        return None

    location_text = map_link.get('href', '') if map_link else ''
    combined = f'{venue} {location_text}'.lower()
    if 'köszeg' in combined or 'k%C3%B6szeg'.lower() in combined:
        return venue, 'Kőszeg', 'HU'
    if 'lockenhaus' in combined:
        return venue, 'Lockenhaus', 'AT'
    return None


def parse_article(article):
    title_link = article.select_one('a.eventlist-title-link[href]')
    title = clean_text(title_link)
    event_date = parse_date(clean_text(article.select_one('.eventlist-meta-date')))
    location = parse_location(article)
    if not title or not title_link or not event_date or not location:
        return None

    times = re.findall(r'\b(?:[01]?\d|2[0-3]):[0-5]\d\b', clean_text(
        article.select_one('.eventlist-meta-time')
    ))
    venue, city, country_code = location
    description = clean_text(article.select_one('.eventlist-excerpt')) or None

    return {
        'title': title,
        'date': event_date,
        'url': urljoin(SOURCE_URL, title_link['href']),
        'time_from': times[0] if times else None,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class KammermusikfestAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='kammermusikfest_at',
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
        try:
            session = requests.Session()
            session.headers.update(HEADERS)
            home_response = session.get(SOURCE_URL, timeout=45)
            home_response.raise_for_status()
            home_soup = BeautifulSoup(home_response.text, 'html.parser')
            programme_link = home_soup.select_one('a[href*="programm-"]')
            if programme_link is None:
                raise ValueError('Could not find the current programme page')
            programme_url = urljoin(SOURCE_URL, programme_link['href'])

            response = session.get(programme_url, timeout=45)
            response.raise_for_status()
        except requests.RequestException as error:
            log_message(
                'Failed to fetch Kammermusikfest programme',
                event='crawler_fetch_failed',
                level='error',
                url=SOURCE_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        soup = BeautifulSoup(response.text, 'html.parser')
        records = []
        for article in soup.select('article.eventlist-event'):
            record = parse_article(article)
            if record:
                records.append(record)

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    KammermusikfestAtCrawler().run()


if __name__ == '__main__':
    main()
