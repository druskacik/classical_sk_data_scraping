import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.theater-wien.at/de/home'
CALENDAR_URL = 'https://www.theater-wien.at/de/kalendarium'
ARCHIVE_URL = 'https://www.theater-wien.at/de/archiv'
SOURCE = 'MusikTheater an der Wien'

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
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def production_urls(soup):
    urls = []
    for link in soup.select('a[href*="/spielplan/saison"][href]'):
        url = urljoin(SOURCE_URL, link.get('href', '').strip())
        if re.search(r'/spielplan/saison[^/]+/\d+/', url):
            urls.append(url.split('#', 1)[0])
    return list(dict.fromkeys(urls))


def production_description(soup):
    parts = []
    content = soup.select_one('.content-wrap')
    value = clean_text(content)
    if value:
        parts.append(value)

    # Cast and creative-team sections often contain the only explicit work or
    # composer credits, so retain them for later programme extraction.
    for section in soup.select('.econtent_wrap .extracontent-item'):
        heading = clean_text(section.select_one('h2, h3'))
        if heading.lower() not in {'besetzung', 'programm', 'program'}:
            continue
        value = clean_text(section)
        if value and value not in parts:
            parts.append(value)
    return clean_text('\n\n'.join(parts)) or None


def parse_start(row):
    meta = row.select_one('meta[itemprop="startDate"][content]')
    if meta:
        try:
            value = datetime.fromisoformat(meta['content'])
            return value.date().isoformat(), value.strftime('%H:%M')
        except ValueError:
            pass
    time = row.select_one('time[datetime]')
    if time:
        try:
            value = datetime.strptime(time['datetime'].strip(), '%d.%m.%Y %H:%M')
            return value.date().isoformat(), value.strftime('%H:%M')
        except ValueError:
            pass
    return None, None


def detail_records(url, soup):
    description = production_description(soup)
    fallback_title = clean_text(soup.select_one('h1.htitle'))
    records = []
    for row in soup.select('.eventdates tr.item'):
        event_date, time_from = parse_start(row)
        title = clean_text(row.select_one('[itemprop="name"]')) or fallback_title
        venue = clean_text(row.select_one('[itemprop="location"] [itemprop="name"]'))
        city = clean_text(row.select_one('[itemprop="addressLocality"]'))
        if not title or not event_date or not venue or not city:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'AT',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    calendar_soup = get_soup(CALENDAR_URL)
    archive_soup = get_soup(ARCHIVE_URL)
    urls = production_urls(calendar_soup)
    urls.extend(url for url in production_urls(archive_soup) if url not in urls)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(get_soup, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(detail_records(url, future.result()))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Theater an der Wien production',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    unique = {
        (record['title'], record['date'], record['time_from'], record['venue']): record
        for record in records
    }
    return sorted(
        unique.values(),
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class TheaterWienAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='theater_wien_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
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
    TheaterWienAtCrawler().run()


if __name__ == '__main__':
    main()
