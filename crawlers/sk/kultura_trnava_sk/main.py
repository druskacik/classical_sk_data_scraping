import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig

URL = 'https://kultura.trnava.sk/podujatie/trnavske-organove-dni-2026'

def extract_event_info(anchor):
    date_and_time = anchor['data-date']
    date, time = date_and_time.split(' ')
    event = anchor.find_parent('div', class_='event')
    
    description = ''
    if event:
        content = event.find('div', class_='content')
        if content:
            description = content.get_text(' ', strip=True)
        
    return {
			'title': anchor['data-name'],
			'date': date,
			'time_from': time[:5],
			'venue': anchor['data-location'],
			'description': description
		}


class KulturaTrnavaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='kultura_trnava_sk',
        source='Zaži v Trnave',
        source_url='https://kultura.trnava.sk',
        columns=['title', 'date', 'time_from', 'venue', 'description'],
        front_fields=[
            ('city', 'Trnava'),
            ('url', URL),
            ('source_url', 'https://kultura.trnava.sk'),
            ('source', 'Zaži v Trnave'),
        ],
    )

    def scrape(self):
        r = requests.get(URL)
        soup = BeautifulSoup(r.content, 'html.parser')

        anchors = soup.find_all('a', class_='js-ical', attrs={'data-date': True, 'data-name': True})
        return [extract_event_info(anchor) for anchor in anchors]


def main():
    KulturaTrnavaCrawler().run()


if __name__ == '__main__':
    main()
