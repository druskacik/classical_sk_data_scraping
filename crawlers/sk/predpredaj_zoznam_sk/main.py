import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from ...extractors import clean_string

import json
import html
from concurrent.futures import ThreadPoolExecutor, as_completed

def parse_json(json_str):
    try:
        return json.loads(json_str, strict=False)[0]
    except:
        new_json = ''
        ignore = False
        
        description_str = ''
        
        for line in json_str.splitlines():
            if line.strip().startswith('"description"'):
                ignore = True
                description_str += line.strip().lstrip('"description": "')
                continue
            if ignore and line.strip().startswith('"offers":'):
                ignore = False
            if ignore:
                description_str += line.strip().rstrip('",')
            else:
                new_json += line.strip()
        
        return {
            **json.loads(new_json, strict=False)[0],
            'description': description_str
        }

def extract_concert_performances(concert_url):
    print(concert_url)
    if not concert_url.startswith('https://predpredaj.zoznam.sk/sk/listky/'):
        return []

    r = requests.get(concert_url, timeout=20)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    title_tag = soup.find('h1')
    if title_tag is None:
        return []
    title = title_tag.text.strip()
    
    event_dates = soup.find('div', class_='event__dates')
    if event_dates is not None:
        items = event_dates.find_all('li', class_='list-group-item')
        performances = []
        for item in items:
            slug = item.find('a')['href']
            url = f'https://predpredaj.zoznam.sk{slug}'
            performances.extend(extract_concert_performances(url))
        # The subtitles include cities, which we don't want
        performances = [{**p, 'title': title} for p in performances]
        return performances
     
    script = soup.find('script', attrs={'type': 'application/ld+json'})
    if script is None:
        return []
    info = parse_json(script.text)
    
    start_date = info.get('startDate')
    if not start_date:
        return []
    if 'T' in start_date:
        date, time = start_date.split('T', 1)
    else:
        parts = start_date.split()
        if len(parts) < 2:
            return []
        date, time = parts[:2]
    time = time[:5]
    location = info['location']['name']
    city = info['location']['address']
    
    venue = None
    if city is not None and location.endswith(city):
        venue = location.split(',')[0].strip()
        
    description = clean_string(html.unescape(info['description']))
    description = description.split('\n\n')[0].strip()
    
    return [{
        'title': title,
        'date': date,
        'time_from': time,
        'venue': venue,
        'city': city,
        'url': concert_url,
        'description': description
    }]


class PredpredajCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='predpredaj_zoznam_sk',
        source='Zoznam.sk',
        source_url='https://predpredaj.zoznam.sk/',
        columns=['title', 'date', 'url', 'time_from', 'venue', 'city', 'description'],
        upload_target='potential',
        front_fields=[
            ('source_url', 'https://predpredaj.zoznam.sk/'),
            ('source', 'Zoznam.sk'),
        ],
    )

    def scrape(self):
        url = 'https://predpredaj.zoznam.sk/sk/kategoria/koncert/'
        r = requests.get(url, timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        concerts = soup.find_all('article')

        def extract_concert_url(concert):
            return f'https://predpredaj.zoznam.sk{concert.find("a")["href"]}'

        concert_urls = [extract_concert_url(c) for c in concerts if 'darcekove-poukazy' not in extract_concert_url(c)]
        concert_urls = list(dict.fromkeys(concert_urls))

        concert_data = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(extract_concert_performances, url) for url in concert_urls]
            for future in as_completed(futures):
                try:
                    concert_data.append(future.result())
                except Exception as exc:
                    print(f'Error extracting Predpredaj event: {exc}')
        return [item for sublist in concert_data for item in sublist]

    def transform(self, df):
        df.drop_duplicates(subset=['title', 'date', 'url'], inplace=True)
        return df[df['city'].notna()].copy()


def main():
    PredpredajCrawler().run()


if __name__ == '__main__':
    main()
