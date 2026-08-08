import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.ineoquartet.com/'
SOURCE = 'Ineo Quartet'
ACCESS_TOKENS_URL = f'{SOURCE_URL}_api/v1/access-tokens'
EVENTS_API_URL = f'{SOURCE_URL}_api/wix-one-events-server/web/paginated-events/viewer'
WIX_EVENTS_APP_ID = '140603ad-af8d-84a5-2c80-a0f60cb47351'
WIX_INSTANCE_ID = '284bbbe7-52c7-454f-aed5-d123fbc9efed'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    raw = str(value)
    text = (
        BeautifulSoup(raw, 'html.parser').get_text('\n', strip=True)
        if '<' in raw
        else raw.strip()
    )
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_time(value):
    value = clean_text(value)
    if not value:
        return None
    for pattern in ('%I:%M %p', '%H:%M'):
        try:
            return datetime.strptime(value, pattern).strftime('%H:%M')
        except ValueError:
            pass
    return None


def usable_venue(value, city):
    venue = clean_text(value)
    if not venue or venue.casefold() == clean_text(city).casefold():
        return ''
    if re.match(r'https?://', venue, re.I):
        return ''
    # Wix sometimes stores only an address in the location-name field.
    if re.search(r'(?:straße|strasse|gasse|\bplatz\b|\bpl\.|\broad\b|\bstreet\b)', venue, re.I):
        return ''
    if re.match(r'^\d', venue) or re.search(r'\b\d{4,5}\b', venue):
        return ''
    return venue


def parse_event(event, dates):
    event_id = clean_text(event.get('id'))
    title = clean_text(event.get('title'))
    slug = clean_text(event.get('slug'))
    location = event.get('location') or {}
    full_address = location.get('fullAddress') or {}
    city = clean_text(full_address.get('city'))
    country_code = clean_text(full_address.get('country')).upper()
    venue = usable_venue(location.get('name'), city)

    date_info = dates.get(event_id) or {}
    local_start = clean_text(date_info.get('startDateISOFormatNotUTC'))
    try:
        event_date = datetime.fromisoformat(local_start).date().isoformat()
    except (TypeError, ValueError):
        event_date = ''
    time_from = parse_time(date_info.get('startTime'))

    if not all((title, event_date, slug, venue, city, country_code)):
        return None

    description_parts = []
    for field in ('description', 'about'):
        text = clean_text(event.get(field))
        if text and text not in description_parts:
            description_parts.append(text)

    return {
        'title': title,
        'date': event_date,
        'url': f'{SOURCE_URL}event-details-registration/{slug}',
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': '\n\n'.join(description_parts) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class IneoQuartetComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ineoquartet_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        token_response = session.get(ACCESS_TOKENS_URL, headers=HEADERS, timeout=45)
        token_response.raise_for_status()
        token = token_response.json()['apps'][WIX_EVENTS_APP_ID]['instance']

        response = session.get(
            EVENTS_API_URL,
            params={
                'offset': 0,
                'filter': 2,
                'byEventId': 'false',
                'members': 'true',
                'paidPlans': 'false',
                'locale': 'en-us',
                'filterType': 3,
                'sortOrder': 0,
                'limit': 100,
                'fetchBadges': 'true',
                'draft': 'false',
                'compId': 'comp-m3yh69xs',
            },
            headers={
                **HEADERS,
                'Authorization': token,
                'x-wix-brand': 'wix',
                'x-wix-linguist': f'en|en-us|true|{WIX_INSTANCE_ID}',
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        dates = ((payload.get('dates') or {}).get('events') or {})
        records = []
        for event in payload.get('events') or []:
            record = parse_event(event, dates)
            if record:
                records.append(record)
            else:
                log_message(
                    'Skipped incomplete Ineo Quartet event',
                    event='crawler_item_skipped',
                    level='warning',
                    url=f"{SOURCE_URL}event-details-registration/{clean_text(event.get('slug'))}",
                    error_type='IncompleteEventData',
                    error_message='Required date, title, venue, city, country, or slug is missing',
                )
        return sorted(
            records,
            key=lambda item: (item['date'], item['time_from'] or '', item['title']),
        )


def main():
    IneoQuartetComCrawler().run()


if __name__ == '__main__':
    main()
