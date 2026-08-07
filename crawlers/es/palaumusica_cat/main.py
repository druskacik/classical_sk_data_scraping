import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.palaumusica.cat/ca'
PROGRAM_URL = f'{SOURCE_URL}/programacio_1158636'
API_URL = f'{SOURCE_URL}/programming_data_json'
SOURCE = 'Palau de la Música Catalana'
HOME_CITY = 'Barcelona'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'ca-ES,ca;q=0.9,es;q=0.8,en;q=0.6',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_start(value):
    value = value.get('value') if isinstance(value, dict) else value
    try:
        parsed = datetime.strptime(value or '', '%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return None, None
    return parsed.date().isoformat(), parsed.strftime('%H:%M')


def resolve_location(stage_title):
    title = clean_text(stage_title).strip(' ,-')
    if not title or re.search(r'\b(lloc|a determinar)\b', title, re.IGNORECASE):
        return None, None

    # These unqualified room names are spaces in Barcelona used by this
    # calendar. Other unqualified names cannot safely be treated as venues in
    # the home city (for example, an archived tour may only say "Saragossa").
    home_room = re.search(
        r'^(Sala (?:de Concerts|Petit Palau|d.Assaig)|Foyer del |Auditori F.rum CCIB)',
        title,
        re.IGNORECASE,
    )
    if home_room:
        return title, HOME_CITY
    if not re.search(r'\s[-–]\s|\bBarcelona\b', title, re.IGNORECASE):
        return None, None

    if re.search(r'\bBarcelona\b', title, re.IGNORECASE):
        venue = re.sub(r'^Barcelona\s*[.:-]\s*', '', title, flags=re.IGNORECASE)
        return clean_text(venue) or title, HOME_CITY

    # The endpoint also contains tours. A location such as "Girona - Auditori"
    # is explicit enough to use; foreign tour locations are deliberately skipped
    # because this country-scoped crawler must never label them as Spanish.
    foreign = (
        'alemanya', 'àustria', 'bèlgica', 'canadà', 'dinamarca', 'estats units',
        'finlàndia', 'frança', 'holanda', 'hongria', 'irlanda', 'itàlia',
        'luxembourg', 'polonia', 'portugal', 'regne unit', 'suècia', 'suïssa',
        'andorra', 'london', 'londres', 'parís', 'paris', 'viena', 'helsinki',
        'copenhaguen', 'amsterdam', 'montreal', 'los angeles', 'toulouse',
    )
    lowered = title.lower()
    if any(name in lowered for name in foreign):
        return None, None

    parts = re.split(r'\s*[-–]\s*', title, maxsplit=1)
    if len(parts) != 2 or not clean_text(parts[0]) or not clean_text(parts[1]):
        return None, None
    city = re.sub(r'\s*\([^)]*\)\s*', '', clean_text(parts[0])).strip()
    venue = clean_text(parts[1])
    return (venue, city) if venue and city else (None, None)


def production_description(production):
    parts = []
    for heading, field in (
        ('', 'summary'),
        ('Programa', 'program'),
        ('Intèrprets', 'performers'),
    ):
        text = clean_text(production.get(field))
        if text and text not in parts:
            parts.append(f'{heading}\n{text}' if heading else text)
    return '\n\n'.join(parts) or None


def make_record(session, production, stage):
    if session.get('hidden') or production.get('hidden'):
        return None
    title = clean_text(production.get('title'))
    subtitle = clean_text(production.get('subtitle'))
    if subtitle and subtitle.lower() not in title.lower():
        title = f'{title} – {subtitle.lstrip("–—- ")}'
    event_date, time_from = parse_start(session.get('start_date'))
    venue, city = resolve_location((stage or {}).get('title'))
    url = production.get('url')
    if not title or not event_date or not url or not venue or not city:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': None if session.get('uncertain_start_time') else time_from,
        'venue': venue,
        'city': city,
        'country_code': 'ES',
        'description': production_description(production),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(
        API_URL,
        params={
            'palau_productions': 1,
            'orfeo_productions': 0,
            'espaisoci_productions': 0,
            'sessions_as_dict': 1,
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    productions = payload.get('productions') or {}
    stages = payload.get('stages') or {}
    records = []
    skipped = 0
    for item in (payload.get('sessions') or {}).values():
        production = productions.get(str(item.get('production')))
        if not production:
            skipped += 1
            continue
        record = make_record(item, production, stages.get(str(item.get('stage'))))
        if record:
            records.append(record)
        else:
            skipped += 1
    log_message(
        'Parsed Palau programme API',
        event='crawler_api_parsed',
        url=response.url,
        record_count=len(records),
        skipped_count=skipped,
    )
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class PalaumusicaCatCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='palaumusica_cat',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='ES',
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
    PalaumusicaCatCrawler().run()


if __name__ == '__main__':
    main()
