import time
import datetime

import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts

import re

def extract_time(text):
    """
    Extract the first occurrence of a time in format HH:MM from a string.
    
    Args:
        text (str): The text to search for time.
        
    Returns:
        str or None: The extracted time in format HH:MM, or None if no time is found.
    """
    time_pattern = r'(\d{1,2}:\d{2})'
    match = re.search(time_pattern, text)
    if match:
        return match.group(1)
    return None

def extract_date(text):
    """
    Extract the first occurrence of a date in format dd.mm.yyyy from a string.
    
    Args:
        text (str): The text to search for date.
        
    Returns:
        str or None: The extracted date in format dd.mm.yyyy, or None if no date is found.
    """
    date_pattern = r'(\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2}))'
    match = re.search(date_pattern, text)
    if match:
        return match.group(1)
    
    # Alternative format with slashes
    date_pattern = r'(\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))'
    match = re.search(date_pattern, text)
    if match:
        return match.group(1)
    
    return None

def convert_date(date_str):
    """
    Convert date string to format 'yyyy-mm-dd'
    """
    if not isinstance(date_str, str):
        return None
    sep = '/' if '/' in date_str else '.'
    return f'{date_str.split(sep)[2]}-{date_str.split(sep)[1]}-{date_str.split(sep)[0]}'

def validate_concert(concert):
    if concert['date'] is None:
        return False
    if 'permanentka' in concert['title'].lower():
        return False
    return True

def validate_venue(venue):
    if venue in ['Moyzesova sieň']:
        return 'Moyzesova sieň'
    if 'Dom hudby' in venue:
        return 'Dom hudby'
    return None

def extract_concert_info(concert):
    title = concert.find('span', class_='clickable--ProfileName').text.strip()
    url = concert.find('a')['href']
    info = concert.find('div', class_='tt-evt-li__sub-info').text.strip()
    date = extract_date(info)
    venue = info.split(' / ')[1]
    time = extract_time(info)
    return {
		'title': title,
		'url': url,
		'date': convert_date(date),
		'time_from': time,
		'time_to': None,
		'venue': validate_venue(venue),
	}
    
def main():
    print('Getting concerts for konvergencie.sk ...')
    url = 'https://www.konvergencie.sk/vstupenky/'
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')

    concerts = soup.find_all('div', class_='tt-evt-li')
    concert_data = []
    for concert in concerts:
        concert_data.append(extract_concert_info(concert))

    concert_data = [c for c in concert_data if validate_concert(c)]
    
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue'])
    df.insert(0, 'city', 'Bratislava')
    df.insert(0, 'source_url', 'https://www.konvergencie.sk')
    df.insert(0, 'source', 'Konvergencie')

    save_path = 'data/konvergencie_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    upload_concerts(concert_data)

if __name__ == '__main__':
    main()



