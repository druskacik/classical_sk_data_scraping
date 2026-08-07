import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://npac-nso.org/'
SOURCE = 'National Symphony Orchestra (Taiwan)'
DATES_URL = urljoin(SOURCE_URL, 'getAllDates')
EVENTS_URL = urljoin(SOURCE_URL, 'getEvents')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.6',
    'Referer': SOURCE_URL,
}

# The calendar includes NSO tours as well as its Taipei season. Specific tour
# halls precede the home-hall defaults so every record keeps its real geography.
LOCATION_HINTS = (
    ('熊本', 'Kumamoto', 'JP'),
    ('大阪', 'Osaka', 'JP'),
    ('三得利', 'Tokyo', 'JP'),
    ('東京歌劇城', 'Tokyo', 'JP'),
    ('橫濱', 'Yokohama', 'JP'),
    ('國立音樂大學', 'Tachikawa', 'JP'),
    ('ACROS福岡', 'Fukuoka', 'JP'),
    ('釜山', 'Busan', 'KR'),
    ('德勒斯登', 'Dresden', 'DE'),
    ('柏林', 'Berlin', 'DE'),
    ('倫敦', 'London', 'GB'),
    ('諾里奇', 'Norwich', 'GB'),
    ('謝菲爾德', 'Sheffield', 'GB'),
    ('洛杉磯', 'Los Angeles', 'US'),
    ('鳳凰城', 'Phoenix', 'US'),
    ('紐約', 'New York', 'US'),
    ('伯明罕', 'Birmingham', 'GB'),
    ('衛武營', 'Kaohsiung', 'TW'),
    ('臺中國家歌劇院', 'Taichung', 'TW'),
    ('國立東華大學', 'Hualien', 'TW'),
    ('苗北藝文中心', 'Zhunan', 'TW'),
    ('臺南文化中心', 'Tainan', 'TW'),
    ('新竹', 'Hsinchu', 'TW'),
    ('新港', 'Xingang', 'TW'),
    ('嘉義', 'Chiayi', 'TW'),
    ('雲林', 'Douliu', 'TW'),
    ('臺北表演藝術中心', 'Taipei', 'TW'),
    ('臺北藝術大學', 'Taipei', 'TW'),
    ('國家音樂廳', 'Taipei', 'TW'),
    ('國家演奏廳', 'Taipei', 'TW'),
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u3000', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def resolve_location(venue):
    venue = clean_text(venue)
    if not venue or venue == 'OPENTIX':
        return None, None
    for marker, city, country_code in LOCATION_HINTS:
        if marker in venue:
            return city, country_code
    return None, None


def parse_detail_description(html):
    soup = BeautifulSoup(html, 'html.parser')
    parts = []
    for selector, heading in (
        ('.concert-block.program .content', '演出曲目'),
        ('.concert-block.info .content', '節目介紹'),
    ):
        node = soup.select_one(selector)
        text = clean_text(node.decode_contents()) if node else ''
        if text:
            parts.append(f'{heading}\n{text}')
    return '\n\n'.join(parts) or None


def fetch_catalogue(session):
    response = session.get(DATES_URL, timeout=45)
    response.raise_for_status()
    payload = response.json()
    dates = (payload.get('data') or {}).values()
    months = sorted({value[:7] for value in dates if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)})

    events = []
    for month in months:
        response = session.post(EVENTS_URL, json={'event_date': month}, timeout=45)
        response.raise_for_status()
        events.extend(response.json().get('data') or [])
    return events


def fetch_description(event_id):
    url = urljoin(SOURCE_URL, f'concert-info/{event_id}')
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return parse_detail_description(response.text)


def get_descriptions(event_ids):
    descriptions = {}
    # The origin throttles larger bursts of concurrent detail requests.
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_description, event_id): event_id for event_id in event_ids}
        for future in as_completed(futures):
            event_id = futures[future]
            try:
                descriptions[event_id] = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to fetch NSO concert detail',
                    event='crawler_detail_fetch_failed',
                    level='warning',
                    url=urljoin(SOURCE_URL, f'concert-info/{event_id}'),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                descriptions[event_id] = None
    return descriptions


def event_record(event, descriptions):
    title = clean_text(event.get('prod_name'))
    venue = clean_text(event.get('location'))
    city, country_code = resolve_location(venue)
    event_id = event.get('prod_id')
    if not all((title, venue, city, country_code, event_id)):
        return None
    try:
        start = datetime.strptime(clean_text(event.get('event_start')), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return {
        'title': title,
        'date': start.strftime('%Y-%m-%d'),
        'url': urljoin(SOURCE_URL, f'concert-info/{event_id}'),
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': descriptions.get(event_id),
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        events = fetch_catalogue(session)
    except (requests.RequestException, ValueError) as error:
        log_message(
            'Failed to fetch NSO concert calendar',
            event='crawler_fetch_failed',
            level='error',
            url=EVENTS_URL,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        raise

    event_ids = {event.get('prod_id') for event in events if event.get('prod_id')}
    descriptions = get_descriptions(event_ids)
    records = [event_record(event, descriptions) for event in events]
    records = [record for record in records if record]
    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class NpacNsoOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='npac_nso_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='TW',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['url', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    NpacNsoOrgCrawler().run()


if __name__ == '__main__':
    main()
