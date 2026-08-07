import re
from datetime import date, datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.operan.se/'
PRODUCTIONS_URL = urljoin(SOURCE_URL, 'forestallningar')
PERFORMANCES_URL = 'https://webapi.operan.se/performances/bymonth'
SOURCE = 'Kungliga Operan'
MONTH_LIMIT = 36
EMPTY_MONTH_LIMIT = 6

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'sv-SE,sv;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def add_month(month, offset):
    index = month.year * 12 + month.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def production_links(session):
    """Return the site's production-id to canonical detail-page mapping."""
    links = {}
    page = 1
    while True:
        response = session.get(PRODUCTIONS_URL, params={'page': page}, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        cards = soup.select('[data-production-card-id]')
        added = 0
        for card in cards:
            link = card.select_one('a[href^="/forestallningar/"]')
            production_id = card.get('data-production-card-id')
            if production_id and link and production_id not in links:
                links[production_id] = urljoin(SOURCE_URL, link.get('href'))
                added += 1
        if not cards or not added:
            break
        page += 1
        if page > 50:
            break
    return links


def detail_data(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    title = clean_text(soup.select_one('h1.production-hero__title'))
    venue = ''
    for item in soup.select('.production-hero__content__description__list li'):
        parts = item.find_all('div', recursive=False)
        if len(parts) >= 2 and clean_text(parts[0]).rstrip(':').upper() == 'SCEN':
            venue = clean_text(parts[1])
            break

    description_parts = []
    for node in soup.select(
        '.production-hero__subtext, '
        '.production-hero__content__description > p, '
        '.uk-container.uk-container-small.rte'
    ):
        text = clean_text(node)
        if text and text not in description_parts:
            description_parts.append(text)

    description = clean_text('\n\n'.join(description_parts)) or None
    return {'title': title, 'venue': venue, 'description': description}


def get_performances(session):
    performances = []
    start = date.today().replace(day=1)
    empty_months = 0
    found_any = False

    for offset in range(MONTH_LIMIT):
        month = add_month(start, offset)
        response = session.get(
            PERFORMANCES_URL,
            params={'date': month.isoformat()},
            timeout=60,
        )
        response.raise_for_status()
        days = response.json()
        month_items = [item for day in days for item in day.get('performances', [])]
        performances.extend(month_items)

        if month_items:
            found_any = True
            empty_months = 0
        elif found_any:
            empty_months += 1
            if empty_months >= EMPTY_MONTH_LIMIT:
                break

    return performances


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    links = production_links(session)
    performances = get_performances(session)
    details = {}
    records = []

    for performance in performances:
        production_id = str(performance.get('productionId', ''))
        url = links.get(production_id)
        if not url:
            continue
        if url not in details:
            try:
                details[url] = detail_data(session, url)
            except requests.RequestException as error:
                log_message(
                    'Production detail could not be scraped',
                    event='crawler_detail_failed',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                details[url] = {}

        detail = details[url]
        title = detail.get('title', '')
        venue = detail.get('venue', '')
        start_at = performance.get('performanceDate', '')
        try:
            local_start = datetime.fromisoformat(start_at.replace('Z', '+00:00')).astimezone(
                ZoneInfo('Europe/Stockholm')
            )
        except (TypeError, ValueError):
            continue
        if not title or not venue:
            continue

        records.append({
            'title': title,
            'date': local_start.date().isoformat(),
            'url': url,
            'time_from': local_start.strftime('%H:%M'),
            'venue': venue,
            'city': 'Stockholm',
            'country_code': 'SE',
            'description': detail.get('description'),
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })

    log_message(
        'Operan catalogue scraped',
        event='crawler_scrape_completed',
        url=PRODUCTIONS_URL,
        record_count=len(records),
    )
    return sorted(records, key=lambda item: (item['date'], item['time_from'], item['title']))


class OperanSeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='operan_se',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='SE',
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
    OperanSeCrawler().run()


if __name__ == '__main__':
    main()
