import json
import re
from datetime import datetime
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.narodni-divadlo.cz'
PROGRAMME_URL = f'{BASE_URL}/en/programme'
SOURCE_NAME = 'National Theatre Prague'
SOURCE_URL = BASE_URL
PRAGUE_TZ = ZoneInfo('Europe/Prague')
CLASSICAL_GENRES = {'Opera', 'Ballet', 'Concert'}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def clean_text(text):
    if not text:
        return ''
    text = text.replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def html_to_text(value):
    if not value:
        return ''
    return clean_text(BeautifulSoup(value, 'html.parser').get_text('\n', strip=True))


def person_name(person):
    if not person:
        return ''
    return clean_text(f"{person.get('firstName', '')} {person.get('name', '')}")


def load_next_data(html):
    soup = BeautifulSoup(html, 'html.parser')
    script = soup.find('script', id='__NEXT_DATA__')
    if not script or not script.string:
        raise ValueError('Could not find __NEXT_DATA__ JSON payload')
    return json.loads(script.string)


def get_page_data(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return load_next_data(response.text)


def parse_datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(PRAGUE_TZ)


def production_url(production):
    slug = production.get('slug')
    production_id = production.get('id')
    if not slug or not production_id:
        return SOURCE_URL
    path = quote(f'{slug}-{production_id}', safe='-_.~')
    return urljoin(BASE_URL, f'/en/show/{path}')


def format_duration(minutes):
    if not minutes:
        return ''
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f'{hours} h {mins} min'
    if hours:
        return f'{hours} h'
    return f'{mins} min'


def collect_named_people(items, label_getter, artist_getter):
    lines = []
    seen = set()
    for item in items or []:
        label = clean_text(label_getter(item) or '')
        artist = person_name(artist_getter(item))
        if not label or not artist:
            continue
        line = f'{label}: {artist}'
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def extract_production_detail(session, production):
    url = production_url(production)
    try:
        data = get_page_data(session, url)
        detail = data['props']['pageProps']['productionData']['production']
    except (KeyError, TypeError, ValueError, requests.RequestException) as exc:
        print(f'Failed to scrape production detail {url}: {exc}')
        return {
            'url': url,
            'description': clean_text(production.get('title')),
        }

    parts = []
    title = clean_text(detail.get('title'))
    subtitle = clean_text(detail.get('subtitle'))
    if title:
        parts.append(title)
    if subtitle:
        parts.append(subtitle)

    titles = detail.get('titles') or []
    works = []
    authors = []
    for item in titles:
        work_title = clean_text(item.get('title'))
        author_names = [person_name(author) for author in item.get('authors') or []]
        author_names = [name for name in author_names if name]
        if work_title and author_names:
            works.append(f'{work_title} - {", ".join(author_names)}')
        elif work_title:
            works.append(work_title)
        authors.extend(author_names)
    if works:
        parts.append('Works: ' + '; '.join(dict.fromkeys(works)))
    if authors:
        parts.append('Authors/composers: ' + ', '.join(dict.fromkeys(authors)))

    genre = (detail.get('genre') or {}).get('title')
    ensemble = (detail.get('ensemble') or {}).get('title')
    duration = format_duration(detail.get('duration'))
    language = ', '.join(
        clean_text(item.get('name') or item.get('codename'))
        for item in detail.get('language') or []
        if item.get('name') or item.get('codename')
    )
    subtitles = ', '.join(
        clean_text(item.get('name') or item.get('codename'))
        for item in detail.get('subtitles') or []
        if item.get('name') or item.get('codename')
    )
    info = []
    if genre:
        info.append(f'Genre: {genre}')
    if ensemble:
        info.append(f'Ensemble: {ensemble}')
    if duration:
        info.append(f'Approximate running time: {duration}')
    if language:
        info.append(f'Language: {language}')
    if subtitles:
        info.append(f'Surtitles: {subtitles}')
    if detail.get('premiere'):
        info.append(f"Premiere: {detail['premiere']}")
    if info:
        parts.append('\n'.join(info))

    for value in [detail.get('perex'), html_to_text(detail.get('description'))]:
        value = clean_text(value)
        if value:
            parts.append(value)

    staff_by_id = {}
    for title_item in titles:
        for staff_item in title_item.get('staff') or []:
            staff_id = staff_item.get('id')
            field = staff_item.get('field') or {}
            if staff_id:
                staff_by_id[staff_id] = field.get('name') or field.get('title')
    staff_lines = collect_named_people(
        detail.get('staff'),
        lambda item: staff_by_id.get((item.get('staff') or {}).get('id')),
        lambda item: item.get('artist'),
    )
    if staff_lines:
        parts.append('Creatives:\n' + '\n'.join(staff_lines))

    for accordion in detail.get('accordions') or []:
        label = clean_text(accordion.get('label'))
        text = html_to_text(accordion.get('text'))
        if label and text:
            parts.append(f'{label}:\n{text}')
        elif text:
            parts.append(text)

    return {
        'url': url,
        'description': clean_text('\n\n'.join(parts)) or None,
    }


def get_programme_data(session):
    data = get_page_data(session, PROGRAMME_URL)
    return data['props']['pageProps']['programmeData']


def extract_concert(event, production, venue, genre, detail):
    start = parse_datetime(event.get('startAt'))
    if not start:
        return None

    event_type = (event.get('eventType') or {}).get('title')
    tags = ', '.join(
        clean_text(tag.get('title'))
        for tag in event.get('_resolved_tags', [])
        if tag.get('title')
    )
    type_parts = [value for value in [genre.get('title'), event_type, tags] if value]

    return {
        'title': clean_text(production.get('title')),
        'date': start.date().isoformat(),
        'time_from': start.strftime('%H:%M'),
        'time_to': None,
        'url': detail['url'],
        'venue': clean_text(
            venue.get('colosseumTitle')
            or (venue.get('scene') or {}).get('title')
            or venue.get('title')
        ),
        'city': 'Praha',
        'type': ' | '.join(type_parts) or None,
        'description': detail['description'],
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    programme = get_programme_data(session)
    productions = programme.get('productions') or {}
    venues = programme.get('venues') or {}
    genres = programme.get('genres') or {}
    tags = programme.get('tags') or {}

    today = datetime.now(PRAGUE_TZ).date()
    details = {}
    concerts = []

    for event in programme.get('events') or []:
        start = parse_datetime(event.get('startAt'))
        if not start or start.date() < today or event.get('isCanceled') or event.get('isHidden'):
            continue

        production = productions.get(event.get('production')) or {}
        genre = genres.get(production.get('genre')) or {}
        if genre.get('title') not in CLASSICAL_GENRES:
            continue

        production_id = production.get('id')
        if production_id not in details:
            details[production_id] = extract_production_detail(session, production)

        event['_resolved_tags'] = [tags[tag_id] for tag_id in event.get('tags') or [] if tag_id in tags]
        concert = extract_concert(
            event=event,
            production=production,
            venue=venues.get(event.get('venue')) or {},
            genre=genre,
            detail=details[production_id],
        )
        if concert:
            concerts.append(concert)

    return concerts


class NarodniDivadloCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='narodni_divadlo_cz',
        source=SOURCE_NAME,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'time_from', 'time_to', 'url', 'venue', 'city', 'type', 'description'],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    NarodniDivadloCrawler().run()


if __name__ == '__main__':
    main()
