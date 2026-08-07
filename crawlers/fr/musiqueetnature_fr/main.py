import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.musiqueetnature.fr/'
PROGRAMME_URL = f'{SOURCE_URL}programme-musique-bauges-nature-festival/'
SOURCE = 'Festival Musique et Nature en Bauges'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.6',
}

MONTHS = {
    'janvier': 1,
    'fevrier': 2,
    'mars': 3,
    'avril': 4,
    'mai': 5,
    'juin': 6,
    'juillet': 7,
    'aout': 8,
    'septembre': 9,
    'octobre': 10,
    'novembre': 11,
    'decembre': 12,
}

# The programme names churches and historic buildings rather than giving full
# addresses. These are the localities represented by the venue labels used by
# the festival. Keep the venue itself intact in the output.
VENUE_CITIES = {
    "chartreuse d'aillon": 'Aillon-le-Jeune',
    'eglise de viuz-la-chiesaz': 'Viuz-la-Chiésaz',
    'ferme de gy a giez': 'Giez',
    "eglise saint-laurent d'annecy-le-vieux": 'Annecy',
    'eglise de doucy-en-bauges': 'Doucy-en-Bauges',
    'eglise de talloires': 'Talloires-Montmin',
    "eglise d'albens": 'Albens',
    'eglise de bloye': 'Bloye',
    'eglise de gresy-sur-aix': 'Grésy-sur-Aix',
    "eglise d'arith": 'Arith',
    'eglise de bellecombe-en-bauges': 'Bellecombe-en-Bauges',
    'eglise du chatelard': 'Le Châtelard',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def normalized(value):
    value = clean_text(value).lower().replace('’', "'")
    return ''.join(
        character
        for character in unicodedata.normalize('NFKD', value)
        if not unicodedata.combining(character)
    )


def parse_year(soup):
    header = soup.select_one('header .date, body > .date, .date')
    match = re.search(r'\b(20\d{2})\b', clean_text(header)) if header else None
    if not match:
        match = re.search(r'FestivalMusiqueNature(20\d{2})', str(soup))
    return int(match.group(1)) if match else None


def parse_date(value, year):
    match = re.search(r'\b(\d{1,2})\s+([a-zA-ZÀ-ÿ]+)\b', clean_text(value))
    if not match or not year:
        return None
    month = MONTHS.get(normalized(match.group(2)))
    if not month:
        return None
    try:
        return date(year, month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2})\s*h\s*(\d{2})\b', clean_text(value), re.IGNORECASE)
    if not match:
        return None
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def resolve_city(venue):
    key = normalized(venue)
    if key in VENUE_CITIES:
        return VENUE_CITIES[key]

    match = re.search(r"\b(?:a|de|d')\s*([A-ZÀ-ÖØ-Þ][\wÀ-ÿ'’ -]+)$", venue)
    return clean_text(match.group(1)).strip(' .') if match else None


def detail_description(session, item):
    response = session.get(item['url'], timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    parts = []
    header = soup.select_one('h1 + div, h1 + section')
    if header:
        header_text = clean_text(header)
        performer_text = clean_text(item.get('performers'))
        if performer_text and performer_text in header_text:
            parts.append(performer_text)

    article = soup.select_one('#content article, main article')
    article_text = clean_text(article)
    if article_text:
        parts.append(article_text)
    if not parts and item.get('performers'):
        parts.append(item['performers'])
    return '\n\n'.join(dict.fromkeys(parts)) or None


def listing_items(session):
    response = session.get(PROGRAMME_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    year = parse_year(soup)
    items = []

    for node in soup.select('.events .event'):
        title_link = node.select_one('h3 a[href]')
        date_node = node.select_one('p.date')
        venue_node = node.select_one('p.lieu')
        title = clean_text(title_link)
        url = title_link.get('href', '').strip() if title_link else ''
        venue = clean_text(venue_node)
        city = resolve_city(venue)
        event_date = parse_date(date_node, year)
        if not all((title, event_date, url, venue, city)):
            log_message(
                'Skipping event with incomplete required fields',
                event='crawler_item_skipped',
                level='warning',
                url=url or PROGRAMME_URL,
            )
            continue

        info_paragraphs = node.select('.text > .infos p:not(.date):not(.lieu)')
        items.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': parse_time(date_node),
            'venue': venue,
            'city': city,
            'performers': clean_text(info_paragraphs[-1]) if info_paragraphs else '',
        })
    return items


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = listing_items(session)

    descriptions = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(detail_description, session, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                descriptions[item['url']] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                descriptions[item['url']] = item['performers'] or None

    records = []
    for item in items:
        item.pop('performers', None)
        item.update({
            'country_code': 'FR',
            'description': descriptions.get(item['url']),
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
        records.append(item)
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class MusiqueEtNatureFrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='musiqueetnature_fr',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
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
        return get_concerts()


def main():
    MusiqueEtNatureFrCrawler().run()


if __name__ == '__main__':
    main()
