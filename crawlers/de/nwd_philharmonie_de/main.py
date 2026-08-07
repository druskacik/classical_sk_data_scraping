import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.nwd-philharmonie.de/'
PROGRAM_URL = urljoin(SOURCE_URL, 'konzerte-und-tickets/')
SOURCE = 'Nordwestdeutsche Philharmonie'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}

# The orchestra tours outside Germany. These are the foreign cities appearing
# in the calendar archives; all other listed places are German.
FOREIGN_CITY_COUNTRIES = {
    'Amsterdam': 'NL',
    'Antwerpen': 'BE',
    'Haarlem': 'NL',
    'Heerlen': 'NL',
    'Lissabon': 'PT',
    'Mailand': 'IT',
    'Torres Vedras': 'PT',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_page(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.text


def archive_urls(html):
    soup = BeautifulSoup(html, 'html.parser')
    urls = {PROGRAM_URL}
    for link in soup.select('a[href*="zeitraum="]'):
        href = urljoin(PROGRAM_URL, link.get('href', ''))
        if re.search(r'zeitraum=\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}', href):
            urls.add(href)
    return sorted(urls)


def parse_location(card):
    location = card.select_one('.col1 > .ort') or card.select_one('.col1-mobile > .ort')
    if not location:
        return None, None
    parts = [clean_text(part) for part in location.stripped_strings]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return None, None
    return parts[0], ' – '.join(parts[1:])


def parse_card(card):
    date_node = card.select_one('.col1 > .datum') or card.select_one('.col1-mobile > .datum')
    time_node = card.select_one('.col1 > .uhrzeit') or card.select_one('.col1-mobile > .uhrzeit')
    link = card.select_one('a.detailbutton[href]')
    title = clean_text(card.select_one('.titel'))
    subtitle = clean_text(card.select_one('.untertitel'))
    city, venue = parse_location(card)

    date_match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', clean_text(date_node))
    time_match = re.search(r'(\d{1,2}):(\d{2})', clean_text(time_node))
    if not title or not date_match or not link or not city or not venue:
        return None
    try:
        event_date = date(
            int(date_match.group(3)), int(date_match.group(2)), int(date_match.group(1))
        ).isoformat()
    except ValueError:
        return None

    if subtitle and subtitle.casefold() not in title.casefold():
        title = f'{title} – {subtitle}'
    description = clean_text(card.select_one('.beschreibung-wrapper')) or None

    return {
        'title': title,
        'date': event_date,
        'url': urljoin(SOURCE_URL, link['href']),
        'time_from': (
            f'{int(time_match.group(1)):02d}:{time_match.group(2)}' if time_match else None
        ),
        'venue': venue,
        'city': city,
        'country_code': FOREIGN_CITY_COUNTRIES.get(city, 'DE'),
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def parse_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for card in soup.select('.nwd-programm-termin'):
        record = parse_card(card)
        if record:
            records.append(record)
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    first_page = get_page(session, PROGRAM_URL)
    urls = archive_urls(first_page)
    records = parse_page(first_page)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(get_page, session, url): url for url in urls if url != PROGRAM_URL}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_page(future.result()))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert archive',
                    event='crawler_page_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    unique = {
        (record['url'], record['date'], record['time_from'], record['city'], record['venue']): record
        for record in records
    }
    return sorted(
        unique.values(),
        key=lambda record: (
            record['date'], record['time_from'] or '', record['city'], record['title']
        ),
    )


class NwdPhilharmonieDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='nwd_philharmonie_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
        dedupe_subset=['url', 'date', 'time_from', 'city', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    NwdPhilharmonieDeCrawler().run()


if __name__ == '__main__':
    main()
