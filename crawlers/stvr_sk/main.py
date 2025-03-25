import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts

MONTHS_MAP = {
    'január': '01',
    'február': '02',
    'marec': '03',
    'apríl': '04',
    'máj': '05',
    'jún': '06',
    'júl': '07',
    'august': '08',
    'september': '09',
    'október': '10',
    'november': '11',
    'december': '12',
}

def format_date(date):
    day, month = date.split('.')
    month = MONTHS_MAP[month.strip().lower()]
    return f'2025-{month}-{day}'

def extract_composer(line):
    if '(' in line and ')' in line:
        return line.split('(')[0].strip()
    return None

def extract_concerts(soup):
    body = soup.find('div', class_='article__body')
    ps = body.find_all('p')
    
    n_line = 0
    data = []
    concert = {}

    for p in ps:
        # This means its a new concert
        if p.attrs.get('align') == 'center':
            n_line = 1
            if concert:
                data.append(concert)
                concert = {}
            concert = {
                'date': p.text.strip(),
                'composers': [],
            }
        elif n_line > 0:
            if n_line == 1:
                interpreter = p.find('b').text.strip()
                concert['interpreter'] = interpreter
                for line in p.get_text(strip=True, separator='\n').splitlines():
                    composer = extract_composer(line)
                    if composer:
                        concert['composers'].append(composer)
            else:
                for line in p.get_text(strip=True, separator='\n').splitlines():
                    composer = extract_composer(line)
                    if composer:
                        concert['composers'].append(composer)
            n_line += 1
            
    if concert:
        data.append(concert)
        
    return data


def main():
    print('Getting concerts for devin.stvr.sk ...')
    url = 'https://devin.stvr.sk/clanky/koncerty-live/388941/cyklus-organovych-koncertov-pod-pyramidou-januar-jun-2025'
    
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    concert_data = extract_concerts(soup)
    base_name = 'Cyklus Organových koncertov pod pyramídou'
    
    df = pd.DataFrame(concert_data, columns=['date', 'interpreter', 'composers'])
    df['title'] = df['interpreter'].apply(lambda x: f'{base_name} - {x}')
    df['date'] = df['date'].apply(format_date)
    
    df.insert(0, 'time_from', '10:30')
    df.insert(0, 'venue', 'Veľké koncertné štúdio Slovenského rozhlasu')
    df.insert(0, 'city', 'Bratislava')
    df.insert(0, 'url', url)
    df.insert(0, 'source_url', 'https://devin.stvr.sk')
    df.insert(0, 'source', 'STVR')
    
    print(f'Found {len(concert_data)} concerts')
    
    save_path = 'data/stvr_sk.csv'
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
