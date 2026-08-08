import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.jeunesse.at/'
PROGRAMME_URL = urljoin(SOURCE_URL, 'programm/list')
SOURCE = 'Jeunesse'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_start(value):
    if not value:
        return None, None
    try:
        start = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None, None
    return start.date().isoformat(), start.strftime('%H:%M')


def listing_record(card):
    link = card.select_one('h2 a[href*="/programm/veranstaltungsdetails/"]')
    start = card.select_one('time[itemprop="startDate"]')
    location = card.select_one('[itemprop="location"]')
    city_node = location.select_one('[itemprop="addressRegion"]') if location else None
    venue_node = location.select_one('[itemprop="name"]') if location else None

    title = clean_text(link.get('title') or link) if link else ''
    url = urljoin(SOURCE_URL, link.get('href')) if link else ''
    event_date, time_from = parse_start(
        start.get('datetime') or start.get('content') if start else ''
    )
    city = clean_text(city_node)
    venue = clean_text(venue_node)
    if not title or not url or not event_date or not city or not venue:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'AT',
        'description': None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def listing_records(session):
    records = []
    seen_urls = set()
    for page in range(1, 101):
        soup = get_soup(
            session,
            PROGRAMME_URL,
            {'tx_news_pi1[currentPage]': page},
        )
        page_records = [listing_record(card) for card in soup.select('.article.event')]
        page_records = [record for record in page_records if record]
        new_records = [record for record in page_records if record['url'] not in seen_urls]
        if not new_records:
            break
        records.extend(new_records)
        seen_urls.update(record['url'] for record in new_records)

        page_numbers = [
            int(node.get_text(strip=True))
            for node in soup.select('.pagination .page-link')
            if node.get_text(strip=True).isdigit()
        ]
        if page_numbers and page >= max(page_numbers):
            break
    return records


def detail_description(session, url):
    soup = get_soup(session, url)
    detail = soup.select_one('.news-single')
    if not detail:
        return None

    parts = []
    for block in detail.select('.d-block.my-3, .d-block.mt-5.mb-3'):
        text = clean_text(block)
        heading = clean_text(block.find(['h2', 'h3', 'h4'])).lower()
        if not text or heading in {'weitere termine', 'dauer'}:
            continue
        if text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = listing_records(session)

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(detail_description, session, record['url']): record
            for record in records
        }
        for future in as_completed(futures):
            record = futures[future]
            try:
                record['description'] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Jeunesse concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=record['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class JeunesseAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='jeunesse_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
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
            'source_url',
            'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    JeunesseAtCrawler().run()


if __name__ == '__main__':
    main()
