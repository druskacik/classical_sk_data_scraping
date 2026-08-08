import re
from datetime import date
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://fourwinds.com.au/'
SOURCE = 'Four Winds'
API_URL = f'{SOURCE_URL}wp-json/tribe/events/v1/events'
CITY = 'Bermagui'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-AU,en;q=0.9',
}

NON_EVENT_TITLE_RE = re.compile(
    r'\b(?:gift vouchers?|flexipass|volunteer with|win 1 of|welcome to the \d{4} program)\b',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    # Older entries contain Divi shortcodes around otherwise useful programme
    # prose. Remove the shortcode tags while retaining their text content.
    value = re.sub(r'\[/?et_pb_[^\]]*\]', ' ', unescape(str(value)))
    text = BeautifulSoup(value, 'html.parser').get_text('\n', strip=True)
    text = unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_events(session):
    events = []
    page = 1
    while True:
        response = session.get(
            API_URL,
            params={
                'per_page': 50,
                'page': page,
                'start_date': '2000-01-01',
                'end_date': '2100-12-31',
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        events.extend(payload.get('events') or [])
        if page >= int(payload.get('total_pages') or 1):
            return events
        page += 1


def make_record(event):
    title = clean_text(event.get('title'))
    url = event.get('url') or ''
    start = event.get('start_date') or ''
    venue_data = event.get('venue') or {}
    venue = clean_text(venue_data.get('venue'))

    if not title or NON_EVENT_TITLE_RE.search(title) or not url or not venue:
        return None
    try:
        event_date = date.fromisoformat(start[:10]).isoformat()
    except (TypeError, ValueError):
        return None

    # Every venue currently exposed by this calendar is on Four Winds Road at
    # the organisation's Bermagui site. Missing venues are skipped above so the
    # home-city inference can never leak onto an unspecified touring event.
    address = clean_text(venue_data.get('address')).lower()
    city = clean_text(venue_data.get('city'))
    if not city and ('four winds rd' in address or venue in {
        'Windsong Pavilion', 'Sound Shell', 'Windsong Pavilion or Sound Shell'
    }):
        city = CITY
    if not city:
        return None

    time_from = None
    time_match = re.match(r'\d{4}-\d{2}-\d{2}[ T](\d{2}):(\d{2})', start)
    if time_match and not event.get('all_day'):
        time_from = f'{time_match.group(1)}:{time_match.group(2)}'

    description = clean_text(event.get('description')) or None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'AU',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class FourwindsComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='fourwinds_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='potential',
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
            events = fetch_events(session)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch Four Winds events',
                event='crawler_fetch_failed',
                level='error',
                url=API_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        records = [record for event in events if (record := make_record(event))]
        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    FourwindsComAuCrawler().run()


if __name__ == '__main__':
    main()
