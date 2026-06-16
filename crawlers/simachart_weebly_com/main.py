import requests

from bs4 import BeautifulSoup

from ..base import BaseCrawler, CrawlerConfig
from ..extractors import extract_date, extract_time, clean_string
from ..formaters import format_date

URL = 'https://simachart.weebly.com/bude.html'

def extract_concert_info(paragraph):
    fonts = paragraph.find_all('font', attrs={'size': True})
    
    title = fonts[0].text
    date = extract_date(fonts[1].text)
    time = extract_time(fonts[1].text)
    location = fonts[2].get_text(strip=True, separator='\n')
    venue, address = location.splitlines()
    venue = venue.strip()
    city = clean_string(address.split(',')[1]).strip()
    
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
            concerts.append(extract_concert_info(p))
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
