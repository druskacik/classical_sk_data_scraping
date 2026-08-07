import html
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://kammarmusikens-vanner.se/'
API_URL = f'{SOURCE_URL}wp-json/wp/v2/posts'
SOURCE = 'Kammarmusikens Vänner i Allhelgonakyrkan'
VENUE = 'Allhelgonakyrkan'
CITY = 'Stockholm'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'sv-SE,sv;q=0.9,en;q=0.7',
}

MONTHS = {
    'januari': 1, 'februari': 2, 'mars': 3, 'april': 4,
    'maj': 5, 'juni': 6, 'juli': 7, 'augusti': 8,
    'september': 9, 'oktober': 10, 'november': 11, 'december': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(html.unescape(str(value)), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u00ad', '').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_posts(session):
    posts = []
    page = 1
    while True:
        response = session.get(
            API_URL,
            params={
                'per_page': 100,
                'page': page,
                '_fields': 'id,date,link,title,content',
            },
            timeout=60,
        )
        if response.status_code == 400 and page > 1:
            break
        response.raise_for_status()
        batch = response.json()
        posts.extend(batch)
        total_pages = int(response.headers.get('X-WP-TotalPages', page))
        if page >= total_pages:
            break
        page += 1
    return posts


def valid_date(year, month, day):
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def title_dates(title, publication_year):
    """Return event dates explicitly encoded in the post title."""
    normalized = title.lower().replace('–', '-').replace('—', '-')
    year_match = re.search(r'\b(20\d{2})\b', normalized)
    year = int(year_match.group(1)) if year_match else publication_year

    # ISO and compact ISO titles used by the oldest part of the archive.
    match = re.search(r'\b(20\d{2})[-/]?(\d{2})[-/]?(\d{2})\b', normalized)
    if match:
        result = valid_date(*(int(value) for value in match.groups()))
        return [result] if result else []

    # Swedish numeric dates, including a two-day range such as 24-25/4.
    match = re.search(r'\b(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s*/\s*(\d{1,2})\b', normalized)
    if match:
        first, last, month = (int(value) if value else None for value in match.groups())
        days = range(first, last + 1) if last else [first]
        return [value for day in days if (value := valid_date(year, month, day))]

    # Long Swedish dates and ranges used for festival headings.
    match = re.search(
        r'\b(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s+(' + '|'.join(MONTHS) + r')\b',
        normalized,
    )
    if match:
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else None
        month = MONTHS[match.group(3)]
        days = range(first, last + 1) if last else [first]
        return [value for day in days if (value := valid_date(year, month, day))]
    return []


def event_time(title, description, event_date):
    patterns = (
        r'\b(?:kl\.?|klockan|at)\s*(\d{1,2})[.:](\d{2})',
        r'\b(?:kl\.?|klockan|at)\s*(\d{1,2})(?!\d)',
    )
    parsed_date = date.fromisoformat(event_date)
    month_name = next(name for name, number in MONTHS.items() if number == parsed_date.month)
    dated_time = re.search(
        rf'\b{parsed_date.day}\s+{month_name}\b.{{0,35}}?'
        r'(?:kl\.?|klockan)\s*(\d{1,2})(?:[.:](\d{2}))?',
        description,
        re.I,
    )
    if dated_time:
        hour = int(dated_time.group(1))
        minute = int(dated_time.group(2) or 0)
        if 0 <= hour < 24 and 0 <= minute < 60:
            return f'{hour:02d}:{minute:02d}'

    for text in (title, description):
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.I):
                context = text[max(0, match.start() - 60):match.start()].lower()
                if any(term in context for term in ('entré', 'konsertdagen', 'dörrarna')):
                    continue
                hour = int(match.group(1))
                minute = int(match.group(2)) if match.lastindex == 2 else 0
                if 0 <= hour < 24 and 0 <= minute < 60:
                    return f'{hour:02d}:{minute:02d}'
    return None


def records_from_post(post):
    title = clean_text((post.get('title') or {}).get('rendered'))
    description = clean_text((post.get('content') or {}).get('rendered'))
    url = post.get('link') or ''
    published = (post.get('date') or '')[:10]
    try:
        publication_year = date.fromisoformat(published).year
    except ValueError:
        return []

    dates = title_dates(title, publication_year)
    if not title or not url or not dates:
        return []

    # Festival overview pages duplicate separately published daily concerts.
    if len(dates) > 2 and 'festival' in title.lower():
        return []

    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': event_time(title, description, event_date),
            'venue': VENUE,
            'city': CITY,
            'country_code': 'SE',
            'description': description or None,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date in dates
    ]


class KammarmusikensVannerSeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='kammarmusikens_vanner_se',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='SE',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            posts = get_posts(session)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch WordPress concert archive',
                event='crawler_fetch_failed',
                level='error',
                url=API_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        records = []
        for post in posts:
            records.extend(records_from_post(post))
        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    KammarmusikensVannerSeCrawler().run()


if __name__ == '__main__':
    main()
