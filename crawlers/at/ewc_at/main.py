import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.ewc.at/'
CALENDAR_URL = urljoin(SOURCE_URL, 'konzerte-2/')
CALENDAR_API_URL = urljoin(SOURCE_URL, 'wp-json/wp/v2/pages')
SOURCE = 'Ensemble Wiener Collage'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'jänner': 1, 'januar': 1, 'februar': 2, 'märz': 3, 'april': 4,
    'mai': 5, 'juni': 6, 'juli': 7, 'august': 8, 'september': 9,
    'oktober': 10, 'november': 11, 'dezember': 12,
}

DATE_RE = re.compile(
    r'(?P<day>\d{1,2})\.\s*(?:(?P<month_name>[A-Za-zÄÖÜäöü]+)\s+|'
    r'(?P<month_number>\d{1,2})\.\s*)(?P<year>20\d{2})',
    re.IGNORECASE,
)
TIME_RE = re.compile(
    r'(?<!\d)(\d{1,2})[.:](\d{2})(?!\.\d{4})(?:\s*Uhr)?', re.IGNORECASE
)
CLOCK_RE = re.compile(
    r'(?<!\d)(\d{1,2})(?:[.:](\d{2}))?\s*Uhr\b', re.IGNORECASE
)
CITY_COUNTRIES = {
    'wien': ('Wien', 'AT'),
    'mödling': ('Mödling', 'AT'),
    'linz': ('Linz', 'AT'),
    'ascoli': ('Ascoli Piceno', 'IT'),
    'tel aviv': ('Tel Aviv', 'IL'),
    'jerusalem': ('Jerusalem', 'IL'),
}


def clean_text(value, separator=' '):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text(separator, strip=True)
    else:
        text = str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    return re.sub(r'\s+', ' ', text).strip()


def canonical_url(value):
    url = urljoin(SOURCE_URL, value or '')
    parsed = urlparse(url)
    if parsed.netloc.casefold() in {'ewc.at', 'www.ewc.at'}:
        path = parsed.path or '/'
        if not path.endswith('/'):
            path += '/'
        return f'https://www.ewc.at{path}'
    return url


def parse_date(match):
    month = (
        MONTHS.get((match.group('month_name') or '').casefold())
        or int(match.group('month_number') or 0)
    )
    try:
        return date(int(match.group('year')), month, int(match.group('day'))).isoformat()
    except (TypeError, ValueError):
        return None


def resolve_city(value):
    normalized = clean_text(value).strip(' ,:|').casefold()
    normalized = normalized.replace('wien/online', 'wien')
    for marker, result in CITY_COUNTRIES.items():
        if normalized == marker or re.search(rf'\b{re.escape(marker)}\b', normalized):
            return result
    return None, None


def listing_items(soup):
    items = []
    for row in soup.select('.x-accordion-group li'):
        text = clean_text(row)
        if re.search(r'\b(?:abgesagt|verschoben)\b', text, re.IGNORECASE):
            continue
        matches = list(DATE_RE.finditer(text))
        if not matches:
            continue

        link = row.select_one('a[href]:not([href="#"])')
        if not link:
            continue
        title = clean_text(link)
        url = canonical_url(link.get('href'))
        if not title or not url:
            continue

        date_match = matches[-1]
        event_date = parse_date(date_match)
        if not event_date:
            continue
        # A small number of archive entries publish two consecutive performance
        # dates as "19. & 20.6.2015". Emit both occurrences.
        dates = [event_date]
        first_day = re.search(r'(\d{1,2})\.\s*&\s*' + re.escape(date_match.group(0)), text)
        if first_day:
            try:
                second = date.fromisoformat(event_date)
                dates.insert(0, second.replace(day=int(first_day.group(1))).isoformat())
            except ValueError:
                pass

        remainder = text[date_match.end():].lstrip(' ,')
        location_text = remainder.split(':', 1)[0] if ':' in remainder else remainder
        if title in location_text:
            location_text = location_text.split(title, 1)[0]
        location_parts = [part.strip() for part in location_text.split(',') if part.strip()]
        city_hint = location_parts[-1] if location_parts else ''
        city, country_code = resolve_city(city_hint)
        venue_hint = ', '.join(location_parts[:-1]) if len(location_parts) > 1 else None
        if venue_hint and venue_hint.casefold() in {'online', 'wien/online'}:
            venue_hint = None

        for value in dates:
            items.append({
                'title': title,
                'date': value,
                'url': url,
                'city': city,
                'country_code': country_code,
                'venue_hint': venue_hint,
                'online': 'online' in location_text.casefold(),
            })
    return items


def detail_lines(soup):
    content = soup.select_one('.entry-content, article')
    if not content:
        return [], None
    lines = [clean_text(line) for line in content.get_text('\n').splitlines()]
    lines = [line for line in lines if line]
    description = '\n'.join(lines) or None
    return lines, description


def clean_venue(candidate, city):
    candidate = clean_text(candidate).strip(' |,;-')
    if city:
        candidate = re.split(rf',\s*{re.escape(city)}\b', candidate, flags=re.IGNORECASE)[0]
    candidate = re.split(r',\s*(?:[A-Z]-?)?\d{4}\b', candidate)[0].strip(' ,')
    candidate = re.split(r',\s*[^,]*\d', candidate, maxsplit=1)[0].strip(' ,')
    if not candidate or candidate.casefold() == (city or '').casefold():
        return None
    if len(candidate) > 100 or re.search(
        r'\b(?:online|karten|eintritt|programm|mitwirkende|unterstützung|montag|dienstag|'
        r'mittwoch|donnerstag|freitag|samstag|sonntag)\b', candidate, re.IGNORECASE
    ):
        return None
    if DATE_RE.search(candidate) or TIME_RE.search(candidate):
        return None
    return candidate


def venue_from_detail(lines, item):
    city = item['city']
    event_date = date.fromisoformat(item['date'])
    date_markers = (
        f'{event_date.day}. {list(MONTHS)[event_date.month - 1]} {event_date.year}',
        f'{event_date.day}.{event_date.month}.{event_date.year}',
    )
    date_indices = [
        index for index, line in enumerate(lines)
        if any(marker.casefold() in line.casefold() for marker in date_markers)
        or (
            str(event_date.year) in line
            and re.search(rf'\b0?{event_date.day}\.', line)
        )
    ]
    for index in date_indices:
        line = lines[index]
        before_date = re.split(r'\b(?:Mo(?:ntag)?|Di(?:enstag)?|Mi(?:ttwoch)?|'
                               r'Do(?:nnerstag)?|Fr(?:eitag)?|Sa(?:mstag)?|'
                               r'So(?:nntag)?)?\s*\d{1,2}\.', line, maxsplit=1,
                               flags=re.IGNORECASE)[0]
        candidate = clean_venue(before_date, city)
        if candidate and not title_like(candidate, item['title']):
            return candidate
        for neighbor in (index - 1, index - 2, index + 1):
            if 0 <= neighbor < len(lines):
                candidate = clean_venue(lines[neighbor], city)
                if candidate and not title_like(candidate, item['title']):
                    return candidate
    return None


def title_like(candidate, title):
    words = set(re.findall(r'\w{4,}', candidate.casefold()))
    title_words = set(re.findall(r'\w{4,}', title.casefold()))
    return bool(words) and len(words & title_words) / len(words) >= 0.6


def make_record(item, soup):
    if item['online']:
        return None
    lines, description = detail_lines(soup)
    if not lines:
        return None

    city, country_code = item['city'], item['country_code']
    if not city:
        for line in lines[:15]:
            city, country_code = resolve_city(line)
            if city:
                break
    localized_item = {**item, 'city': city}
    venue = clean_venue(item['venue_hint'], city) or venue_from_detail(lines[:25], localized_item)
    if not city or not country_code or not venue:
        return None

    time_from = None
    event_year = item['date'][:4]
    for line in lines[:25]:
        if event_year not in line:
            continue
        match = CLOCK_RE.search(line) or TIME_RE.search(line)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2) or 0)
            if hour < 24 and minute < 60:
                time_from = f'{hour:02d}:{minute:02d}'
                break

    return {
        'title': item['title'],
        'date': item['date'],
        'url': item['url'],
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class EwcAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ewc_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        response = session.get(
            CALENDAR_API_URL,
            params={'slug': 'konzerte-2', '_fields': 'content'},
            timeout=45,
        )
        response.raise_for_status()
        pages = response.json()
        if not pages:
            return []
        items = listing_items(BeautifulSoup(pages[0]['content']['rendered'], 'html.parser'))

        records = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(session.get, item['url'], timeout=45): item
                for item in items
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    detail_response = future.result()
                    detail_response.raise_for_status()
                    record = make_record(item, BeautifulSoup(detail_response.text, 'html.parser'))
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape Ensemble Wiener Collage concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=item['url'],
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                    continue
                if record:
                    records.append(record)

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    return EwcAtCrawler().run()


if __name__ == '__main__':
    main()
