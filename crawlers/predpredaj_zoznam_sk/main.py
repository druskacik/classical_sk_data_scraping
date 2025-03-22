import requests
from bs4 import BeautifulSoup

import pandas as pd

from ..classical import upload_potential_concerts
from ..extractors import clean_string

import json
import html

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
    r = requests.get(concert_url)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    title = soup.find('h1').text.strip()
    
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
    info = parse_json(script.text)
    
    date, time = info['startDate'].split()
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


def main():
    url = 'https://predpredaj.zoznam.sk/sk/kategoria/koncert/'
    r = requests.get(url)
    soup = BeautifulSoup(r.text, 'html.parser')
    concerts = soup.find_all('article')
    
    def extract_concert_url(concert):
        return f'https://predpredaj.zoznam.sk{concert.find("a")["href"]}'

    concert_urls = [extract_concert_url(c) for c in concerts if 'darcekove-poukazy' not in extract_concert_url(c)]
    
    concert_data = [extract_concert_performances(url) for url in concert_urls]
    concert_data = [item for sublist in concert_data for item in sublist]

    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'venue', 'city', 'description'])
    df.drop_duplicates(subset=['title', 'date', 'url'], inplace=True)
    df = df[df['city'].notna()].copy()
    
    df.insert(0, 'source_url', 'https://predpredaj.zoznam.sk/')
    df.insert(0, 'source', 'Zoznam.sk')
    
    save_path = 'data/predpredaj_zoznam_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    inserted_count, skipped_count = upload_potential_concerts(concert_data)
    print(f'Uploaded {inserted_count} concerts, skipped {skipped_count} concerts')

if __name__ == '__main__':
    main()
