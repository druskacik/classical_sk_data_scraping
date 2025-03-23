import re
import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts
from ..extractors import extract_date, extract_time
from ..formaters import format_date

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

def clean_whitespace(text):
    return re.sub(r'\s+', ' ', text).strip()
    
def clean_composer_name(name):
    # Remove text in parentheses from composer name
    # e.g., "Beethoven (1800)" -> "Beethoven"
    for i, char in enumerate(name):
        if char == '(' or char.isdigit():
            return clean_whitespace(name[:i].strip())
    return clean_whitespace(name)
    
def extract_composers(url):
    print(url)
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    content = soup.find('div', class_='mt-pricelist')
    hs = content.find_all('h3')
    composers = [clean_composer_name(h.text.strip()) for h in hs]
    return list(set(composers))

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
    df['composers'] = df['url'].apply(extract_composers)
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
    inserted_count, skipped_count = upload_concerts(concert_data)
    print(f'Uploaded {inserted_count} concerts, skipped {skipped_count} concerts')
    
if __name__ == '__main__':
    main()
