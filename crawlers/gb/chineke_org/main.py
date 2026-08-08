import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.chineke.org/'
API_URL = 'https://live-chineke-main.pantheonsite.io/jsonapi/node/events'
SOURCE = 'Chineke! Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/vnd.api+json',
}

# The event API supplies a venue but no address or city. These are the venues
# used by the catalogue, including Chineke!'s international touring dates.
VENUES = {
    'berliner philharmoniker': ('Berlin', 'DE'),
    'brucknerhaus': ('Linz', 'AT'),
    'de singel': ('Antwerp', 'BE'),
    'desingel': ('Antwerp', 'BE'),
    'fairfield concert hall': ('Croydon', 'GB'),
    'granada theatre': ('Santa Barbara', 'US'),
    'hackney empire': ('London', 'GB'),
    'isar philharmonic hall': ('Munich', 'DE'),
    'kkl luzern, concert hall': ('Lucerne', 'CH'),
    'koko': ('London', 'GB'),
    'konzerthaus dortmund': ('Dortmund', 'DE'),
    'lugano arte e cultura': ('Lugano', 'CH'),
    'national concert hall': ('Dublin', 'IE'),
    'philharmonie berlin': ('Berlin', 'DE'),
    'philharmonie de paris': ('Paris', 'FR'),
    'queen elizabeth hall': ('London', 'GB'),
    'renee and henry sege': ('Costa Mesa', 'US'),
    "ronnie scott's": ('London', 'GB'),
    'royal albert hall': ('London', 'GB'),
    'royal concertgebouw': ('Amsterdam', 'NL'),
    'royal festival hall': ('London', 'GB'),
    'sound out leeds 2024/25': ('Leeds', 'GB'),
    'the anvil, basingstoke': ('Basingstoke', 'GB'),
    'warwick arts centre': ('Coventry', 'GB'),
    'wiener konzerthaus': ('Vienna', 'AT'),
    'wigmore hall': ('London', 'GB'),
    'wimbledon book fest': ('London', 'GB'),
    'woolwich works': ('London', 'GB'),
}

DESCRIPTION_TYPES = {
    'paragraph--2_column_text',
    'paragraph--bullet_point_text',
    'paragraph--rte_single_column',
}


def clean_text(value):
    if value is None:
        return ''
    if isinstance(value, dict):
        value = value.get('processed') or value.get('value') or ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_payload(session, url, params=None):
    response = session.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def catalogue(session):
    url = API_URL
    params = {'page[limit]': 50, 'include': 'field_reusable_components'}
    while url:
        payload = get_payload(session, url, params=params)
        yield payload
        next_link = (payload.get('links', {}).get('next') or {}).get('href')
        url = next_link
        params = None


def component_description(component):
    if component.get('type') not in DESCRIPTION_TYPES:
        return ''
    attributes = component.get('attributes') or {}
    parts = []
    for key in (
        'field_title',
        'field_body_text',
        'field_list_text',
        'field_left_column_text',
        'field_right_column_text',
        'field_text',
    ):
        value = attributes.get(key)
        values = value if isinstance(value, list) else [value]
        parts.extend(clean_text(item) for item in values if clean_text(item))
    return '\n'.join(parts)


def make_record(item, included):
    attributes = item.get('attributes') or {}
    title = clean_text(attributes.get('title'))
    venue = clean_text(attributes.get('field_location'))
    location = VENUES.get(venue.casefold())
    date_value = (attributes.get('field_date') or {}).get('value') or ''
    alias = (attributes.get('path') or {}).get('alias')
    if not title or not venue or not location or not alias:
        return None
    try:
        starts_at = datetime.strptime(date_value, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None

    relationships = item.get('relationships') or {}
    component_refs = (relationships.get('field_reusable_components') or {}).get('data') or []
    descriptions = []
    for ref in component_refs:
        component = included.get((ref.get('type'), ref.get('id')))
        description = component_description(component or {})
        if description and description not in descriptions:
            descriptions.append(description)

    city, country_code = location
    return {
        'title': title,
        'date': starts_at.date().isoformat(),
        'url': urljoin(SOURCE_URL, alias),
        'time_from': starts_at.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': '\n\n'.join(descriptions) or None,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    for payload in catalogue(session):
        included = {
            (entry.get('type'), entry.get('id')): entry
            for entry in payload.get('included') or []
        }
        for item in payload.get('data') or []:
            record = make_record(item, included)
            if record:
                records.append(record)
            else:
                attributes = item.get('attributes') or {}
                log_message(
                    'Skipping event with incomplete date or location',
                    event='crawler_item_skipped',
                    level='warning',
                    url=urljoin(SOURCE_URL, (attributes.get('path') or {}).get('alias') or ''),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class ChinekeOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='chineke_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        # The orchestra's catalogue occasionally includes adjacent cultural
        # events (for example a book-festival appearance), so classification
        # is required before insertion into the classical concert table.
        upload_target='potential',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'venue',
            'city',
            'country_code',
            'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    ChinekeOrgCrawler().run()


if __name__ == '__main__':
    main()
