import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts
from ..extractors import extract_date, extract_time, clean_string

def format_date(date):
    dd, mm, yy = date.split('.')
    dd, mm, yy = int(dd), int(mm), int(yy)
    return f'{yy}-{mm:02d}-{dd:02d}'

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


def main():
    print('Getting concerts for simachart.weebly.com ...')
    url = 'https://simachart.weebly.com/bude.html'
    
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    concert_data = extract_concerts(soup)
    
    print(f'Found {len(concert_data)} concerts')
        
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'time_from', 'venue', 'city'])
    df.insert(0, 'url', url)
    df.insert(0, 'source_url', 'https://simachart.weebly.com')
    df.insert(0, 'source', 'Simachart')
    
    save_path = 'data/simachart_weebly_com.csv'
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
