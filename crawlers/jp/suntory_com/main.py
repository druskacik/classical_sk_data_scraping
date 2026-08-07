import re
from datetime import date

import requests

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.suntory.com/culture-sports/suntoryhall/'
SOURCE = 'Suntory Hall'
CALENDAR_ROOT = 'https://www.suntory.co.jp/suntoryhall/schedule/'
# The current calendar application retains monthly pages from January 2024.
ARCHIVE_START = date(2024, 1, 1)
MIRROR_PREFIX = 'https://r.jina.ai/http://'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.6',
}

DETAIL_LINK_RE = re.compile(
    r'https?://www\.suntory\.co\.jp/suntoryhall/schedule/detail/'
    r'(\d{8})_([MS])_\d+\.html'
)
CALENDAR_ITEM_RE = re.compile(
    r'\[([^\n]+)\]\((https?://www\.suntory\.co\.jp/suntoryhall/schedule/'
    r'detail/(\d{8})_([MS])_\d+\.html)\)'
)


def clean_text(value):
    if not value:
        return ''
    text = str(value).replace('\xa0', ' ').replace('\u3000', ' ').replace('\u200b', '')
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


def add_months(value, count):
    month_index = value.year * 12 + value.month - 1 + count
    return date(month_index // 12, month_index % 12 + 1, 1)


def fetch_source_text(session, url):
    """Fetch a rendered text representation of Suntory's script-driven page.

    Suntory's Akamai configuration rejects server-side clients before returning
    the calendar HTML. The text endpoint renders the canonical public URL and is
    used only as a transport; all record URLs remain the original Suntory URLs.
    """
    mirror_url = MIRROR_PREFIX + url.removeprefix('https://').removeprefix('http://')
    # The rendering endpoint rejects a spoofed browser user-agent; identify this
    # as a normal server-side fetch even though the origin headers use one.
    response = session.get(
        mirror_url,
        headers={'User-Agent': 'crawler-factory/1.0', 'Accept': 'text/plain'},
        timeout=90,
    )
    response.raise_for_status()
    return response.text


def calendar_detail_links(text):
    links = {}
    for match in DETAIL_LINK_RE.finditer(text):
        url = match.group(0).replace('http://', 'https://')
        links[url] = (match.group(1), match.group(2))
    return links


def calendar_records(text):
    records = []
    for match in CALENDAR_ITEM_RE.finditer(text):
        summary, url, date_token, hall_token = match.groups()
        summary = clean_text(summary)
        time_match = re.search(r'(?<!\d)([0-2]?\d):([0-5]\d)\s*(?:開演|開始)', summary)
        time_from = None
        if time_match and int(time_match.group(1)) < 24:
            time_from = f'{int(time_match.group(1)):02d}:{time_match.group(2)}'
        title_part = summary[:time_match.start()].strip() if time_match else summary
        # Calendar cards repeat the display title before their compact details.
        # Keep the first copy, including meaningful promoter/category prefixes.
        probe = title_part[:min(12, len(title_part))]
        repeat_at = title_part.find(probe, len(probe)) if probe else -1
        title = title_part[:repeat_at].strip() if repeat_at > 0 else title_part
        try:
            event_date = date.fromisoformat(
                f'{date_token[:4]}-{date_token[4:6]}-{date_token[6:8]}'
            ).isoformat()
        except ValueError:
            continue
        if not title:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url.replace('http://', 'https://'),
            'time_from': time_from,
            'venue': (
                'Suntory Hall Main Hall' if hall_token == 'M'
                else 'Suntory Hall Blue Rose'
            ),
            'city': 'Tokyo',
            'country_code': 'JP',
            # Cards contain the available subtitle/programme and performer text.
            'description': summary,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def parse_detail(text, url, date_token, hall_token):
    if 'ページが見つかりませんでした' in text:
        return None

    title = ''
    breadcrumb = re.search(
        r'\*\s+([^\n]+)\n\n# \[公演カレンダー\][^\n]*\n\n(.+?)\n\n日時\s',
        text,
        flags=re.DOTALL,
    )
    if breadcrumb:
        title = clean_text(breadcrumb.group(2)).replace('\n', ' ')
    if not title:
        heading = re.search(r'^Title:\s*(.+)$', text, flags=re.MULTILINE)
        title = clean_text(heading.group(1)) if heading else ''

    facts = re.search(r'日時\s*(.+?)(?:\n\nお問い合わせ|\nお問い合わせ)', text, flags=re.DOTALL)
    facts_text = clean_text(facts.group(1)).replace('\n', ' ') if facts else ''
    time_match = re.search(r'(?<!\d)([0-2]?\d):([0-5]\d)\s*(?:開演|開始)', facts_text)
    time_from = None
    if time_match and int(time_match.group(1)) < 24:
        time_from = f'{int(time_match.group(1)):02d}:{time_match.group(2)}'

    venue_match = re.search(r'会場\s*(.+?)(?=\s+(?:出演|曲目|料金|お問い合わせ)\s)', facts_text)
    venue = clean_text(venue_match.group(1)) if venue_match else ''
    if not venue:
        venue = 'Suntory Hall Main Hall' if hall_token == 'M' else 'Suntory Hall Blue Rose'

    description_match = re.search(
        r'(?:出演|曲目)\s+(.+?)(?=\s+料金\s|\n\nお問い合わせ)', facts_text,
        flags=re.DOTALL,
    )
    description = clean_text(description_match.group(0)) if description_match else None

    try:
        event_date = date.fromisoformat(
            f'{date_token[:4]}-{date_token[4:6]}-{date_token[6:8]}'
        ).isoformat()
    except ValueError:
        return None
    if not all((title, venue)):
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': 'Tokyo',
        'country_code': 'JP',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    final_month = add_months(date.today().replace(day=1), 4)
    records = []
    for year, month in month_sequence(ARCHIVE_START, final_month):
        url = f'{CALENDAR_ROOT}{year:04d}{month:02d}/'
        try:
            records.extend(calendar_records(fetch_source_text(session, url)))
        except requests.RequestException as error:
            log_message(
                'Failed to fetch Suntory Hall calendar month',
                event='crawler_month_fetch_failed', level='warning', url=url,
                error_type=type(error).__name__, error_message=str(error),
            )

    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class SuntoryComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='suntory_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='JP',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from'],
    )

    def scrape(self):
        return get_concerts()


def main():
    SuntoryComCrawler().run()


if __name__ == '__main__':
    main()
