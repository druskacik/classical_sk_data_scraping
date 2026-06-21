import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.cnso.cz'
PROGRAM_URL = f'{BASE_URL}/program'
SOURCE_NAME = 'Český národní symfonický orchestr'
SOURCE_URL = BASE_URL
DEFAULT_CITY = 'Praha'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

CZECH_MONTHS = {
    'leden': 1,
    'ledna': 1,
    'únor': 2,
    'února': 2,
    'unor': 2,
    'unora': 2,
    'březen': 3,
    'března': 3,
    'brezen': 3,
    'brezna': 3,
    'duben': 4,
    'dubna': 4,
    'květen': 5,
    'května': 5,
    'kveten': 5,
    'kvetna': 5,
    'červen': 6,
    'června': 6,
    'cerven': 6,
    'cervna': 6,
    'červenec': 7,
    'července': 7,
    'cervenec': 7,
    'cervence': 7,
    'srpen': 8,
    'srpna': 8,
    'září': 9,
    'zari': 9,
    'říjen': 10,
    'října': 10,
    'rijen': 10,
    'rijna': 10,
    'listopad': 11,
    'listopadu': 11,
    'prosinec': 12,
    'prosince': 12,
}

COUNTRY_WORDS = {
    'česko',
    'česká republika',
    'německo',
    'nemecko',
    'nizozemsko',
    'rakousko',
    'slovensko',
}


def clean_text(text):
    if not text:
        return ''
    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_czech_datetime(text):
    text = clean_text(text).lower()
    match = re.search(
        r'\b(\d{1,2})\.\s*([a-záéíóúýčďěňřšťůž]+)\s*(20\d{2})(?:,\s*(\d{1,2})[:.](\d{2}))?',
        text,
    )
    if not match:
        return None, None

    month = CZECH_MONTHS.get(match.group(2))
    if not month:
        return None, None

    start = datetime(int(match.group(3)), month, int(match.group(1)))
    date = start.date().isoformat()
    if not match.group(4):
        return date, None

    return date, f'{int(match.group(4)):02d}:{int(match.group(5)):02d}'


def parse_card_datetime(lines):
    if len(lines) < 3:
        return None, None
    date_text = f'{lines[0]}. {lines[1]} {lines[2]}'
    time_text = next((line for line in lines[3:] if re.fullmatch(r'\d{1,2}[:.]\d{2}', line)), '')
    return parse_czech_datetime(clean_text(f'{date_text}, {time_text}'))


def split_place(place):
    place = clean_text(place)
    if not place:
        return None, DEFAULT_CITY

    parts = [clean_text(part) for part in place.split(',') if clean_text(part)]
    if not parts:
        return None, DEFAULT_CITY

    city = DEFAULT_CITY
    venue_parts = parts

    if len(parts) >= 2:
        last = parts[-1].lower()
        if last in COUNTRY_WORDS and len(parts) >= 3:
            city = parts[-2]
            venue_parts = parts[:-2]
            if re.search(r'\b(louka|sál|sal|budova|scéna|scena)\b', city, re.IGNORECASE):
                city_match = re.search(r'\b(Jerichow|Salzwedel|Hamburk|Stendal)\b', parts[0], re.IGNORECASE)
                if city_match:
                    city = city_match.group(1)
                    venue_parts = parts[:-1]
        elif last in COUNTRY_WORDS:
            venue_parts = parts[:-1]
        elif re.search(r'\b(Praha|Polička|České Budějovice|Hamburk|Stendal|Salzwedel|Jerichow)\b', parts[-1], re.IGNORECASE):
            city = parts[-1]
            venue_parts = parts[:-1]

    venue = clean_text(', '.join(venue_parts)) or place
    return venue, city


def listing_links(session):
    soup = get_soup(session, PROGRAM_URL)
    links = []
    for card in soup.select('a.card-a[href]'):
        href = card.get('href')
        if not href:
            continue
        links.append(urljoin(BASE_URL, href))
    return list(dict.fromkeys(links))


def card_fallback(card):
    lines = [clean_text(line) for line in card.get_text('\n', strip=True).split('\n')]
    lines = [line for line in lines if line and line.lower() not in {'zobrazit detail', 'koupit vstupenky'}]
    date, time_from = parse_card_datetime(lines)

    venue = None
    city = DEFAULT_CITY
    title_lines = []
    for line in lines[3:]:
        if re.search(r'\d{1,2}[:.]\d{2}', line) and not title_lines:
            venue, city = split_place(re.sub(r',?\s*\d{1,2}[:.]\d{2}$', '', line))
            continue
        title_lines.append(line)

    return {
        'title': clean_text(title_lines[0]) if title_lines else None,
        'date': date,
        'time_from': time_from,
        'time_to': None,
        'venue': venue,
        'city': city,
        'description': clean_text('\n'.join(title_lines)) or None,
        'type': 'concert',
    }


def extract_title(soup):
    title = clean_text(soup.select_one('h1').get_text(' ', strip=True)) if soup.select_one('h1') else ''
    subtitle = clean_text(soup.select_one('.concert-titles h2').get_text(' ', strip=True)) if soup.select_one('.concert-titles h2') else ''
    return title or subtitle or None, subtitle or None


def section_text(soup, selector):
    element = soup.select_one(selector)
    if not element:
        return ''
    return clean_text(element.get_text('\n', strip=True))


def section_by_heading(soup, heading_text):
    for heading in soup.select('h2'):
        if clean_text(heading.get_text(' ', strip=True)).lower() == heading_text.lower():
            parent = heading.parent
            if parent:
                return clean_text(parent.get_text('\n', strip=True))
    return ''


def extract_detail(session, url):
    soup = get_soup(session, url)
    title, subtitle = extract_title(soup)

    date, time_from = parse_czech_datetime(section_text(soup, 'time.date'))
    place_text = section_text(soup, '.place')
    venue, city = split_place(place_text)

    performers = section_by_heading(soup, 'Účinkující')
    programme = section_by_heading(soup, 'Program') or section_text(soup, '.performers')
    body = section_text(soup, '.longText') or section_by_heading(soup, 'Popis')

    description_parts = [
        title,
        subtitle,
        f'Datum a místo: {section_text(soup, "time.date")} - {place_text}',
        f'Účinkující:\n{performers}' if performers else '',
        f'Program:\n{programme}' if programme else '',
        body,
    ]

    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': venue,
        'city': city,
        'description': clean_text('\n\n'.join(part for part in description_parts if part)) or None,
        'type': 'concert',
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    soup = get_soup(session, PROGRAM_URL)
    fallbacks = {
        urljoin(BASE_URL, card.get('href')): card_fallback(card)
        for card in soup.select('a.card-a[href]')
    }

    concerts = []
    for url in listing_links(session):
        fallback = fallbacks.get(url, {})
        try:
            detail = extract_detail(session, url)
        except requests.RequestException as exc:
            print(f'Failed to scrape {url}: {exc}')
            detail = {}

        concert = {**fallback, **{key: value for key, value in detail.items() if value}}
        concert['url'] = url
        if concert.get('title') and concert.get('date'):
            concerts.append(concert)

    return concerts


class CnsoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cnso_cz',
        source=SOURCE_NAME,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'time_to',
            'venue',
            'city',
            'description',
            'type',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    CnsoCrawler().run()


if __name__ == '__main__':
    main()
