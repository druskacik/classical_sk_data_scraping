import json
import requests
from bs4 import BeautifulSoup

import pandas as pd

from ..classical import upload_potential_concerts
from ..extractors import extract_date, extract_time
from ..formaters import format_date

def extract_event_url(concert):
    a = concert.find('a', class_='tt-evt-li__name')
    url = a['href']
    return url

def crawl_event_urls():
    url = 'https://podujatia.pkopresov.sk/'
    print(url)
    r = requests.get(url)
    soup = BeautifulSoup(r.text, 'html.parser')
    posts_widget = soup.find('div', class_='elementor-element', attrs={'data-widget_type': 'tootoot-event-list.tiles'})
    
    event_elements = posts_widget.find_all('div', class_='tt-evt-li__event-info')
    event_urls = [extract_event_url(event) for event in event_elements]
    data_id = posts_widget['data-id']
    
    page = 1
    while True:
        url = f'https://podujatia.pkopresov.sk/wp-json/elementor-pro/v1/posts-widget?post_id=1100&element_id={data_id}&page={page}'
        print(url)
        r = requests.get(url)
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

    return event_urls

def extract_event_info(url):
    print(url)
    r = requests.get(url)
    soup = BeautifulSoup(r.text, 'html.parser')
    script = soup.find('script', type='application/ld+json')
    info = json.loads(script.text)
    
    ps = soup.find_all('div', class_='elementor-widget-text-editor')
    next_is_about = False
    description = None
    for p in ps:
        if next_is_about:
            description = p.get_text().strip()
            break
        if p.text.strip() == 'About':
            next_is_about = True
    return {
		'title': info['name'],
		'date': info['startDate'].split('T')[0],
		'time_from': info['startDate'].split('T')[1][:5],
		'venue': info['location']['name'],
		'city': info['location']['address']['addressLocality'],
		'url': url,
		'description': description
	}



def main():
    event_urls = crawl_event_urls()
    event_data = [extract_event_info(url) for url in event_urls]

    df = pd.DataFrame(event_data, columns=['title', 'date', 'url', 'time_from', 'venue', 'city', 'description'])
    df.drop_duplicates(subset=['title', 'date', 'url'], inplace=True)
    
    df.insert(0, 'source_url', 'https://podujatia.pkopresov.sk/')
    df.insert(0, 'source', 'Park kultúry a oddychu')
    
    save_path = 'data/pkopresov_sk.csv'
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
