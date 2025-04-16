import requests
from datetime import datetime

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts

def convert_date(date_str):
    """
    Argument:
        date_str (str): Date string in format 'dd.mm'
    Returns:
        str: Converted date string in format 'yyyy-mm-dd'
    """
    current_year = datetime.now().year
    date_str = date_str.split('.')
    day = int(date_str[0])
    month = int(date_str[1])
    return f'{current_year}-{month:02d}-{day:02d}'

def find_concert_links(soup):
    a_tags = soup.find_all('a', class_='n2-ow')
    
    concert_links = []
    for a in a_tags:
        if a.text == 'Viac informácií':
            concert_links.append(a.get('href'))
    concert_links = list(set(concert_links))
    concert_links = [f'https://vivamusica.sk{link}' for link in concert_links]
    return concert_links

def extract_concert_info(url):
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    main_text = soup.find('div', class_='main-text')
    
    title = main_text.find('h1').text.strip()
    subtitle = main_text.find('p', class_='concert-podtitul').text.strip()
    
    date = main_text.find('p', class_='time').text.strip()
    place_and_time = main_text.find('div', class_='miesto-a-cas')
    place = place_and_time.find('span').text.strip()
    time = place_and_time.find('span', class_='cas').text.strip()
    
    descs = soup.find_all('div', class_='concert-desc')
    description = f'{descs[0].text.strip()}\n\n{descs[1].text.strip()}'

    return {
        'title': f'{title}: {subtitle}',
		'date': convert_date(date),
        'url': url,
        'venue': place,
        'time_from': time.replace('.', ':'),
        'description': description
	}
    
def main():
    print('Getting concerts for vivamusica.sk ...')
    url = 'https://www.vivamusica.sk'
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')

    concert_links = find_concert_links(soup)
    concert_data = [extract_concert_info(link) for link in concert_links]

    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'venue', 'description'])
    df.insert(0, 'city', 'Bratislava')
    df.insert(0, 'source_url', 'https://www.vivamusica.sk')
    df.insert(0, 'source', 'Viva Musica!')

    save_path = 'data/vivamusica_sk.csv'
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



