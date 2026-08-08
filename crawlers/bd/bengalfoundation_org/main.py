import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://bengalfoundation.org/'
SOURCE = 'Bengal Foundation'
RECITALS_URL = urljoin(SOURCE_URL, 'recitals/')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9,bn;q=0.8',
}

MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'জানুয়ারি': 1, 'ফেব্রুয়ারি': 2, 'মার্চ': 3, 'এপ্রিল': 4,
    'মে': 5, 'জুন': 6, 'জুলাই': 7, 'আগস্ট': 8,
    'সেপ্টেম্বর': 9, 'অক্টোবর': 10, 'নভেম্বর': 11, 'ডিসেম্বর': 12,
}

TRANSLATION = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')

VENUES = (
    (r'Bengal Shilpalay|বেঙ্গল শিল্পালয়', 'Bengal Shilpalay', 'Dhaka'),
    (r'Bengal Gallery of Fine Arts|বেঙ্গল গ্যালারি', 'Bengal Gallery of Fine Arts', 'Dhaka'),
    (r'Army Stadium|আর্মি স্টেডিয়াম', 'Bangladesh Army Stadium', 'Dhaka'),
    (r'Abahani Field|আবাহনী মাঠ', 'Abahani Field', 'Dhaka'),
    (r'Bengal Parampara Sangeetalay|বেঙ্গল পরম্পরা সংগীতালয়',
     'Bengal Parampara Sangeetalay', 'Dhaka'),
    (r'Bengal Centre|বেঙ্গল সেন্টার', 'Bengal Centre', 'Dhaka'),
    (r'Bengal Boi|বেঙ্গল বই', 'Bengal Boi', 'Dhaka'),
    (r'Chhayanaut|ছায়ানট', 'Chhayanaut', 'Dhaka'),
    (r'National Theatre Hall|জাতীয় নাট্যশালা', 'National Theatre Hall', 'Dhaka'),
)


def clean_text(element):
    if element is None:
        return ''
    text = element.get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_dates(text):
    normalized = text.translate(TRANSLATION)
    found = set()
    pattern = re.compile(
        r'\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|'
        r'September|October|November|December|জানুয়ারি|ফেব্রুয়ারি|মার্চ|এপ্রিল|মে|'
        r'জুন|জুলাই|আগস্ট|সেপ্টেম্বর|অক্টোবর|নভেম্বর|ডিসেম্বর)\s+(20\d{2})\b',
        re.IGNORECASE,
    )
    for match in pattern.finditer(normalized):
        try:
            found.add(date(
                int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1))
            ).isoformat())
        except (KeyError, ValueError):
            continue

    range_pattern = re.compile(
        r'\b(\d{1,2})\s*[-–—]\s*(\d{1,2})\s+'
        r'(January|February|March|April|May|June|July|August|September|October|November|'
        r'December|জানুয়ারি|ফেব্রুয়ারি|মার্চ|এপ্রিল|মে|জুন|জুলাই|আগস্ট|সেপ্টেম্বর|'
        r'অক্টোবর|নভেম্বর|ডিসেম্বর)\s+(20\d{2})\b', re.IGNORECASE,
    )
    for match in range_pattern.finditer(normalized):
        month = MONTHS.get(match.group(3).lower())
        if not month:
            continue
        for day in range(int(match.group(1)), int(match.group(2)) + 1):
            try:
                found.add(date(int(match.group(4)), month, day).isoformat())
            except ValueError:
                continue

    for match in re.finditer(r'\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b', normalized):
        try:
            found.add(date(*map(int, match.groups())).isoformat())
        except ValueError:
            continue
    return sorted(found)


def parse_time(text):
    normalized = text.translate(TRANSLATION)
    match = re.search(r'\b(\d{1,2})(?:[:.]([0-5]\d))?\s*(AM|PM)\b', normalized, re.I)
    if match:
        hour = int(match.group(1)) % 12
        if match.group(3).lower() == 'pm':
            hour += 12
        return f'{hour:02d}:{int(match.group(2) or 0):02d}'
    match = re.search(r'(সন্ধ্যা|বিকেল|রাত|সকাল)\s*(\d{1,2})[.:](\d{2})', normalized)
    if match:
        hour = int(match.group(2))
        if match.group(1) in {'সন্ধ্যা', 'বিকেল', 'রাত'}:
            hour = hour % 12 + 12
        if hour < 24:
            return f'{hour:02d}:{int(match.group(3)):02d}'
    return None


def parse_location(text):
    for pattern, venue, city in VENUES:
        if re.search(pattern, text, re.I):
            return venue, city
    return None


def parse_event(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    article = soup.select_one('article.events_archive')
    if article is None:
        return []
    title = clean_text(article.select_one('.entry-post-title'))
    content = article.select_one('.entry-content')
    description = clean_text(content)
    location = parse_location(description)
    dates = parse_dates(description)
    if not title or not description or not location or not dates:
        return []
    venue, city = location
    time_from = parse_time(description)
    return [{
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'BD',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    } for event_date in dates]


class BengalFoundationOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bengalfoundation_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='BD',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city', 'country_code',
            'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        event_urls = []
        seen_urls = set()

        for page_number in range(1, 101):
            page_url = RECITALS_URL if page_number == 1 else urljoin(
                RECITALS_URL, f'page/{page_number}/'
            )
            try:
                response = session.get(page_url, timeout=45)
                response.raise_for_status()
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Bengal Foundation recital listing',
                    event='crawler_fetch_failed', level='error', url=page_url,
                    error_type=type(error).__name__, error_message=str(error),
                )
                raise
            soup = BeautifulSoup(response.text, 'html.parser')
            page_urls = list(dict.fromkeys(
                urljoin(SOURCE_URL, link['href'])
                for link in soup.select('a[href*="/events_archive/"]')
            ))
            new_urls = [url for url in page_urls if url not in seen_urls]
            if not new_urls:
                break
            event_urls.extend(new_urls)
            seen_urls.update(new_urls)

        records = []
        for event_url in event_urls:
            try:
                response = session.get(event_url, timeout=45)
                response.raise_for_status()
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Bengal Foundation event',
                    event='crawler_event_fetch_failed', level='warning', url=event_url,
                    error_type=type(error).__name__, error_message=str(error),
                )
                continue
            records.extend(parse_event(response.text, event_url))

        return sorted(records, key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ))


def main():
    BengalFoundationOrgCrawler().run()


if __name__ == '__main__':
    main()
