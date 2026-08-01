import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.plzenskafilharmonie.cz'
PROGRAM_URL = f'{BASE_URL}/program/'
SOURCE = 'Plzeňská filharmonie'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

# Most performances take place in Plzeň. These names cover the touring venues
# currently present in the programme and also make common Czech tour stops
# unambiguous without mistaking a venue name for a city.
CITY_MARKERS = {
    'bad elster': 'Bad Elster',
    'beroun': 'Beroun',
    'chotěbuz': 'Chotěbuz',
    'chotebuz': 'Chotěbuz',
    'domažlice': 'Domažlice',
    'domazlice': 'Domažlice',
    'karlovy vary': 'Karlovy Vary',
    'klatovy': 'Klatovy',
    'mariánské lázně': 'Mariánské Lázně',
    'marianske lazne': 'Mariánské Lázně',
    'regensburg': 'Regensburg',
    'rokycany': 'Rokycany',
    'tachov': 'Tachov',
}


def clean_text(value):
    if not value:
        return ''
    text = unescape(str(value)).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_time(value):
    match = re.search(r'\b(\d{1,2}):(\d{2})\b', clean_text(value))
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def infer_city(*values):
    text = clean_text(' '.join(value or '' for value in values)).lower()
    for marker, city in CITY_MARKERS.items():
        if marker in text:
            return city
    return 'Plzeň'


def extract_listing(html):
    soup = BeautifulSoup(html, 'html.parser')
    today = date.today()
    concerts = {}

    for card in soup.select('article.event'):
        title_node = card.select_one('.event__heading')
        time_node = card.select_one('time.event__date[datetime]')
        link = card.select_one('a.event__link[href]')
        if not title_node or not time_node or not link:
            continue

        raw_date = time_node.get('datetime', '').split()[0]
        try:
            event_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            continue
        if event_date < today:
            continue

        title = clean_text(title_node.get_text(' ', strip=True))
        url = urljoin(BASE_URL, link.get('href'))
        venue_node = card.select_one('.event__place')
        subtitle_node = card.select_one('.event__subtitle')
        venue = clean_text(
            venue_node.get_text(' ', strip=True) if venue_node else ''
        )
        subtitle = clean_text(
            subtitle_node.get_text(' ', strip=True) if subtitle_node else ''
        )
        time_from = parse_time(time_node.get_text(' ', strip=True))
        key = (title, event_date.isoformat(), time_from, venue)
        concerts[key] = {
            'title': title,
            'date': event_date.isoformat(),
            'url': url,
            'time_from': time_from,
            'venue': venue or None,
            'city': infer_city(venue, title),
            'country_code': 'CZ',
            'description': subtitle or None,
            'source_url': BASE_URL + '/',
            'source': SOURCE,
        }

    return concerts


def extract_detail(url, fallback):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    title_node = soup.select_one('.detail-intro__heading')
    time_node = soup.select_one('.detail-intro__time')
    venue_node = soup.select_one('.detail-intro__place')
    room_node = soup.select_one('.detail-intro__room')
    info_node = soup.select_one('.detail-info')

    description_parts = []
    for node in (room_node, info_node):
        if not node:
            continue
        clone = BeautifulSoup(str(node), 'html.parser')
        for unwanted in clone.select('script, style, img, h2.detail-info__heading'):
            unwanted.decompose()
        value = clean_text(clone.get_text('\n', strip=True))
        if value and value not in description_parts:
            description_parts.append(value)

    result = {
        'title': clean_text(title_node.get_text(' ', strip=True)) if title_node else None,
        'time_from': parse_time(time_node.get_text(' ', strip=True)) if time_node else None,
        'venue': clean_text(venue_node.get_text(' ', strip=True)) if venue_node else None,
        'description': clean_text('\n\n'.join(description_parts)) or fallback,
    }
    if time_node and time_node.get('datetime'):
        try:
            result['date'] = date.fromisoformat(time_node['datetime'][:10]).isoformat()
        except ValueError:
            pass
    return result


def get_concerts():
    response = requests.get(PROGRAM_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    concerts = extract_listing(response.text)

    details = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(extract_detail, concert['url'], concert['description']): key
            for key, concert in concerts.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                details[key] = future.result()
            except requests.RequestException as exc:
                print(f'Failed to scrape {concerts[key]["url"]}: {exc}')

    for key, concert in concerts.items():
        detail = details.get(key)
        if not detail:
            continue
        for field in ('title', 'date', 'time_from', 'venue', 'description'):
            if detail.get(field):
                concert[field] = detail[field]
        concert['city'] = infer_city(concert['venue'], concert['title'])

    return sorted(
        concerts.values(),
        key=lambda item: (item['date'], item['time_from'] or '', item['title']),
    )


class PlzenskaFilharmonieCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='plzenskafilharmonie_cz',
        source=SOURCE,
        source_url=BASE_URL + '/',
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
    PlzenskaFilharmonieCrawler().run()


if __name__ == '__main__':
    main()
