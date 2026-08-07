import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.nntt.jac.go.jp/'
SOURCE = 'New National Theatre, Tokyo'
CALENDAR_URL = f'{SOURCE_URL}calendar/topcalendar.json'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.6',
}


def clean_text(value):
    if not value:
        return ''
    text = str(value)
    if '<' in text and '>' in text:
        text = BeautifulSoup(text, 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u3000', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_time(value):
    value = clean_text(value).replace('：', ':')
    match = re.search(r'(?<!\d)([0-2]?\d):([0-5]\d)', value)
    if not match:
        return None
    try:
        parsed = datetime.strptime(f'{match.group(1)}:{match.group(2)}', '%H:%M')
        # The calendar uses 0:00 as a sentinel when no start time is supplied.
        return None if parsed.hour == 0 else parsed.strftime('%H:%M')
    except ValueError:
        return None


def resolve_location(raw_venue):
    venue = clean_text(raw_venue)
    if not venue or venue == 'その他':
        return None, None
    if '京都' in venue or 'ロームシアター' in venue:
        return venue, 'Kyoto'
    # All remaining calendar venues are rooms within the New National Theatre
    # complex in Hatsudai. Prefixing short room names makes them unambiguous.
    if '新国立劇場' not in venue:
        venue = f'新国立劇場 {venue}'
    return venue, 'Tokyo'


def parse_occurrence(month, event):
    title = clean_text(event.get('title'))
    url = clean_text(event.get('url'))
    venue, city = resolve_location(event.get('altplace') or event.get('place'))
    if not all((title, url, venue, city)):
        return None
    try:
        year, month_number = (int(part) for part in month.split('/'))
        event_date = date(year, month_number, int(event.get('datenumber'))).isoformat()
    except (TypeError, ValueError):
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': parse_time(event.get('datetime2') or event.get('alttime')),
        'venue': venue,
        'city': city,
        'country_code': 'JP',
        'description': None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def fetch_description(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=45)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
    except requests.RequestException as error:
        log_message(
            'Failed to fetch New National Theatre event details',
            event='crawler_detail_fetch_failed', level='warning', url=url,
            error_type=type(error).__name__, error_message=str(error),
        )
        return url, None

    soup = BeautifulSoup(response.text, 'html.parser')
    content = soup.select_one('#content, main, article')
    if not content:
        return url, None
    for element in content.select(
        'script, style, nav, .gd__LocalNavWrap, .gd__Ticket, '
        '.gd__StaffModal, .st__Breadcrumb'
    ):
        element.decompose()
    description = clean_text(content)
    # Pages can contain very long biographies and ticket boilerplate. This still
    # preserves the introduction, programme, composer, and staff information.
    return url, description[:40000] or None


def get_concerts():
    try:
        response = requests.get(CALENDAR_URL, headers=HEADERS, timeout=60)
        response.raise_for_status()
        calendar = response.json()
    except (requests.RequestException, ValueError) as error:
        log_message(
            'Failed to fetch New National Theatre calendar',
            event='crawler_fetch_failed', level='error', url=CALENDAR_URL,
            error_type=type(error).__name__, error_message=str(error),
        )
        raise

    records = []
    for day in calendar:
        month = clean_text(day.get('datetime'))
        for event in day.get('dateballs') or []:
            record = parse_occurrence(month, event)
            if record:
                records.append(record)

    descriptions = {}
    urls = sorted({record['url'] for record in records})
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_description, url) for url in urls]
        for future in as_completed(futures):
            url, description = future.result()
            descriptions[url] = description
    for record in records:
        record['description'] = descriptions.get(record['url'])

    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class NnttJacGoJpCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='nntt_jac_go_jp',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='JP',
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
    NnttJacGoJpCrawler().run()


if __name__ == '__main__':
    main()
