import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://alpenarte.eu/'
PROGRAMME_URL = f'{SOURCE_URL}programm/'
API_URL = f'{SOURCE_URL}wp-json/wp/v2'
SOURCE = ':alpenarte'
DEFAULT_CITY = 'Schwarzenberg'
DEFAULT_VENUE = 'Angelika Kauffmann Saal'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'jänner': 1, 'januar': 1, 'februar': 2, 'märz': 3, 'april': 4,
    'mai': 5, 'juni': 6, 'juli': 7, 'august': 8, 'september': 9,
    'oktober': 10, 'okt': 10, 'november': 11, 'dezember': 12,
}

# The archive pages are prose retrospectives rather than event objects. These
# dates follow the explicitly stated festival ranges and weekday headings.
ARCHIVE_EVENTS = {
    'rueckblick-2025': [
        ('Express Yourself – Hanke Brothers', '2025-10-10', DEFAULT_VENUE, DEFAULT_CITY,
         'Die Hanke Brothers eröffneten das Festival mit ihrem Programm „Express Yourself“, einer Mischung aus Klassik, Pop und Jazz.'),
        ('Familienkonzert mit den Hanke Brothers', '2025-10-11', 'Altes Hallenbad', 'Feldkirch',
         'Familienkonzert der Hanke Brothers für Kinder und Eltern.'),
        ('Grand Concert – Amelio Trio, Miriam Kutrowatz und Anja Mittermüller', '2025-10-11', DEFAULT_VENUE, DEFAULT_CITY,
         'Musik von Brahms bis Dvořák sowie die Uraufführung eines Werks von Tsotne Zedginidze.'),
        ('Alles Walzer', '2025-10-12', DEFAULT_VENUE, DEFAULT_CITY,
         'Eine Hommage an Johann Strauss II mit Miriam Kutrowatz, Lara Kusztrich, David Kessler, Christoph Hammer, Benedikt Sinko und Anna Gruchmann.'),
    ],
    'rueckblick-2024': [
        ('Gebrüder Martin', '2024-10-10', 'Alter Landtagssaal', 'Bregenz',
         'Lionel Martin (Cello) und Demian Martin (Klavier) mit Lieblingsrepertoire und Improvisation.'),
        ('Klassik bis Percussion', '2024-10-11', DEFAULT_VENUE, DEFAULT_CITY,
         'Duo Minerva und Trio Colores mit Klassik, Crossover, Percussion und der Uraufführung eines Werks von F. Künzli.'),
        ('Grand Concert – María Dueñas und Alexander Malofeev', '2024-10-12', DEFAULT_VENUE, DEFAULT_CITY,
         'María Dueñas (Violine) und Alexander Malofeev (Klavier), ergänzt durch die Uraufführung eines Werks von G. Ortiz.'),
        ('Federspiel – 20-jähriges Jubiläum', '2024-10-13', DEFAULT_VENUE, DEFAULT_CITY,
         'Federspiel feierte sein 20-jähriges Jubiläum mit einem Best-of-Programm und Jakob Lampert als Solist.'),
    ],
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, endpoint, params=None):
    response = session.get(f'{API_URL}/{endpoint}', params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def parse_german_date(value, year=None):
    match = re.search(
        r'(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\.?(?:\s+(\d{4}))?', value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    month = MONTHS.get(match.group(2).lower().rstrip('.'))
    event_year = int(match.group(3) or year or 0)
    if not month or not event_year:
        return None
    try:
        return date(event_year, month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def first_time(value, concert_only=False):
    if concert_only:
        match = re.search(r'(\d{1,2})[.:](\d{2})\s*Uhr[^\n]{0,45}\bKonzert\b', value, re.I)
        if match:
            return f'{int(match.group(1)):02d}:{match.group(2)}'
    match = re.search(r'(\d{1,2})[.:](\d{2})\s*Uhr', value, re.I)
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def programme_year(session):
    pages = get_json(session, 'pages', {'include': 113, '_fields': 'content'})
    text = clean_text(pages[0]['content']['rendered']) if pages else ''
    match = re.search(r'16\.\s*Okt\.?\s*[–-]\s*18\.\s*Okt\.?\s*(20\d{2})', text, re.I)
    return int(match.group(1)) if match else date.today().year


def heading_title(section):
    columns = section.select(':scope > .elementor-container > .elementor-column')
    candidate = clean_text(columns[-1]) if columns else clean_text(section)
    return candidate.replace('\n', ' ')


def current_programme(session):
    pages = get_json(session, 'pages', {'slug': 'programm', '_fields': 'content,link'})
    if not pages:
        return []
    soup = BeautifulSoup(pages[0]['content']['rendered'], 'html.parser')
    root = soup.find('div', class_='elementor') or soup
    sections = root.find_all('section', recursive=False)
    year = programme_year(session)
    records = []
    for index, section in enumerate(sections):
        heading = clean_text(section)
        event_date = parse_german_date(heading, year)
        if not event_date or not re.match(r'^(Mo|Di|Mi|Do|Fr|Sa|So)\b', heading):
            continue
        detail = ''
        for following in sections[index + 1:]:
            following_text = clean_text(following)
            if parse_german_date(following_text, year) and re.match(
                r'^(Mo|Di|Mi|Do|Fr|Sa|So)\b', following_text
            ):
                break
            if len(following_text) > 80 and 'Detailprogramm' not in following_text:
                detail = following_text
                break
        title = heading_title(section)
        title = re.sub(r'^(Mo|Di|Mi|Do|Fr|Sa|So)\s+\d{1,2}\.\s*\w+\.?\s*', '', title)
        if not title or not detail:
            continue

        venue, city = DEFAULT_VENUE, DEFAULT_CITY
        if 'Dorfspaziergang' in title:
            venue = 'Dorfplatz Schwarzenberg'
            walk_time = re.search(
                r'(\d{1,2})[.:](\d{2})\s*Uhr\s+Musikalischer Dorfspaziergang', detail, re.I
            )
            time_from = (
                f'{int(walk_time.group(1)):02d}:{walk_time.group(2)}'
                if walk_time else first_time(detail)
            )
        else:
            time_from = first_time(detail, concert_only=True) or first_time(detail)
        records.append({
            'title': title,
            'date': event_date,
            'url': PROGRAMME_URL,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'AT',
            'description': detail,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def legacy_events(session):
    events = get_json(session, 'events', {
        'per_page': 100,
        'orderby': 'date',
        'order': 'asc',
        '_fields': 'id,link,title,content',
    })
    records = []
    for event in events:
        url = event.get('link') or ''
        try:
            response = session.get(url, timeout=45)
            response.raise_for_status()
            page_text = clean_text(response.text)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape :alpenarte event detail',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        title = clean_text((event.get('title') or {}).get('rendered'))
        date_match = re.search(r'(\d{1,2}\.\s*\w+\s+20\d{2}),\s*(\d{1,2})[.:](\d{2})\s*Uhr', page_text, re.I)
        if not title or not url or not date_match:
            continue
        event_date = parse_german_date(date_match.group(1))
        location_match = re.search(
            re.escape(date_match.group(0)) + r'\s*\n([^\n]+),\s*([^\n]+)', page_text,
            re.I,
        )
        if location_match:
            venue = clean_text(location_match.group(1))
            city = clean_text(location_match.group(2))
        elif 'Gottesdienst' in title:
            # The other published 2023 event pages identify this event as
            # taking place at Kirche, Andelsbuch, although its own detail
            # template omits the location line.
            venue, city = 'Kirche', 'Andelsbuch'
        else:
            venue, city = '', ''
        if not event_date or not venue or not city:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': f'{int(date_match.group(2)):02d}:{date_match.group(3)}',
            'venue': venue,
            'city': city,
            'country_code': 'AT',
            'description': clean_text((event.get('content') or {}).get('rendered')) or None,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def archive_events(session):
    records = []
    for slug, events in ARCHIVE_EVENTS.items():
        pages = get_json(session, 'pages', {'slug': slug, '_fields': 'link,content'})
        if not pages:
            continue
        page = pages[0]
        archive_text = clean_text(page['content']['rendered'])
        for title, event_date, venue, city, summary in events:
            # Only emit hard-coded archive facts while the source still
            # publishes the corresponding retrospective.
            if title.split()[0].strip('„“') not in archive_text:
                continue
            records.append({
                'title': title,
                'date': event_date,
                'url': page['link'],
                'time_from': None,
                'venue': venue,
                'city': city,
                'country_code': 'AT',
                'description': summary,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = current_programme(session) + legacy_events(session) + archive_events(session)
    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['url']
    ))


class AlpenarteEuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='alpenarte_eu',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    AlpenarteEuCrawler().run()


if __name__ == '__main__':
    main()
