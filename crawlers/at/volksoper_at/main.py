import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.volksoper.at/'
SCHEDULE_URL = urljoin(SOURCE_URL, 'spielplan/')
SOURCE = 'Volksoper Wien'
CITY = 'Wien'
VENUE = 'Volksoper Wien'

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


def get_soup(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def season_url(start_year):
    return urljoin(
        SCHEDULE_URL,
        f'saison-{start_year}-{start_year + 1}.de.html',
    )


def available_season_pages(session):
    # Archives remain published back to 2018/19. Probe backwards so the
    # crawler automatically picks up any newly exposed older season too.
    today = date.today()
    newest = today.year if today.month >= 7 else today.year - 1
    pages = []
    empty_count = 0
    # The following season is sometimes announced before the current one ends.
    year = newest + 1
    while empty_count < 2:
        url = season_url(year)
        try:
            soup = get_soup(session, url)
        except requests.HTTPError as error:
            # The site responds with 500, rather than 404, for seasons that
            # have no calendar page.
            status = (
                error.response.status_code if error.response is not None else None
            )
            if status not in (404, 500):
                raise
            empty_count += 1
            year -= 1
            continue
        if soup.select_one('article.event a[itemprop="url"][href*="/produktion/"]'):
            pages.append((url, soup))
            empty_count = 0
        else:
            empty_count += 1
        year -= 1
    return pages


def listing_record(article):
    link = article.select_one('a[itemprop="url"][href*="/produktion/"]')
    title_node = article.select_one('[itemprop="name"]')
    date_node = article.select_one('time[datetime]')
    start_node = article.select_one('[itemprop="startDate"][content]')
    location_node = article.select_one('meta[itemprop="location"]')
    if not all((link, title_node, date_node, location_node)):
        return None

    title = clean_text(title_node.get_text(' ', strip=True))
    event_date = (date_node.get('datetime') or '').strip()
    location = clean_text(location_node.get('content'))
    try:
        event_date = date.fromisoformat(event_date).isoformat()
    except ValueError:
        return None

    # Every published schedule entry currently uses the Volksoper building.
    # Do not silently assign Vienna to any future explicitly touring entry.
    if location.lower() != 'volksoper':
        return None

    start = (start_node.get('content') if start_node else '') or ''
    time_match = re.search(r'T(\d{2}:\d{2})', start)
    descriptions = [
        clean_text(node.get_text(' ', strip=True))
        for node in article.select('.description-wrapper .event-description')
    ]
    descriptions = [value for value in descriptions if value]
    url = urljoin(SOURCE_URL, link.get('href'))
    if not title or not url:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_match.group(1) if time_match else None,
        'venue': VENUE,
        'city': CITY,
        'country_code': 'AT',
        'description': '\n'.join(descriptions) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def detail_description(session, record):
    soup = get_soup(session, record['url'])
    meta = soup.select_one('meta[name="description"]')
    body = clean_text(meta.get('content')) if meta else ''
    summary = record.get('description') or ''
    if body and summary and summary not in body:
        return f'{summary}\n\n{body}'
    return body or summary or None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records_by_url = {}
    for _, soup in available_season_pages(session):
        for article in soup.select('article.event'):
            record = listing_record(article)
            if record:
                records_by_url[record['url']] = record

    records = list(records_by_url.values())
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(detail_description, session, record): record
            for record in records
        }
        for future in as_completed(futures):
            record = futures[future]
            try:
                record['description'] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=record['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['url']
        ),
    )


class VolksoperAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='volksoper_at',
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
        dedupe_subset=['url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    VolksoperAtCrawler().run()


if __name__ == '__main__':
    main()
