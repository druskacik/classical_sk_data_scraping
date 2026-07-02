import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from ...extractors import extract_date, extract_time
from ...formaters import format_date

def extract_event_url(concert):
    a = concert.find('a', class_='tt-evt-li__name')
    url = a['href']
    return url

def crawl_event_urls():
    url = 'https://podujatia.pkopresov.sk/'
    print(url)
    r = requests.get(url, timeout=20)
    soup = BeautifulSoup(r.text, 'html.parser')
    posts_widget = soup.find('div', class_='elementor-element', attrs={'data-widget_type': 'tootoot-event-list.tiles'})
    
    event_elements = posts_widget.find_all('div', class_='tt-evt-li__event-info')
    event_urls = [extract_event_url(event) for event in event_elements]
    data_id = posts_widget['data-id']
    
    page = 1
    while True:
        url = f'https://podujatia.pkopresov.sk/wp-json/elementor-pro/v1/posts-widget?post_id=1100&element_id={data_id}&page={page}'
        print(url)
        r = requests.get(url, timeout=20)
        data = r.json()
        soup = BeautifulSoup(data['content'], 'html.parser')
        event_elements = soup.find_all('div', class_='tt-evt-li__event-info')
        if len(event_elements) == 0:
            break
        for event in event_elements:
            a = event.find('a', class_='tt-evt-li__name')
            url = a['href']
            event_urls.append(url)
        page += 1

    return [url for url in event_urls if '/event-detail/' in url]

def extract_event_info(url):
    print(url)
    r = requests.get(url, timeout=20)
    soup = BeautifulSoup(r.text, 'html.parser')
    script = soup.find('script', type='application/ld+json')
    if script is None:
        return None
    info = json.loads(script.text)
    if info.get('name', '').lower().startswith('darčeková poukážka'):
        return None

    description = info.get('description')
    location = info.get('location') or {}
    address = location.get('address') or {}
    return {
			'title': info['name'],
			'date': info['startDate'].split('T')[0],
			'time_from': info['startDate'].split('T')[1][:5],
			'venue': location.get('name'),
			'city': address.get('addressLocality'),
			'url': url,
			'description': description
		}



class PkoPresovCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='pkopresov_sk',
        source='Park kultúry a oddychu',
        source_url='https://podujatia.pkopresov.sk/',
        columns=['title', 'date', 'url', 'time_from', 'venue', 'city', 'description'],
        upload_target='potential',
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', 'https://podujatia.pkopresov.sk/'),
            ('source', 'Park kultúry a oddychu'),
        ],
    )

    def scrape(self):
        event_urls = crawl_event_urls()
        concerts = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(extract_event_info, url) for url in event_urls]
            for future in as_completed(futures):
                try:
                    concert = future.result()
                except Exception as exc:
                    print(f'Error extracting PKO Prešov event: {exc}')
                    continue
                if concert is not None:
                    concerts.append(concert)
        return sorted(concerts, key=lambda concert: (concert['date'], concert['time_from'], concert['title']))


def main():
    PkoPresovCrawler().run()


if __name__ == '__main__':
    main()
