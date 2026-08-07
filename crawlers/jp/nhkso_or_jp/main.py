import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.nhkso.or.jp/'
SOURCE = 'NHK Symphony Orchestra'
ARCHIVE_INDEX_URL = urljoin(SOURCE_URL, 'concert/json/list_archive_concert.json')
HALLS_URL = urljoin(SOURCE_URL, 'concert/hall/json/hall_all.json')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.6',
}

# The API has no address field. Its event titles and hall names do, however,
# identify nearly all touring cities. Longer/specific hints precede broader ones.
CITY_HINTS = {
    'ブロッセル': ('Brussels', 'BE'), 'アントワープ': ('Antwerp', 'BE'),
    'アムステルダム': ('Amsterdam', 'NL'), 'インスブルック': ('Innsbruck', 'AT'),
    'ドレスデン': ('Dresden', 'DE'), 'ウィーン': ('Vienna', 'AT'),
    'プラハ': ('Prague', 'CZ'), 'シンガポール': ('Singapore', 'SG'),
    '台北': ('Taipei', 'TW'), '台中': ('Taichung', 'TW'), '高雄': ('Kaohsiung', 'TW'),
    '東広島': ('Higashihiroshima', 'JP'), '八王子': ('Hachioji', 'JP'),
    '西宮': ('Nishinomiya', 'JP'), '郡山': ('Koriyama', 'JP'),
    '横浜': ('Yokohama', 'JP'), '川崎': ('Kawasaki', 'JP'),
    '所沢': ('Tokorozawa', 'JP'), '足利': ('Ashikaga', 'JP'),
    '葛飾': ('Tokyo', 'JP'), 'かつしか': ('Tokyo', 'JP'), '練馬': ('Tokyo', 'JP'),
    '池袋': ('Tokyo', 'JP'), '調布': ('Chofu', 'JP'), 'Chofu': ('Chofu', 'JP'),
    '多摩': ('Tama', 'JP'), '松戸': ('Matsudo', 'JP'), '浦安': ('Urayasu', 'JP'),
    '市川': ('Ichikawa', 'JP'), 'Ichikawa': ('Ichikawa', 'JP'),
    '厚木': ('Atsugi', 'JP'), '鎌倉': ('Kamakura', 'JP'), '成田': ('Narita', 'JP'),
    '大宮': ('Saitama', 'JP'), '埼玉': ('Saitama', 'JP'), '越谷': ('Koshigaya', 'JP'),
    '宇都宮': ('Utsunomiya', 'JP'), '高崎': ('Takasaki', 'JP'),
    'いわき': ('Iwaki', 'JP'), '水戸': ('Mito', 'JP'), '茨城': ('Mito', 'JP'),
    '宮崎': ('Miyazaki', 'JP'), '大分': ('Oita', 'JP'), '熊本': ('Kumamoto', 'JP'),
    '福岡': ('Fukuoka', 'JP'), '直方': ('Nogata', 'JP'), '沖縄': ('Ginowan', 'JP'),
    '和歌山': ('Wakayama', 'JP'), '堺': ('Sakai', 'JP'), '大阪': ('Osaka', 'JP'),
    '京都': ('Kyoto', 'JP'), '姫路': ('Himeji', 'JP'), '舞鶴': ('Maizuru', 'JP'),
    '大津': ('Otsu', 'JP'), '岩国': ('Iwakuni', 'JP'), '倉敷': ('Kurashiki', 'JP'),
    '三原': ('Mihara', 'JP'), '広島': ('Hiroshima', 'JP'), '呉': ('Kure', 'JP'),
    '松山': ('Matsuyama', 'JP'), '高知': ('Kochi', 'JP'), '高松': ('Takamatsu', 'JP'),
    '鳥取': ('Tottori', 'JP'), '名古屋': ('Nagoya', 'JP'), '愛知': ('Nagoya', 'JP'),
    '豊田': ('Toyota', 'JP'), '刈谷': ('Kariya', 'JP'), '幸田': ('Kota', 'JP'),
    '土岐': ('Toki', 'JP'), '福井': ('Fukui', 'JP'), 'ふくい': ('Fukui', 'JP'),
    'おおみや': ('Saitama', 'JP'), '金沢': ('Kanazawa', 'JP'),
    '富山': ('Toyama', 'JP'), '新潟': ('Niigata', 'JP'), '長岡': ('Nagaoka', 'JP'),
    '静岡': ('Shizuoka', 'JP'), '富士': ('Fuji', 'JP'), '上田': ('Ueda', 'JP'),
    '松本': ('Matsumoto', 'JP'), '軽井沢': ('Karuizawa', 'JP'), '伊那': ('Ina', 'JP'),
    '長野': ('Nagano', 'JP'), '甲府': ('Kofu', 'JP'), '山形': ('Yamagata', 'JP'),
    '仙台': ('Sendai', 'JP'), '福島': ('Fukushima', 'JP'), '秋田': ('Akita', 'JP'),
    '盛岡': ('Morioka', 'JP'), '弘前': ('Hirosaki', 'JP'), '青森': ('Aomori', 'JP'),
    '札幌': ('Sapporo', 'JP'), '旭川': ('Asahikawa', 'JP'), '帯広': ('Obihiro', 'JP'),
    '東京': ('Tokyo', 'JP'), '渋谷': ('Tokyo', 'JP'), '上野': ('Tokyo', 'JP'),
    'すみだ': ('Tokyo', 'JP'), '府中': ('Fuchu', 'JP'),
}

HOME_HALL_HINTS = {
    'NHKホール': 'Tokyo', 'サントリーホール': 'Tokyo',
    'Bunkamuraオーチャードホール': 'Tokyo', 'Hakuju Hall': 'Tokyo',
    'ザ・シンフォニーホール': 'Osaka', 'フェスティバルホール': 'Osaka',
    'ソニックシティ': 'Saitama', 'グランシップ': 'Shizuoka',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u3000', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def parse_time(value):
    value = clean_text(value).upper().replace(' ', '')
    if not value:
        return None
    for pattern in ('%I:%M%p', '%H:%M'):
        try:
            return datetime.strptime(value, pattern).strftime('%H:%M')
        except ValueError:
            pass
    return None


def resolve_location(title, venue):
    text = f'{title}\n{venue}'
    for hint, location in CITY_HINTS.items():
        if hint in text:
            return location
    for hint, city in HOME_HALL_HINTS.items():
        if hint in venue:
            return city, 'JP'
    return None, None


def description(event):
    parts = []
    for value in (event.get('catchcopy'), event.get('change_information'),
                  event.get('chamber_music_title'), event.get('chamber_music_text')):
        text = clean_text(value)
        if text and text not in parts:
            parts.append(text)
    performers = [clean_text(item.get('text')) for item in event.get('main_performers') or []]
    performers = [item for item in performers if item]
    if performers:
        parts.append('出演者\n' + '\n'.join(performers))
    works = [clean_text(item.get('name')) for item in event.get('songs') or []]
    works = [item for item in works if item]
    if works:
        parts.append('曲目\n' + '\n'.join(works))
    return '\n\n'.join(parts) or None


def event_records(event, halls):
    title = clean_text(event.get('title'))
    url = urljoin(SOURCE_URL, event.get('details_url') or '')
    venue = clean_text((halls.get(str(event.get('hall_id'))) or {}).get('name'))
    city, country_code = resolve_location(title, venue)
    if not all((title, event.get('details_url'), venue, city, country_code)):
        return []
    records = []
    for occurrence in event.get('details') or []:
        try:
            event_date = date.fromisoformat(clean_text(occurrence.get('date'))).isoformat()
        except ValueError:
            continue
        records.append({
            'title': title, 'date': event_date, 'url': url,
            'time_from': parse_time(occurrence.get('start_time')),
            'venue': venue, 'city': city, 'country_code': country_code,
            'description': description(event), 'source_url': SOURCE_URL, 'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        index = get_json(session, ARCHIVE_INDEX_URL)
        halls = get_json(session, HALLS_URL).get('hall_info') or {}
        events = []
        for archive_path in index.get('year_list') or []:
            events.extend(get_json(session, urljoin(SOURCE_URL, archive_path)).get('concerts') or [])
    except (requests.RequestException, ValueError) as error:
        log_message(
            'Failed to fetch NHK Symphony Orchestra concert archive',
            event='crawler_fetch_failed', level='error', url=ARCHIVE_INDEX_URL,
            error_type=type(error).__name__, error_message=str(error),
        )
        raise
    records = [record for event in events for record in event_records(event, halls)]
    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class NhksoOrJpCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='nhkso_or_jp', source=SOURCE, source_url=SOURCE_URL,
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
    NhksoOrJpCrawler().run()


if __name__ == '__main__':
    main()
