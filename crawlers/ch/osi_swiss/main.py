import re
import unicodedata
from datetime import datetime

import requests

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.osi.swiss/en'
CONCERTS_URL = f'{SOURCE_URL}/concerti/tutti-i-concerti/'
API_URL = 'https://api.storyblok.com/v2/cdn/stories'
SOURCE = 'Orchestra della Svizzera italiana'

# This is Storyblok's public delivery token, also used by the website itself.
STORYBLOK_TOKEN = 'lMfsYaPoTpLmVJlsR9NPRgtt'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-GB,en;q=0.9,it;q=0.8',
}

# OSI is Swiss but publishes its touring performances in the same calendar.
# Unrecognised locations are skipped rather than incorrectly labelled Swiss.
CITY_COUNTRIES = {
    'alicante': 'ES',
    'ascona': 'CH',
    'barcellona': 'ES',
    'bellinzona': 'CH',
    'brissago': 'CH',
    'chiasso': 'CH',
    'cremona': 'IT',
    'linz': 'AT',
    'locarno': 'CH',
    'lubiana': 'SI',
    'lucerna': 'CH',
    'lugano': 'CH',
    'madrid': 'ES',
    'mendrisio': 'CH',
    'mesocco': 'CH',
    'milano': 'IT',
    'monaco di baviera': 'DE',
    'norimberga': 'DE',
    'pavia': 'IT',
    'piacenza': 'IT',
    'pordenone': 'IT',
    'regensburg': 'DE',
    'santa maria in calanca': 'CH',
    'saragozza': 'ES',
    'sementina': 'CH',
    'vienna': 'AT',
    'zagabria': 'HR',
}


def clean_text(value):
    if value is None:
        return ''
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def location_key(city):
    city = unicodedata.normalize('NFKD', clean_text(city))
    city = ''.join(character for character in city if not unicodedata.combining(character))
    city = re.sub(r'\s*\([^)]*\)\s*$', '', city)
    return city.casefold().strip()


def rich_text(value):
    """Turn Storyblok rich-text JSON into useful plain text."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return clean_text('\n'.join(filter(None, (rich_text(item) for item in value))))
    if not isinstance(value, dict):
        return ''

    own_text = clean_text(value.get('text'))
    children = rich_text(value.get('content'))
    return clean_text('\n'.join(filter(None, (own_text, children))))


def description_for(content):
    parts = []
    seo_description = clean_text((content.get('SEO') or {}).get('description'))
    if seo_description:
        parts.append(seo_description)

    body = rich_text(content.get('body'))
    if body and body not in parts:
        parts.append(body)

    programme = []
    for item in content.get('program_items') or []:
        composer = clean_text(item.get('composer_name'))
        work = rich_text(item.get('piece_of_music'))
        movements = rich_text(item.get('parts_of_the_peace'))
        line = ': '.join(filter(None, (composer, work)))
        if movements:
            line = '\n'.join(filter(None, (line, movements)))
        if line:
            programme.append(line)

    if programme:
        parts.append('Programme\n' + '\n'.join(programme))
    elif clean_text(content.get('music_of')):
        parts.append('Music by\n' + clean_text(content['music_of']))

    return clean_text('\n\n'.join(parts)) or None


def event_url(story):
    slug = clean_text(story.get('full_slug')).strip('/')
    if slug.startswith('en/'):
        slug = slug[3:]
    return f'{SOURCE_URL}/{slug}' if slug else ''


def make_record(story):
    content = story.get('content') or {}
    if content.get('component') != 'concert':
        return None

    title = clean_text(content.get('title'))
    city = clean_text(content.get('city'))
    venue = clean_text(content.get('address'))
    url = event_url(story)
    country_code = CITY_COUNTRIES.get(location_key(city))
    try:
        start = datetime.strptime(clean_text(content.get('date')), '%Y-%m-%d %H:%M')
    except ValueError:
        return None

    if not all((title, city, venue, url, country_code)):
        return None

    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': url,
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description_for(content),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    params = {
        'version': 'published',
        'resolve_links': 'url',
        'language': 'en',
        'starts_with': 'concerti/tutti-i-concerti',
        'per_page': 100,
        'token': STORYBLOK_TOKEN,
    }
    records = []
    page = 1

    while True:
        params['page'] = page
        response = session.get(API_URL, params=params, timeout=45)
        response.raise_for_status()
        stories = response.json().get('stories') or []
        for story in stories:
            record = make_record(story)
            if record:
                records.append(record)
            elif (story.get('content') or {}).get('component') == 'concert':
                log_message(
                    'Skipped concert with incomplete or unknown location data',
                    event='crawler_item_skipped',
                    level='warning',
                    url=event_url(story),
                )

        if len(stories) < params['per_page']:
            break
        page += 1

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class OsiSwissCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='osi_swiss',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
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
    OsiSwissCrawler().run()


if __name__ == '__main__':
    main()
