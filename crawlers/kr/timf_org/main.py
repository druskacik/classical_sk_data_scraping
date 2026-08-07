import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.timf.org/kr/'
SOURCE = 'Tongyeong International Music Foundation'
LIST_URL = 'https://www.timf.org/kr/sub/ticket/show.asp'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.6',
}
LIST_PARAMS = {
    's_sort': '2',
    # Supplying the complete range exposes the site's entire retained archive.
    's_srdate': '1900-01-01',
    's_erdate': '2999-12-31',
}

CITY_MARKERS = {
    '서울': 'Seoul',
    '부산': 'Busan',
    '대구': 'Daegu',
    '인천': 'Incheon',
    '광주': 'Gwangju',
    '대전': 'Daejeon',
    '울산': 'Ulsan',
    '세종': 'Sejong',
    '창원': 'Changwon',
    '진주': 'Jinju',
    '거제': 'Geoje',
    '김해': 'Gimhae',
    '통영': 'Tongyeong',
}
SEOUL_VENUES = ('예술의전당', '롯데콘서트홀', '세종문화회관', '금호아트홀')


def clean_text(value):
    if not value:
        return ''
    return re.sub(r'\s+', ' ', str(value).replace('\xa0', ' ')).strip()


def parse_korean_time(value):
    value = clean_text(value)
    match = re.search(r'(오전|오후)\s*(\d{1,2})시(?:\s*(\d{1,2})분)?', value)
    if not match:
        return None
    hour = int(match.group(2))
    minute = int(match.group(3) or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    if match.group(1) == '오후' and hour != 12:
        hour += 12
    elif match.group(1) == '오전' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def city_for_venue(venue):
    for marker, city in CITY_MARKERS.items():
        if marker in venue:
            return city
    if any(marker in venue for marker in SEOUL_VENUES):
        return 'Seoul'
    # This is a venue calendar operated by the Tongyeong music foundation;
    # unqualified halls in its inventory are all in Tongyeong.
    return 'Tongyeong'


def parse_list_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for link in soup.select('a[href*="/kr/sub/ticket/view.asp"]'):
        item = link.find_parent('li')
        if item is None or item.select_one('li.date') is None:
            continue

        title = clean_text(link.get_text(' ', strip=True))
        date_node = item.select_one('li.date strong')
        venue_node = item.select_one('li.loca strong')
        time_node = item.select_one('li.time strong')
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', clean_text(date_node.get_text()) if date_node else '')
        venue = clean_text(venue_node.get_text(' ', strip=True)) if venue_node else ''
        if not title or not date_match or not venue:
            continue
        try:
            event_date = date.fromisoformat(date_match.group(0)).isoformat()
        except ValueError:
            continue

        records.append({
            'title': title,
            'date': event_date,
            'url': urljoin(SOURCE_URL, link.get('href', '').strip()),
            'time_from': parse_korean_time(time_node.get_text()) if time_node else None,
            'venue': venue,
            'city': city_for_venue(venue),
            'country_code': 'KR',
            # Most detail bodies are programme posters embedded as images and
            # therefore expose no reliable text beyond the list metadata.
            'description': None,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def page_count(html):
    soup = BeautifulSoup(html, 'html.parser')
    pages = [1]
    for link in soup.select('a[href*="page="]'):
        match = re.search(r'(?:\?|&)page=(\d+)', link.get('href', ''))
        if match:
            pages.append(int(match.group(1)))
    return max(pages)


def fetch_page(page):
    response = requests.get(
        LIST_URL,
        params={**LIST_PARAMS, 'page': page},
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    response.encoding = 'utf-8'
    return response.text


def get_concerts():
    first_html = fetch_page(1)
    total_pages = page_count(first_html)
    records = parse_list_page(first_html)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_page, page): page for page in range(2, total_pages + 1)}
        for future in as_completed(futures):
            page = futures[future]
            try:
                records.extend(parse_list_page(future.result()))
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch TIMF performance list page',
                    event='crawler_page_fetch_failed',
                    level='warning',
                    url=f'{LIST_URL}?page={page}',
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class TimfOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='timf_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='KR',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from'],
    )

    def scrape(self):
        return get_concerts()


def main():
    TimfOrgCrawler().run()


if __name__ == '__main__':
    main()
