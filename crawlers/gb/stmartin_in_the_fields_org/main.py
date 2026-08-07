import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.stmartin-in-the-fields.org/'
LISTING_URL = f'{SOURCE_URL}whats-on/'
AJAX_URL = f'{SOURCE_URL}wp-admin/admin-ajax.php'
SOURCE = 'St Martin-in-the-Fields'
VENUE = 'St Martin-in-the-Fields'
CITY = 'London'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}
MONTHS = {
    month.lower(): number
    for number, month in enumerate(
        ('', 'January', 'February', 'March', 'April', 'May', 'June',
         'July', 'August', 'September', 'October', 'November', 'December')
    ) if month
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def listing_items(session):
    response = session.post(
        AJAX_URL,
        data={
            'action': 'whatson_filter',
            'search': '',
            'category[]': 'concerts',
            'date-from': '',
            'date-to': '',
        },
        headers={'X-Requested-With': 'XMLHttpRequest'},
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get('success') or not isinstance(payload.get('data'), dict):
        raise ValueError('Unexpected response from the concert listing endpoint')
    return BeautifulSoup(payload['data'].get('html') or '', 'html.parser').select(
        'li.WhatsonItem'
    )


def parse_performance(value, today=None):
    today = today or date.today()
    text = re.sub(r'(\d)(?:st|nd|rd|th)\b', r'\1', clean_text(value), flags=re.I)
    match = re.search(
        r'\b(\d{1,2})\s+(' + '|'.join(MONTHS) + r')'
        r'(?:\s+(\d{4}))?\s*,?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b',
        text,
        re.I,
    )
    if not match:
        return None
    year = int(match.group(3)) if match.group(3) else today.year
    hour = int(match.group(4)) % 12
    if match.group(6).lower() == 'pm':
        hour += 12
    minute = int(match.group(5) or 0)
    try:
        event_date = date(year, MONTHS[match.group(2).lower()], int(match.group(1)))
    except ValueError:
        return None
    return event_date.isoformat(), f'{hour:02d}:{minute:02d}'


def parse_performances(value, today=None):
    """Return every explicitly dated performance in a listing label."""
    today = today or date.today()
    text = re.sub(r'(\d)(?:st|nd|rd|th)\b', r'\1', clean_text(value), flags=re.I)
    single = parse_performance(text, today)
    if single:
        return [single]

    # Some seasonal listings collapse several days into one pipe-separated
    # label and intentionally say "Various Times". Times are optional records.
    month_match = re.search(r'\b(' + '|'.join(MONTHS) + r')\b', text, re.I)
    if not month_match:
        return []
    year_match = re.search(r'\b(20\d{2})\b', text)
    year = int(year_match.group(1)) if year_match else today.year
    month = MONTHS[month_match.group(1).lower()]
    prefix = text[:month_match.start()]
    if not ('|' in text or re.search(r'\d\s*-\s*\D*\d', prefix)):
        match = re.search(r'\b(\d{1,2})\s*$', prefix)
        days = [int(match.group(1))] if match else []
    else:
        days = [int(day) for day in re.findall(r'\b(\d{1,2})\b', prefix)]
    performances = []
    for day in days:
        try:
            performances.append((date(year, month, day).isoformat(), None))
        except ValueError:
            continue
    return list(dict.fromkeys(performances))


def listing_data(item):
    link = item.select_one('.WhatsonItemTitle h3 a[href]')
    performances = parse_performances(item.select_one('.EV_ListDate'))
    title = clean_text(link)
    url = (link.get('href') or '').split('#', 1)[0] if link else ''
    if not title or not url or not performances:
        return []
    return [(title, url, performance) for performance in performances]


def detail_description(session, url):
    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
    parts = []
    for selector in (
        '.whatson-event-details-content',
        '.Event_Accordion_Items',
    ):
        for node in soup.select(selector):
            text = clean_text(node)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = [
        data
        for item in listing_items(session)
        for data in listing_data(item)
    ]
    descriptions = {}
    urls = {item[1] for item in items}

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(detail_description, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                descriptions[url] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape St Martin-in-the-Fields concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                descriptions[url] = None

    records = [
        {
            'title': title,
            'date': performance[0],
            'url': url,
            'time_from': performance[1],
            'venue': VENUE,
            'city': CITY,
            'country_code': 'GB',
            'description': descriptions.get(url),
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for title, url, performance in items
    ]
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class StMartinInTheFieldsOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='stmartin_in_the_fields_org',
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
        return get_concerts()


def main():
    StMartinInTheFieldsOrgCrawler().run()


if __name__ == '__main__':
    main()
