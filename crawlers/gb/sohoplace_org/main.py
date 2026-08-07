import json
import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://sohoplace.org/'
CALENDAR_URL = urljoin(SOURCE_URL, 'whats-on-calendar/')
WHATS_ON_URL = urljoin(SOURCE_URL, 'whats-on/')
SOURCE = '@sohoplace'
VENUE = '@sohoplace'
CITY = 'London'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def title_key(value):
    return re.sub(r'[^a-z0-9]+', '', clean_text(value).lower())


def extract_performances(html):
    match = re.search(r'const\s+nlivenPerformances\s*=\s*', html)
    if not match:
        raise ValueError('Could not find nlivenPerformances in calendar page')
    payload, _ = json.JSONDecoder().raw_decode(html, match.end())
    return payload.get('performances') or []


def show_links(session):
    response = session.get(WHATS_ON_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    return sorted({urljoin(WHATS_ON_URL, link['href']) for link in soup.select('a[href*="/shows/"]')})


def show_details(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    heading = soup.select_one('main h1')
    about = soup.select_one('.site-event-details__bottom-left')
    paragraphs = []
    if about:
        for paragraph in about.select('p'):
            text = clean_text(paragraph)
            if text and text not in paragraphs:
                paragraphs.append(text)
    return title_key(heading), url, '\n\n'.join(paragraphs) or None


def load_show_details(session):
    details = {}
    for url in show_links(session):
        try:
            key, show_url, description = show_details(session, url)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape show detail',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        if key:
            details[key] = (show_url, description)
    return details


def make_record(performance, details):
    title = clean_text(performance.get('event_title') or performance.get('name'))
    raw_start = performance.get('start_date_time')
    try:
        start = datetime.strptime(raw_start, '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None
    booking_url = clean_text(performance.get('booking_url'))
    if not title or not booking_url:
        return None

    show_url, description = details.get(title_key(title), (booking_url, None))
    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': show_url,
        'time_from': start.strftime('%H:%M'),
        'venue': VENUE,
        'city': CITY,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class SohoplaceOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='sohoplace_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
        response = session.get(CALENDAR_URL, timeout=45)
        response.raise_for_status()
        details = load_show_details(session)
        records = [make_record(item, details) for item in extract_performances(response.text)]
        return sorted(
            (record for record in records if record),
            key=lambda item: (item['date'], item['time_from'], item['title']),
        )


def main():
    SohoplaceOrgCrawler().run()


if __name__ == '__main__':
    main()
