import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts
from ..extractors import extract_date, extract_time

def format_date(date_str):
	day, month, year = date_str.split('.')
	year = int(year.strip())
	month = int(month.strip())
	day = int(day.strip())
	return f'{year}-{month:02d}-{day:02d}'

def remove_non_breaking_spaces(text):
    return text.replace('\xa0', ' ').strip()

def extract_concert_info(concert_div):
    
    title = concert_div.find('font', class_='wsw-05')
    if title is not None:
        title = title.text.strip()
    else:
        title = concert_div.find('p').text.strip()
    
    date_and_venue_text = concert_div.find('h3').text.strip()
    date = extract_date(date_and_venue_text)
    time = extract_time(date_and_venue_text)
    
    # Extract venue as text after 'hod.' and strip punctuation
    venue = None
    venue_part = None if 'hod' not in date_and_venue_text else date_and_venue_text.split('hod')[-1]
    if venue_part:
        venue = venue_part.strip().strip(',.;:- ')
        venue = remove_non_breaking_spaces(venue)
        
    # Extract URL from the "viac o programe" link
    url = None
    program_link = concert_div.find(lambda tag: tag.name == 'a' and 'viac o programe' in tag.text)
    if program_link:
        href = program_link.get('href')
        if href:
            url = f"https://www.kpvh.sk{href}"
    
    return {
		'title': remove_non_breaking_spaces(title),
		'date': format_date(date),
		'url': url,
		'time_from': time,
		'venue': venue
	}

def main():
    print('Getting concerts for kpvh.sk ...')
    url = 'https://www.kpvh.sk/sezona-2024-2025/'
    
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    concert_divs = soup.find_all('div', class_='mt-i cf')
    concert_data = [extract_concert_info(div) for div in concert_divs]
    
    print(f'Found {len(concert_data)} concerts')
        
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'venue'])
    df.drop_duplicates(subset=['title', 'date', 'url'], inplace=True)
    df.insert(0, 'city', 'Trenčín')
    df.insert(0, 'source_url', 'https://www.kpvh.sk')
    df.insert(0, 'source', 'Klub priateľov vážnej hudby')
    
    save_path = 'data/kpvh_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    upload_concerts(concert_data)
    
if __name__ == '__main__':
    main()
