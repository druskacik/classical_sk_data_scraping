import re
from html import unescape
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.klavirnirecitaly.cz'
ORIGINAL_URL = 'https://www.hybatelerezonance.cz/'
SOURCE = 'Hybatelé rezonance'
SOURCE_URL = ORIGINAL_URL
DEFAULT_CITY = 'Praha'
DEFAULT_VENUE = 'Anežský klášter'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

SKIP_TEXTS = {
    'cz',
    'en',
    'videogalerie',
    'vstupenky',
    'koupit abonmá',
    'tyto stránky používají cookies',
    'přijmout',
}

DESCRIPTION_STOP_PREFIXES = (
    'partner koncertu',
    'partneři koncertu',
    'největší evropský nástrojový výrobce',
    'jednotlivé vstupenky',
    'abonmá celý cyklus',
    'resonances - film',
    'c. bechstein - piano',
    'více na www.bechstein.com',
    'máte-li zájem',
    ': ve spolupráci',
    ': mediální partne',
    'aficionado s.r.o.',
)


def clean_text(text):
    if not text:
        return ''
    text = unescape(str(text)).replace('\xa0', ' ').replace('\ufeff', '').replace('\u200b', '')
    text = re.sub(r'(?<=\d)\s+(?=\d)', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser', from_encoding='utf-8'), response.url


def season_urls():
    return [f'{BASE_URL}/{year}/' for year in range(2019, 2025)]


def is_concert_detail_url(url):
    parsed = urlparse(url)
    path = parsed.path
    if not path.endswith('.html'):
        return False
    if 'klavirni-recitaly' not in path:
        return False
    excluded = ('videogalerie', 'privacy', '-cv.html')
    return not any(value in path for value in excluded)


def discover_detail_urls(session):
    detail_urls = set()

    for season_url in season_urls():
        soup, final_url = get_soup(session, season_url)
        for link in soup.select('a[href]'):
            url = urldefrag(urljoin(final_url, link.get('href')))[0]
            if is_concert_detail_url(url):
                detail_urls.add(url)

    return sorted(detail_urls)


def element_texts(soup):
    texts = []

    for element in soup.select('h1, h2, h3, p'):
        text = clean_text(element.get_text(' ', strip=True))
        if not text:
            continue
        if text.casefold() in SKIP_TEXTS:
            continue
        texts.append(text)

    return texts


def parse_detail_year(url):
    match = re.search(r'/(\d{4})/', url)
    return int(match.group(1)) if match else None


def parse_date_line(text):
    text = clean_text(text)
    match = re.search(r'\b(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4}|\d{2})\b', text)
    if not match:
        return None, None, None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    if year < 100:
        year += 2000

    return day, month, year


def season_corrected_date(date_text, detail_url):
    day, month, year = parse_date_line(date_text)
    if not day:
        return None

    season_year = parse_detail_year(detail_url)
    if season_year and year < season_year:
        year = season_year + 1 if month < 9 else season_year
    elif season_year and year == season_year and month < 9:
        year = season_year + 1

    return f'{year:04d}-{month:02d}-{day:02d}'


def parse_time(text):
    match = re.search(r'\b(\d{1,2})\s*:\s*(\d{2})\b', clean_text(text))
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def title_from_page(soup, fallback):
    if soup.title:
        title = clean_text(soup.title.get_text(' ', strip=True)).split('|', 1)[0].strip()
    else:
        title = ''

    title = title or fallback
    if title and title.upper() == title:
        return title.title()
    return title


def content_start_index(texts):
    for index, text in enumerate(texts):
        if 'klavírní recitály' in text.casefold():
            return index + 1
    return 0


def find_main_date(texts, detail_url):
    for index in range(content_start_index(texts), len(texts)):
        date = season_corrected_date(texts[index], detail_url)
        if date:
            return index, texts[index], date
    return None, None, None


def same_text(left, right):
    return clean_text(left).casefold() == clean_text(right).casefold()


def description_lines(texts, title, date_index, date, time_from):
    date_label = f'{date} {time_from}' if time_from else date
    description = [title, date_label]
    start = date_index + 1

    for index in range(start, min(len(texts), start + 8)):
        if same_text(texts[index], title):
            start = index + 1
            break

    for text in texts[start:]:
        lowered = text.casefold().strip()
        if lowered in SKIP_TEXTS:
            continue
        if lowered.startswith('více o '):
            continue
        if any(lowered.startswith(prefix) for prefix in DESCRIPTION_STOP_PREFIXES):
            break
        if text not in description:
            description.append(text)

    return clean_text('\n'.join(description)) or None


def extract_concert(session, url):
    soup, final_url = get_soup(session, url)
    texts = element_texts(soup)
    date_index, date_text, date = find_main_date(texts, final_url)
    if not date:
        return None

    title = title_from_page(soup, texts[date_index + 1] if date_index + 1 < len(texts) else SOURCE)
    time_from = parse_time(date_text)
    description = description_lines(texts, title, date_index, date, time_from)

    return {
        'title': title,
        'date': date,
        'url': final_url,
        'time_from': time_from,
        'time_to': None,
        'venue': DEFAULT_VENUE,
        'city': DEFAULT_CITY,
        'description': description,
        'type': 'concert',
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = []
    for detail_url in discover_detail_urls(session):
        concert = extract_concert(session, detail_url)
        if concert and concert['title'] and concert['date']:
            concerts.append(concert)

    return concerts


class HybateleRezonanceCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='hybatelerezonance_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'time_to',
            'venue',
            'city',
            'description',
            'type',
        ],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    HybateleRezonanceCrawler().run()


if __name__ == '__main__':
    main()
