import time
import datetime

import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from ...extractors import extract_city, extract_date, extract_time

def convert_date(date_str):
    """
    Convert date string to format 'yyyy-mm-dd'
    """
    if not isinstance(date_str, str):
        return None
    sep = '/' if '/' in date_str else '.'
    parts = [part.strip() for part in date_str.split(sep)]
    return f'{parts[2]}-{int(parts[1]):02d}-{int(parts[0]):02d}'

def validate_concert(concert):
    if concert['date'] is None:
        return False
    if datetime.date.fromisoformat(concert['date']) < datetime.date.today():
        return False
    if 'permanentka' in concert['title'].lower():
        return False
    return True

def validate_venue(venue):
    if venue is None:
        return None
    if venue in ['Moyzesova sieň']:
        return 'Moyzesova sieň'
    if 'Dom hudby' in venue:
        return 'Dom hudby'
    return venue

def extract_city_from_venue(venue):
    city = extract_city(venue or '')
    return city or 'Bratislava'

def extract_concert_info(concert):
    title = concert.find('span', class_='clickable--ProfileName').text.strip()
    url = concert.find('a')['href']
    info_tag = concert.find('div', class_='tt-evt-li__sub-info')
    info = info_tag.get_text('\n', strip=True) if info_tag else ''
    metadata = info.splitlines()[0] if info else ''
    date = extract_date(metadata)
    time = extract_time(metadata)
    parts = [part.strip() for part in metadata.split(' / ')]
    venue = next((part for part in parts if part and not extract_date(part) and extract_time(part) is None and part.isupper() is False), None)
    description = info
    return {
			'title': title,
			'url': url,
			'date': convert_date(date),
			'time_from': time,
			'time_to': None,
			'venue': validate_venue(venue),
            'city': extract_city_from_venue(venue),
	        'description': description,
		}
    
class KonvergencieCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='konvergencie_sk',
        source='Konvergencie',
        source_url='https://www.konvergencie.sk',
        columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue', 'city', 'description'],
        front_fields=[
            ('source_url', 'https://www.konvergencie.sk'),
            ('source', 'Konvergencie'),
        ],
    )

    def scrape(self):
        url = 'https://www.konvergencie.sk/vstupenky/'
        r = requests.get(url)
        soup = BeautifulSoup(r.content, 'html.parser')

        concerts = soup.find_all('div', class_='tt-evt-li')
        concert_data = [extract_concert_info(concert) for concert in concerts]
        return [c for c in concert_data if validate_concert(c)]


def main():
    KonvergencieCrawler().run()


if __name__ == '__main__':
    main()
