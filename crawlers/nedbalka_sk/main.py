import requests
import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts

MONTH_TO_NUMBER = {
    'január': 1,
    'február': 2,
    'marec': 3,
    'apríl': 4,
    'máj': 5,
    'jún': 6,
    'júl': 7,
    'august': 8,
    'september': 9,
    'október': 10,
    'november': 11,
    'december': 12,
}

def extract_concerts(soup):
    article = soup.find('div', class_='article')
    ols = article.find_all('ol')
    concerts = []
    for ol in ols:
        date = ol.text.removeprefix('predstavenie: ')
        date_parts = date.split()
        year = date_parts[2].strip(',')
        month = MONTH_TO_NUMBER[date_parts[1]]
        day = date_parts[0].strip('.')
        time_from = date_parts[3]
        
        title = ol.next_sibling
        composers = title.next_sibling.text.split('–')
        composers = [c.strip() for c in composers]
        concerts.append({
            'title': title.text.strip(),
            'date': f'{year}-{month:02d}-{day}',
            'url': ol.find_next('a')['href'],
            'time_from': time_from,
            'composers': composers,
        })
    return concerts

def main():
    print('Getting concerts for nedbalka.sk ...')
    url = 'https://www.nedbalka.sk/aktuality/koncerty-musica_litera/'
    
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    concert_data = extract_concerts(soup)

    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'composers'])
    df.insert(0, 'venue', 'Galéria Nedbalka')
    df.insert(0, 'city', 'Bratislava')
    df.insert(0, 'source_url', 'https://nedbalka.sk')
    df.insert(0, 'source', 'Galéria Nedbalka')

    print(f'Found {len(concert_data)} concerts')
    
    save_path = 'data/nedbalka_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')

    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    inserted_count, skipped_count = upload_concerts(concert_data)
    print(f'Uploaded {inserted_count} concerts, skipped {skipped_count} concerts')
    
    return concert_data

if __name__ == '__main__':
    main()