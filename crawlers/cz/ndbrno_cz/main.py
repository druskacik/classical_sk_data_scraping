import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.ndbrno.cz'
PROGRAM_URL = f'{BASE_URL}/program/'
SOURCE = 'Národní divadlo Brno'
CLASSICAL_GENRE_PATHS = ('/opery/', '/balety/', '/koncerty/')

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


def iter_months(start, count=18):
    year = start.year
    month = start.month
    for _ in range(count):
        yield year, month
        month += 1
        if month == 13:
            year += 1
            month = 1


def parse_date(value):
    try:
        return datetime.strptime(clean_text(value), '%d/%m/%Y').date()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2}:\d{2})\b', clean_text(value))
    if not match:
        return None
    hour, minute = match.group(1).split(':')
    return f'{int(hour):02d}:{minute}'


def is_classical_card(card):
    genre_links = card.select('a[href]')
    return any(
        any(path in link.get('href', '') for path in CLASSICAL_GENRE_PATHS)
        for link in genre_links
    )


def extract_description(session, url, fallback):
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return fallback or None

    soup = BeautifulSoup(response.text, 'html.parser')
    content = soup.select_one('#main_content')
    if not content:
        return fallback or None

    for node in content.select(
        'script, style, form, img, .button, section.margin-70, '
        '.socials, .gallery, [class*="gallery"]'
    ):
        node.decompose()
    description = clean_text(content.get_text('\n', strip=True))
    return description or fallback or None


def extract_card(card, today):
    if not is_classical_card(card):
        return None

    title_node = card.select_one('h3')
    detail_link = card.select_one('a.screen[href]') or card.select_one(
        'a[href*="/program/"]'
    )
    list_items = card.select('ul > li')
    date_node = next(
        (item for item in list_items if re.search(r'\d{2}/\d{2}/\d{4}', item.get_text())),
        None,
    )
    if not title_node or not detail_link or not date_node:
        return None

    event_date = parse_date(date_node.get_text(' ', strip=True))
    if not event_date or event_date < today:
        return None

    url = detail_link.get('href')
    if not url:
        return None

    venue_node = card.select_one('a[href*="/budova/"] strong')
    genre_node = next(
        (
            link
            for link in card.select('a[href]')
            if any(path in link.get('href', '') for path in CLASSICAL_GENRE_PATHS)
        ),
        None,
    )
    time_node = next(
        (item for item in list_items if re.search(r'\d{1,2}:\d{2}', item.get_text())),
        None,
    )

    summary_parts = []
    for node in card.find_all(['span', 'strong'], recursive=False):
        text = clean_text(node.get_text(' ', strip=True))
        if text and text not in summary_parts:
            summary_parts.append(text)
    fallback = clean_text('\n'.join(summary_parts))

    venue = clean_text(venue_node.get_text(' ', strip=True)) if venue_node else None
    genre = clean_text(genre_node.get_text(' ', strip=True)) if genre_node else ''
    return {
        'title': clean_text(title_node.get_text(' ', strip=True)),
        'date': event_date.isoformat(),
        'url': url,
        'time_from': parse_time(time_node.get_text(' ', strip=True)) if time_node else None,
        'venue': venue,
        'city': 'Brno',
        'country_code': 'CZ',
        'description': fallback or None,
        'source_url': BASE_URL,
        'source': SOURCE,
        '_genre': genre,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    today = date.today()
    concerts = {}

    for year, month in iter_months(today):
        response = session.get(
            PROGRAM_URL,
            params={'month': month, 'years': year},
            timeout=30,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        for card in soup.select('.row.news.plan > .cl-k'):
            concert = extract_card(card, today)
            if not concert:
                continue
            concert.pop('_genre', None)
            key = (
                concert['title'],
                concert['date'],
                concert['time_from'],
                concert['venue'],
            )
            concerts[key] = concert

    details = {}
    fallbacks = {
        concert['url']: concert['description'] for concert in concerts.values()
    }
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(extract_description, session, url, fallback): url
            for url, fallback in fallbacks.items()
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                details[url] = future.result()
            except requests.RequestException:
                details[url] = fallbacks[url]

    for concert in concerts.values():
        concert['description'] = details.get(
            concert['url'], concert['description']
        )

    return sorted(
        concerts.values(),
        key=lambda item: (item['date'], item['time_from'] or '', item['title']),
    )


class NdBrnoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ndbrno_cz',
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
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    concerts = NdBrnoCrawler().scrape()
    print(f'Found {len(concerts)} concerts')
    for concert in concerts:
        print(concert)


if __name__ == '__main__':
    main()
