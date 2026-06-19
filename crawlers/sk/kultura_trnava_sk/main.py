import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig

URL = 'https://kultura.trnava.sk/podujatie/trnavska-hudobna-jar-2025'

def extract_event_info(event):
    tag = event.find('span', class_='tag').find('a')
    title = tag['data-name']
    date_and_time = tag['data-date']
    date, time = date_and_time.split(' ')
    venue = tag['data-location']
    
    description = None
    p = event.find('p', attrs={'dir': 'ltr'})
    if p:
        description = p.text.strip()
        
    return {
		'title': title,
		'date': date,
		'time': time,
		'venue': venue,
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

        concert_divs = soup.find_all('div', class_='event')
        return [extract_event_info(div) for div in concert_divs]


def main():
    KulturaTrnavaCrawler().run()


if __name__ == '__main__':
    main()
