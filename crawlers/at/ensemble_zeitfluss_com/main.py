import html
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.ensemble-zeitfluss.com/'
API_URL = f'{SOURCE_URL}wp-json/wp/v2/posts'
SOURCE = 'Ensemble Zeitfluss'
CONCERT_CATEGORIES = (457, 459)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'jan': 1, 'january': 1, 'jänner': 1, 'januar': 1,
    'feb': 2, 'february': 2, 'februar': 2,
    'mar': 3, 'march': 3, 'märz': 3,
    'apr': 4, 'april': 4,
    'may': 5, 'mai': 5,
    'jun': 6, 'june': 6, 'juni': 6,
    'jul': 7, 'july': 7, 'juli': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10, 'okt': 10, 'oktober': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12, 'dez': 12, 'dezember': 12,
}

DATE_PATTERN = re.compile(
    r'(?<!\d)(\d{1,2})\.\s*'
    r'(?:(\d{1,2})\.|'
    r'(Jän(?:ner)?|Jan(?:uar|uary)?|Feb(?:ruar|ruary)?|März|Mar(?:ch)?|'
    r'Apr(?:il)?|Mai|May|Jun(?:i|e)?|Jul(?:i|y)?|Aug(?:ust)?|'
    r'Sep(?:t(?:ember)?)?|Okt(?:ober)?|Oct(?:ober)?|Nov(?:ember)?|'
    r'Dez(?:ember)?|Dec(?:ember)?))'
    r'\s*(20\d{2})',
    re.I,
)

VENUES = (
    (r'(?:Pfarrkirche St\.?\s*Andrä|St\.?\s*Andrä Kirche).*?Mariahilferkirche.*?Minoritensaal',
     'Pfarrkirche St. Andrä / Mariahilferkirche / Minoritensaal', 'Graz', 'AT'),
    (r'(?:Großer\s+)?Minoritensaal|KULTUM[^\n|,]*Minoritensaal', 'Minoritensaal', 'Graz', 'AT'),
    (r'Kultursalon(?:\s+Graz)?', 'Kultursalon Graz', 'Graz', 'AT'),
    (r'Helmut[- ]List[- ]Halle', 'Helmut List Halle', 'Graz', 'AT'),
    (r'(?:Ligeti Saal[^\n|,]*M[Uu][Mm][Uu][Tt]|M[Uu][Mm][Uu][Tt])', 'MUMUTH', 'Graz', 'AT'),
    (r'Next Liberty', 'Next Liberty', 'Graz', 'AT'),
    (r'Generalmusikdirektion(?:\s+Graz)?', 'Generalmusikdirektion Graz', 'Graz', 'AT'),
    (r'Orangerie Burggarten(?:\s+Graz)?', 'Orangerie Burggarten', 'Graz', 'AT'),
    (r'Heimatsaal', 'Heimatsaal', 'Graz', 'AT'),
    (r'Ehrbarsaal', 'Ehrbarsaal', 'Vienna', 'AT'),
    (r'\breaktor\b', 'REAKTOR', 'Vienna', 'AT'),
    (r'\bF23\b', 'F23', 'Vienna', 'AT'),
    (r'Neuer Konzertsaal der MDW', 'Neuer Konzertsaal der MDW', 'Vienna', 'AT'),
    (r'Gemeindezentrum St\.?\s*Ruprecht', 'Gemeindezentrum St. Ruprecht', 'Klagenfurt', 'AT'),
    (r'Festhalle Edelsbach', 'Festhalle Edelsbach', 'Edelsbach bei Feldbach', 'AT'),
    (r'Hotel Imperial', 'Hotel Imperial', 'Opatija', 'HR'),
    (r'(?:Small Hall\s+)?Vatroslav Lisinski(?: Concert Hall)?',
     'Vatroslav Lisinski Concert Hall', 'Zagreb', 'HR'),
    (r'Chamber Hall of the Macedonian Philharmonic',
     'Chamber Hall of the Macedonian Philharmonic', 'Skopje', 'MK'),
    (r'Pallas(?: Theatre| Theater)', 'Pallas Theatre', 'Nicosia', 'CY'),
)


def clean_text(value):
    if not value:
        return ''
    soup = BeautifulSoup(str(value), 'html.parser')
    text = soup.get_text('\n', strip=True)
    text = html.unescape(text).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date_match(match):
    month = int(match.group(2)) if match.group(2) else MONTHS.get(match.group(3).lower())
    if not month:
        return None
    try:
        return date(int(match.group(4)), month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def time_near(text, start, end):
    nearby = text[end:min(len(text), end + 120)]
    for match in re.finditer(r'(?<!\d)([012]?\d)([:.])(\d{2})(?!\d)', nearby):
        hour = int(match.group(1))
        minute = int(match.group(3))
        following = nearby[match.end():match.end() + 12]
        if hour > 23 or minute > 59:
            continue
        if (
            match.group(2) == '.'
            and match.start() > 35
            and not re.match(r'\s*(?:Uhr|h\b)', following, re.I)
        ):
            continue
        return f'{hour:02d}:{minute:02d}'
    match = re.search(r'(?<!\d)([012]?\d)\s*(?:Uhr|o.clock)\b', nearby, re.I)
    if match and int(match.group(1)) <= 23:
        return f'{int(match.group(1)):02d}:00'
    match = re.search(r'(?<!\d)(1[0-2]|[1-9])\s*(?:pm|p\.m\.)\b', nearby, re.I)
    if match:
        hour = int(match.group(1)) % 12 + 12
        return f'{hour:02d}:00'
    return None


def location_from_text(text):
    for pattern, venue, city, country_code in VENUES:
        if re.search(pattern, text, re.I | re.S):
            return venue, city, country_code
    return None, None, None


def event_occurrences(text, published):
    abbreviated = re.search(
        r'((?:\d{1,2}\.\s*/\s*)+)(\d{1,2})\.\s*'
        r'([A-Za-zÄÖÜäöü]+)\s*(20\d{2})',
        text,
    )
    if abbreviated:
        days = re.findall(r'\d{1,2}', abbreviated.group(1)) + [abbreviated.group(2)]
        expanded = '\n'.join(
            f'{day}. {abbreviated.group(3)} {abbreviated.group(4)}'
            for day in days
        )
        text = text[:abbreviated.start()] + expanded + text[abbreviated.end():]
    matches = list(DATE_PATTERN.finditer(text))
    occurrences = []
    for index, match in enumerate(matches):
        event_date = parse_date_match(match)
        if not event_date:
            continue
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.start():next_start]
        venue, city, country_code = location_from_text(segment)
        if not venue and index + 1 < len(matches) and next_start - match.end() < 35:
            venue, city, country_code = location_from_text(
                text[match.start():min(len(text), matches[-1].end() + 250)]
            )
        if not venue and len(matches) == 1:
            venue, city, country_code = location_from_text(text)
        occurrences.append((event_date, time_near(text, match.start(), match.end()), venue, city, country_code))

    if not occurrences:
        event_date = published[:10]
        venue, city, country_code = location_from_text(text)
        occurrences.append((event_date, time_near(text, 0, min(80, len(text))), venue, city, country_code))

    unique = []
    seen = set()
    for item in occurrences:
        key = (item[0], item[2], item[3])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def parse_post(post):
    title = clean_text(post.get('title', {}).get('rendered'))
    description = clean_text(post.get('content', {}).get('rendered'))
    url = post.get('link', '').strip()
    if not title or not description or not url:
        return []

    records = []
    for event_date, time_from, venue, city, country_code in event_occurrences(
        description, post.get('date', '')
    ):
        if not event_date or not venue or not city or not country_code:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


class EnsembleZeitflussComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ensemble_zeitfluss_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        posts = {}
        for category in CONCERT_CATEGORIES:
            response = session.get(
                API_URL,
                params={
                    'categories': category,
                    'per_page': 100,
                    '_fields': 'id,date,link,title,content',
                },
                timeout=45,
            )
            response.raise_for_status()
            for post in response.json():
                posts[post['id']] = post

        records = []
        for post in posts.values():
            parsed = parse_post(post)
            if not parsed:
                log_message(
                    'Skipped Ensemble Zeitfluss post without a complete event location',
                    event='crawler_item_skipped',
                    level='warning',
                    url=post.get('link', ''),
                    error_type='IncompleteEventData',
                    error_message='Required title, date, venue, city, or country is missing',
                )
            records.extend(parsed)
        return sorted(
            records,
            key=lambda item: (item['date'], item['time_from'] or '', item['title'], item['venue']),
        )


def main():
    EnsembleZeitflussComCrawler().run()


if __name__ == '__main__':
    main()
