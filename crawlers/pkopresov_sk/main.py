import requests
from bs4 import BeautifulSoup

import pandas as pd

from ..classical import upload_potential_concerts
from ..extractors import extract_date, extract_time
from ..formaters import format_date

def crawl_concerts():
    url = 'https://podujatia.pkopresov.sk/'
    print(url)
    r = requests.get(url)
    soup = BeautifulSoup(r.text, 'html.parser')
    posts_widget = soup.find('div', class_='elementor-element', attrs={'data-widget_type': 'tootoot-event-list.tiles'})
    
    concert_elements = posts_widget.find_all('div', class_='tt-evt-li__event-info')
    data_id = posts_widget['data-id']
    
    page = 1
    while True:
        url = f'https://podujatia.pkopresov.sk/wp-json/elementor-pro/v1/posts-widget?post_id=1100&element_id={data_id}&page={page}'
        print(url)
        r = requests.get(url)
        data = r.json()
        soup = BeautifulSoup(data['content'], 'html.parser')
        concerts = soup.find_all('div', class_='tt-evt-li__event-info')
        if len(concerts) == 0:
            break
        concert_elements.extend(concerts)
        page += 1

    return concert_elements

def extract_concert_info(concert):
    a = concert.find('a', class_='tt-evt-li__name')
    title = a.text.strip()
    url = a['href']
    
    date = concert.find('div', class_='tt-evt-li__sub-info--BeginDate').text.strip()
    date = extract_date(date)
    date = format_date(date)
    
    time = concert.find('div', class_='tt-evt-li__sub-info--BeginTime').text.strip()
    time = extract_time(time)
    
    venue = concert.find('div', class_='tt-evt-li__sub-info--BuildingName').text.strip()
    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time,
        'venue': venue
    }

def main():
    concert_elements = crawl_concerts()
    concert_data = [extract_concert_info(concert) for concert in concert_elements]

    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'venue'])
    df.drop_duplicates(subset=['title', 'date', 'url'], inplace=True)
    
    df.insert(0, 'city', 'Prešov')
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
