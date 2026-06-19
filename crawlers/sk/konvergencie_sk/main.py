import time
import datetime

import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from ...extractors import extract_date, extract_time

def convert_date(date_str):
    """
    Convert date string to format 'yyyy-mm-dd'
    """
    if not isinstance(date_str, str):
        return None
    sep = '/' if '/' in date_str else '.'
    return f'{date_str.split(sep)[2]}-{date_str.split(sep)[1]}-{date_str.split(sep)[0]}'

def validate_concert(concert):
    if concert['date'] is None:
        return False
    if 'permanentka' in concert['title'].lower():
        return False
    return True

def validate_venue(venue):
    if venue in ['Moyzesova sieň']:
        return 'Moyzesova sieň'
    if 'Dom hudby' in venue:
        return 'Dom hudby'
    return None

def extract_concert_info(concert):
    title = concert.find('span', class_='clickable--ProfileName').text.strip()
    url = concert.find('a')['href']
    info = concert.find('div', class_='tt-evt-li__sub-info').text.strip()
    date = extract_date(info)
    venue = info.split(' / ')[1]
    time = extract_time(info)
    description = concert.find('div', class_='tt-evt-li__sub-info--About').text.strip()
    return {
		'title': title,
		'url': url,
		'date': convert_date(date),
		'time_from': time,
		'time_to': None,
		'venue': validate_venue(venue),
        'description': description,
	}
    
class KonvergencieCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='konvergencie_sk',
        source='Konvergencie',
        source_url='https://www.konvergencie.sk',
        columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue', 'description'],
        front_fields=[
            ('city', 'Bratislava'),
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


