import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts
from ..formaters import format_date

def extract_event_info(event):
    tag = event.find('span', class_='tag').find('a')
    title = tag['data-name']
    date_and_time = tag['data-date']
    date, time = date_and_time.split(' ')
    venue = tag['data-location']
    return {
		'title': title,
		'date': date.replace('-', '/'),
		'time_from': time,
		'venue': venue
	}


def main():
    print('Getting concerts for kultura.trnava.sk ...')
    url = 'https://kultura.trnava.sk/podujatie/trnavska-hudobna-jar-2025'
    
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    concert_divs = soup.find_all('div', class_='event')
    concert_data = [extract_event_info(div) for div in concert_divs]
    print(f'Found {len(concert_data)} concerts')
        
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'time_from', 'venue'])
    df.insert(0, 'city', 'Trnava')
    df.insert(0, 'url', url)
    df.insert(0, 'source_url', 'https://kultura.trnava.sk')
    df.insert(0, 'source', 'Zaži v Trnave')
    
    save_path = 'data/kultura_trnava_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    inserted_count, skipped_count = upload_concerts(concert_data)
    print(f'Uploaded {inserted_count} concerts, skipped {skipped_count} concerts')
    
if __name__ == '__main__':
    main()
