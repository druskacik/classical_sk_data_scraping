import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://juventuslyrica.ar/'
API_URL = f'{SOURCE_URL}wp-json/wp/v2/pages'
SOURCE = 'Juventus Lyrica'
CITY = 'Buenos Aires'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'es-AR,es;q=0.9',
}

MONTHS = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'setiembre': 9, 'octubre': 10,
    'noviembre': 11, 'diciembre': 12,
}

# Older Elementor pages are prose rather than event objects. These overrides
# preserve every unambiguous performance date still published in the archive.
PUBLISHED_SCHEDULES = {
    'rigoletto': [
        ('2026-10-16', '20:00'), ('2026-10-18', '17:30'),
        ('2026-10-23', '20:00'), ('2026-10-24', '20:00'),
    ],
    'madama-butterfly': [
        ('2026-06-05', '20:00'), ('2026-06-07', '17:30'),
        ('2026-06-12', '20:00'), ('2026-06-13', '20:00'),
    ],
    'macbeth2025': [
        ('2025-09-10', None), ('2025-09-11', None),
        ('2025-09-12', None), ('2025-09-14', None), ('2025-09-20', None),
    ],
    'el-barbero-de-sevilla': [
        ('2025-06-28', None), ('2025-07-05', None), ('2025-07-12', None),
    ],
    'gran-fiesta-de-la-opera': [
        ('2024-10-11', '20:00'), ('2024-10-13', '17:30'),
        ('2024-10-18', '20:00'), ('2024-10-19', '20:00'),
    ],
    'nadie-duerma': [
        ('2024-07-06', '11:00'), ('2024-07-13', '11:00'),
        ('2024-07-17', '11:00'), ('2024-07-24', '11:00'),
    ],
    'cavalleria-rusticana': [
        ('2023-06-02', '20:00'), ('2023-06-04', '17:30'),
        ('2023-06-10', '20:00'),
    ],
    'don-giovanni': [
        ('2023-10-06', None), ('2023-10-08', None),
        ('2023-10-12', None), ('2023-10-14', None),
    ],
    'carmen_info': [('2022-10-22', '20:00')],
    'gran_fiesta_de_la_opera': [
        ('2022-07-01', None), ('2022-07-02', None), ('2022-07-03', None),
    ],
    'nadie-duerma-2022': [('2022-05-07', None), ('2022-05-08', None)],
}

NON_EVENTS = {
    'home', 'amigos', 'bases-y-condiciones', 'carmen', 'carmen_2022', 'conocenos',
    'desarrollo-de-audiencia', 'equipo', 'figaro',
    'formacion-de-artistas', 'formacion-de-artistas-2023',
    'guia-didactica', 'mapa-de-la-opera', 'opera-a-un-click',
    'repositorio-de-cvs', 'temporada-2017', 'temporadas-anteriores',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_pages(session):
    response = session.get(API_URL, params={'per_page': 100}, timeout=45)
    response.raise_for_status()
    return response.json()


def resolve_venue(text):
    lowered = text.lower()
    candidates = []
    for needle, venue in (
        ('casa victoria ocampo', 'Casa Victoria Ocampo'),
        ('ciudad cultural konex', 'Ciudad Cultural Konex'),
        ('cckonex', 'Ciudad Cultural Konex'),
        ('teatro avenida', 'Teatro Avenida'),
    ):
        position = lowered.find(needle)
        if position >= 0:
            candidates.append((position, venue))
    return min(candidates)[1] if candidates else None


def date_groups(text, year):
    """Read compact Spanish date lists near the beginning of an event page."""
    intro = text[:1200]
    groups = []
    month_matches = list(re.finditer('|'.join(MONTHS), intro, re.IGNORECASE))
    for match in month_matches[:2]:
        month = MONTHS[match.group(0).lower()]
        window = intro[max(0, match.start() - 110):match.end() + 230]
        # Stop before production credits/cast numbers contaminate day parsing.
        window = re.split(
            r'(?i)direcci[oó]n|elenco|m[uú]sica original|compr[aá]', window
        )[0]
        blocks = re.findall(
            r'(?i)((?:(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+)?'
            r'\d{1,2}(?:\s*,\s*(?:(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+)?'
            r'\d{1,2})*(?:\s+y\s*(?:(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+)?'
            r'\d{1,2})?)(?:\s+de)?\s+(?:' + '|'.join(MONTHS) + r')?'
            r'(?:[^.]{0,45}?a\s+las?\s+(\d{1,2})(?::|\.)(\d{2})\s*h?s?\.?)?',
            window,
        )
        for day_text, hour, minute in blocks:
            days = [int(value) for value in re.findall(r'\d{1,2}', day_text)]
            if not days:
                continue
            time_from = f'{int(hour):02d}:{minute}' if hour else None
            for day in days:
                try:
                    event_date = date(year, month, day).isoformat()
                except ValueError:
                    continue
                groups.append((event_date, time_from))
    return groups


def event_dates(page, text):
    slug = page.get('slug', '')
    if slug in PUBLISHED_SCHEDULES:
        return PUBLISHED_SCHEDULES[slug]

    year = int((page.get('date') or '')[:4])
    return date_groups(text, year)


def make_records(page):
    slug = page.get('slug', '')
    if slug in NON_EVENTS:
        return []
    title = clean_text((page.get('title') or {}).get('rendered'))
    description = clean_text((page.get('content') or {}).get('rendered'))
    url = page.get('link') or ''
    if not title or not description or not url:
        return []

    intro = description[:1200].lower()
    if slug not in PUBLISHED_SCHEDULES and not any(
        term in intro for term in (
            'ópera', 'opera', 'concierto', 'rigoletto', 'macbeth', 'butterfly', 'barbero'
        )
    ):
        return []
    venue = resolve_venue(description)
    if not venue and slug in {'madama-butterfly'}:
        venue = 'Teatro Avenida'
    if not venue:
        return []

    records = []
    for event_date, time_from in event_dates(page, description):
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': CITY,
            'country_code': 'AR',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


class JuventusLyricaArCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='juventuslyrica_ar',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AR',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            pages = fetch_pages(session)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch Juventus Lyrica pages',
                event='crawler_fetch_failed',
                level='error',
                url=API_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        records = [record for page in pages for record in make_records(page)]
        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    JuventusLyricaArCrawler().run()


if __name__ == '__main__':
    main()
