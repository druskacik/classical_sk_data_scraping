import re
from datetime import date as date_cls

import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from ...extractors import clean_string, extract_city, extract_time

URL = 'https://simachart.weebly.com/bude.html'

def extract_concert_info(paragraph):
    lines = [clean_string(line) for line in paragraph.get_text('\n', strip=True).splitlines()]
    lines = [line for line in lines if line]
    date_line_index = next((i for i, line in enumerate(lines) if re.search(r'\d{1,2}\.\s*\d{1,2}\.\s*\.?\s*\d{4}', line)), None)
    if date_line_index is None:
        return None

    date_match = re.search(r'(\d{1,2})\.\s*(\d{1,2})\.\s*\.?\s*(\d{4})', lines[date_line_index])
    day, month, year = [int(part) for part in date_match.groups()]
    date = f'{year}-{month:02d}-{day:02d}'
    if date_cls.fromisoformat(date) < date_cls.today():
        return None

    time = extract_time(lines[date_line_index])
    venue = lines[date_line_index + 1] if len(lines) > date_line_index + 1 else None
    address = lines[date_line_index + 2] if len(lines) > date_line_index + 2 else ''
    city = extract_city(address) or 'Ružomberok'
    title = lines[date_line_index + 3] if len(lines) > date_line_index + 3 else lines[0]
    
    return {
		'title': title,
		'date': format_date(date),
		'time_from': time,
		'venue': venue,
		'city': city,
		'description': paragraph.get_text().strip()
	}

def extract_concerts(soup):
    paragraphs = soup.find_all('div', class_='paragraph', style='text-align:justify;')
    concerts = []
    for p in paragraphs:
        try:
            concert = extract_concert_info(p)
            if concert is not None:
                concerts.append(concert)
        except Exception as e:
            print(f"Error extracting concert info: {e}")
    return concerts


class SimachartCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='simachart_weebly_com',
        source='Simachart',
        source_url='https://simachart.weebly.com',
        columns=['title', 'date', 'time_from', 'venue', 'city', 'description'],
        front_fields=[
            ('url', URL),
            ('source_url', 'https://simachart.weebly.com'),
            ('source', 'Simachart'),
        ],
    )

    def scrape(self):
        r = requests.get(URL)
        soup = BeautifulSoup(r.content, 'html.parser')
        return extract_concerts(soup)


def main():
    SimachartCrawler().run()


if __name__ == '__main__':
    main()
