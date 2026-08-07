import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.tmso.or.jp/'
SOURCE = 'Tokyo Metropolitan Symphony Orchestra'
ARCHIVE_URL = urljoin(SOURCE_URL, 'j/archives/concert/list.php')
CURRENT_URL = urljoin(SOURCE_URL, 'j/concert/')
ARCHIVE_START = date(1965, 1, 1)
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.6',
}

# Explicit city names take precedence. The second table covers Tokyo halls whose
# names do not contain a locality; unknown touring venues are deliberately skipped.
CITY_HINTS = {
    '立川': 'Tachikawa', '八王子': 'Hachioji', '府中': 'Fuchu',
    '調布': 'Chofu', '多摩': 'Tama', '町田': 'Machida', '武蔵野': 'Musashino',
    '横浜': 'Yokohama', '川崎': 'Kawasaki', '相模原': 'Sagamihara',
    'さいたま': 'Saitama', '大宮': 'Saitama', '所沢': 'Tokorozawa',
    '千葉': 'Chiba', '市川': 'Ichikawa', '松戸': 'Matsudo',
    '札幌': 'Sapporo', '仙台': 'Sendai', '水戸': 'Mito', '宇都宮': 'Utsunomiya',
    '高崎': 'Takasaki', '長野': 'Nagano', '松本': 'Matsumoto',
    '新潟': 'Niigata', '金沢': 'Kanazawa', '静岡': 'Shizuoka',
    '名古屋': 'Nagoya', '京都': 'Kyoto', '大阪': 'Osaka', '神戸': 'Kobe',
    '広島': 'Hiroshima', '福岡': 'Fukuoka', '沖縄': 'Okinawa',
    '東京': 'Tokyo', '上野': 'Tokyo', '池袋': 'Tokyo', '渋谷': 'Tokyo',
}
TOKYO_HALL_HINTS = (
    'サントリーホール', '東京文化会館', '東京芸術劇場', 'すみだトリフォニーホール',
    '紀尾井ホール', 'オペラシティ', 'NHKホール', '日比谷公会堂', '杉並公会堂',
    '新宿文化センター', '文京シビックホール', 'ティアラこうとう',
    'めぐろパーシモンホール', '世田谷区民会館', '練馬文化センター',
    '国立劇場', '国立音楽大学', '第一生命ホール', '浜離宮朝日ホール',
)


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u3000', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def month_sequence(start, end):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        month += 1
        if month == 13:
            year, month = year + 1, 1


def resolve_city(venue):
    for hint, city in CITY_HINTS.items():
        if hint in venue:
            return city
    if any(hint in venue for hint in TOKYO_HALL_HINTS):
        return 'Tokyo'
    return None


def parse_archive_page(html, year, month, url):
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for row in soup.select('tr'):
        heading = row.select_one('.detailBox h3')
        date_cell = row.find('th')
        venue_cell = row.select_one('.hallBox')
        if not all((heading, date_cell, venue_cell)):
            continue
        title, venue = clean_text(heading), clean_text(venue_cell)
        city = resolve_city(venue)
        date_time = clean_text(date_cell)
        match = re.search(
            r'(\d{1,2})/(\d{1,2})[\s\S]*?(\d{1,2}):(\d{2})', date_time
        )
        if not match or not all((title, venue, city)):
            continue
        try:
            event_date = date(year, int(match.group(1)), int(match.group(2))).isoformat()
            time_from = datetime.strptime(
                f'{match.group(3)}:{match.group(4)}', '%H:%M'
            ).strftime('%H:%M')
        except ValueError:
            continue
        details = row.select_one('.detailBox dl')
        records.append({
            'title': title, 'date': event_date, 'url': url,
            'time_from': time_from, 'venue': venue, 'city': city,
            'country_code': 'JP', 'description': clean_text(details) or None,
            'source_url': SOURCE_URL, 'source': SOURCE,
        })
    return records


def parse_current_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for link in soup.select('a[href*="/j/concert/detail/detail.php"]'):
        links.append(urljoin(SOURCE_URL, link.get('href')))
    next_link = soup.find('a', string=lambda value: clean_text(value) == '>')
    return list(dict.fromkeys(links)), urljoin(CURRENT_URL, next_link['href']) if next_link else None


def parse_detail_page(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title = next((clean_text(node) for node in soup.select('h1') if clean_text(node)), '')
    text = clean_text(soup)
    date_match = re.search(r'(20\d{2})年(\d{1,2})月(\d{1,2})日', text)
    time_match = re.search(r'(\d{1,2}):(\d{2})開演', text)
    venue = ''
    for selector in ('.hall', '.venue', '.place'):
        node = soup.select_one(selector)
        if node:
            venue = clean_text(node)
            break
    if not venue:
        venue_match = re.search(
            r'(?:会場|公演場所|場所)[／：:\s]+(.+?)(?=\s+ホール案内|\n)', text
        )
        venue = clean_text(venue_match.group(1)) if venue_match else ''
    city = resolve_city(venue)
    if not all((title, date_match, venue, city)):
        return None
    try:
        event_date = date(*map(int, date_match.groups())).isoformat()
    except ValueError:
        return None
    time_from = None
    if time_match and int(time_match.group(1)) < 24:
        time_from = f'{int(time_match.group(1)):02d}:{time_match.group(2)}'
    description_parts = []
    for heading in soup.find_all(['dt', 'h2', 'h3', 'h4']):
        if any(word in clean_text(heading) for word in ('出演', '曲目', 'プログラム')):
            sibling = heading.find_next_sibling()
            if sibling:
                value = clean_text(sibling)
                if value and value not in description_parts:
                    description_parts.append(f'{clean_text(heading)}\n{value}')
    works = [clean_text(node) for node in soup.select('.program-title')]
    works = [work for work in works if work]
    if works:
        description_parts.append('曲目\n' + '\n'.join(dict.fromkeys(works)))
    return {
        'title': title, 'date': event_date, 'url': url, 'time_from': time_from,
        'venue': venue, 'city': city, 'country_code': 'JP',
        'description': '\n\n'.join(description_parts) or None,
        'source_url': SOURCE_URL, 'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    today = date.today()
    archive_months = list(month_sequence(ARCHIVE_START, today.replace(day=1)))

    def fetch_archive(year, month):
        url = f'{ARCHIVE_URL}?ym={year:04d}{month:02d}'
        try:
            response = requests.get(url, headers=HEADERS, timeout=45)
            response.raise_for_status()
            return parse_archive_page(response.text, year, month, url)
        except requests.RequestException as error:
            log_message(
                'Failed to fetch TMSO archive month', event='crawler_month_fetch_failed',
                level='warning', url=url, error_type=type(error).__name__,
                error_message=str(error),
            )
            return []

    # The archive is one small HTML document per month back to 1965. A modest
    # pool keeps a complete historical scrape practical without high concurrency.
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_archive, *item) for item in archive_months]
        for future in as_completed(futures):
            records.extend(future.result())

    page_url = CURRENT_URL
    seen_pages = set()
    detail_urls = []
    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        response = session.get(page_url, timeout=45)
        response.raise_for_status()
        links, page_url = parse_current_page(response.text)
        detail_urls.extend(links)
    for url in dict.fromkeys(detail_urls):
        try:
            response = session.get(url, timeout=45)
            response.raise_for_status()
            record = parse_detail_page(response.text, url)
            if record:
                records.append(record)
        except requests.RequestException as error:
            log_message(
                'Failed to fetch TMSO concert detail', event='crawler_detail_fetch_failed',
                level='warning', url=url, error_type=type(error).__name__,
                error_message=str(error),
            )
    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class TmsoOrJpCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='tmso_or_jp', source=SOURCE, source_url=SOURCE_URL,
        country_code='JP', upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    TmsoOrJpCrawler().run()


if __name__ == '__main__':
    main()
