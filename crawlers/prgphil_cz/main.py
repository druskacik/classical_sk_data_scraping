import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import re

from ..classical import upload_concerts

MONTHS_MAPPING = {
    'Jan': '01',
    'Feb': '02',
    'Mar': '03',
    'Apr': '04',
    'Máj': '05',
    'Jún': '06',
    'Júl': '07',
    'Aug': '08',
    'Sep': '09',
    'Okt': '10',
    'Nov': '11',
    'Dec': '12',
}

def parse_date_time(date_str):
    """
    Parse date and time from string like '10. 04. 25 Čt • 19:00'
    Returns tuple (date, time)
    """
    # Split date and time
    parts = date_str.split('•')
    date_part = parts[0].strip()
    time_part = parts[1].strip() if len(parts) > 1 else None
    
    # Parse date
    day, month, year = [x.strip(' .') for x in date_part.split()[:3]]
    date = f'20{year}-{month.zfill(2)}-{day.zfill(2)}'
    
    return date, time_part

def get_concert_data():
    """
    Get concert data from prgphil.cz
    """
    url = 'https://www.prgphil.cz'
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'lxml')
    
    concerts = []
    
    # Find all concert links in the upcoming concerts section
    concert_links = soup.find_all('a', href=re.compile(r'/(lobkowicz|festival-colmar|antonio-vivaldi)'))
    
    for link in concert_links:
        # Skip if not a concert link
        if not link.find('time'):
            continue
            
        date_time = link.find('time').text
        date, time = parse_date_time(date_time)
        
        # Extract venue
        venue_text = link.text.split('•')[1].strip() if '•' in link.text else None
        venue = venue_text.split('\n')[0] if venue_text else None
        
        concerts.append({
            'title': link.find('h3').text if link.find('h3') else None,
            'url': f"{url}{link['href']}",
            'date': date,
            'time_from': time,
            'venue': venue
        })
    
    return concerts

def get_concert_description(url):
    """
    Get concert description from prgphil.cz
    """
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'lxml')
    
    # Find main article content
    article = soup.find('article')
    if not article:
        return None
        
    # Extract program details
    program_items = []
    for p in article.find_all('p'):
        if p.find('strong'):
            program_items.append(p.text.strip())
            
    # Extract description paragraphs
    description_paras = []
    for p in article.find_all('p'):
        if not p.find('strong') and p.text.strip():
            description_paras.append(p.text.strip())
            
    description = {
        'program': '\n'.join(program_items),
        'description': '\n'.join(description_paras)
    }
    
    return description

def main():
    print('Getting concerts for prgphil.cz ...')
    concert_data = get_concert_data()
    print(f'Found {len(concert_data)} concerts')
    
    # Get detailed descriptions
    for concert in concert_data:
        description = get_concert_description(concert['url'])
        if description:
            concert.update(description)
            
    df = pd.DataFrame(concert_data)
    df.insert(0, 'city', 'Prague')
    df.insert(0, 'source_url', 'https://www.prgphil.cz')
    df.insert(0, 'source', 'Prague Philharmonia')
    
    save_path = 'data/prgphil_cz.csv'
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