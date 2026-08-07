import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.deutsches-theater.de/'
SOURCE = 'Deutsches Theater München'
CITY = 'München'
API_URL = f'{SOURCE_URL}wp-json/wp/v2'
POST_TYPES = ('mshows', 'ashows')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'januar': 1, 'februar': 2, 'märz': 3, 'maerz': 3, 'april': 4,
    'mai': 5, 'juni': 6, 'juli': 7, 'august': 8, 'september': 9,
    'oktober': 10, 'november': 11, 'dezember': 12,
}
WEEKDAYS = {
    'montag': 0, 'dienstag': 1, 'mittwoch': 2, 'donnerstag': 3,
    'freitag': 4, 'samstag': 5, 'sonntag': 6,
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(html.unescape(str(value)), 'html.parser').get_text('\n', strip=True)
    text = re.sub(r'\[(?:/?vc_[^\]]+|/?dt_[^\]]+)\]', '', text)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.json(), response.headers


def api_posts(session, post_type):
    params = {'per_page': 100, 'page': 1, '_fields': 'link,title,content'}
    posts = []
    while True:
        page, headers = get_json(session, f'{API_URL}/{post_type}', params)
        posts.extend(page)
        if params['page'] >= int(headers.get('X-WP-TotalPages', '1')):
            return posts
        params['page'] += 1


def parse_short_date(value):
    match = re.fullmatch(r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})', value.strip())
    if not match:
        return None
    day, month, year = map(int, match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def date_span(text):
    values = re.findall(r'\d{1,2}\.\d{1,2}\.(?:\d{2,4})?', text)
    if not values:
        return []
    end = parse_short_date(values[-1])
    start_value = values[0]
    if start_value.endswith('.') and end:
        start_value += str(end.year)
    start = parse_short_date(start_value)
    if not start or not end or end < start or (end - start).days > 370:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def fact(soup, label_name):
    for label in soup.find_all('label'):
        label_text = clean_text(label.get_text(' ', strip=True)).rstrip(':').lower()
        if label_text == label_name.lower():
            parent = label.find_parent(class_='block')
            if not parent:
                continue
            right = parent.select_one('.fakten-vorstellung-rechts, .col')
            if right:
                return clean_text(right)
    return ''


def weekday_times(value):
    result = {}
    normalized = clean_text(value).lower()
    for line in normalized.splitlines():
        time_match = re.search(r'(\d{1,2}):(\d{2})', line)
        if not time_match:
            continue
        time_value = f'{int(time_match.group(1)):02d}:{time_match.group(2)}'
        found = [number for name, number in WEEKDAYS.items() if name in line]
        if '-' in line and len(found) >= 2:
            first, last = found[0], found[-1]
            found = list(range(first, last + 1)) if first <= last else []
        for number in found:
            result[number] = time_value
    return result


def cancelled_dates(text):
    cancelled = set()
    for sentence in re.split(r'(?<=[.!?])\s+', clean_text(text)):
        if re.search(r'abgesagt|entfällt', sentence, re.I):
            for value in re.findall(r'\d{1,2}\.\d{1,2}\.\d{2,4}', sentence):
                parsed = parse_short_date(value)
                if parsed:
                    cancelled.add(parsed)
    return cancelled


def description_from(soup):
    parts = []
    # The REST API returns Visual Composer shortcodes mixed with HTML. Some
    # paragraph tags therefore become siblings of their nominal containers,
    # so limiting this to `.wpb_text_column p` would lose most programme copy.
    for element in soup.select('h2, h3, p'):
        if element.find_parent(class_=re.compile(r'dt-tickets|fakten')):
            continue
        text = clean_text(element)
        if not text or text in parts:
            continue
        if re.search(
            r'^(Tickets?|Preise?:|WEITERE DETAILS|ZUM SEITENANFANG|'
            r'ggf\. inkl\. MwSt|Aus unseren? News)', text, re.I,
        ):
            continue
        parts.append(text)
    return '\n\n'.join(parts) or None


def records_from_post(post):
    raw_content = (post.get('content') or {}).get('rendered') or ''
    soup = BeautifulSoup(html.unescape(raw_content), 'html.parser')
    title = clean_text((post.get('title') or {}).get('rendered'))
    url = post.get('link') or ''
    date_element = soup.select_one('.zeitraum-generisch')
    venue = fact(soup, 'Ort')
    beginning = fact(soup, 'Vorstellungsbeginn')
    if not title or not url or not date_element or not venue:
        return []

    dates = date_span(clean_text(date_element))
    times = weekday_times(beginning)
    single_time_match = re.fullmatch(r'.*?(\d{1,2}):(\d{2})(?:\s*Uhr)?', beginning)
    single_time = (
        f'{int(single_time_match.group(1)):02d}:{single_time_match.group(2)}'
        if single_time_match else None
    )
    cancelled = cancelled_dates(raw_content)
    description = description_from(soup)
    records = []
    for event_date in dates:
        if event_date in cancelled:
            continue
        if times and event_date.weekday() not in times:
            continue
        records.append({
            'title': title,
            'date': event_date.isoformat(),
            'url': url,
            'time_from': times.get(event_date.weekday(), single_time),
            'venue': venue,
            'city': CITY,
            'country_code': 'DE',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    posts = []
    with ThreadPoolExecutor(max_workers=len(POST_TYPES)) as executor:
        futures = {executor.submit(api_posts, session, post_type): post_type for post_type in POST_TYPES}
        for future in as_completed(futures):
            post_type = futures[future]
            try:
                posts.extend(future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to fetch programme API', event='crawler_page_failed', level='warning',
                    url=f'{API_URL}/{post_type}', error_type=type(error).__name__,
                    error_message=str(error),
                )
                raise

    records = []
    for post in posts:
        records.extend(records_from_post(post))
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class DeutschesTheaterDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='deutsches_theater_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
    DeutschesTheaterDeCrawler().run()


if __name__ == '__main__':
    main()
