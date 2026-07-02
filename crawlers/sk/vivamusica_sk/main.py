import requests
import re
from datetime import date as date_cls, datetime

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig

def convert_date(date_str, year):
    """
    Argument:
        date_str (str): Date string in format 'dd.mm'
    Returns:
        str: Converted date string in format 'yyyy-mm-dd'
    """
    date_str = date_str.split('.')
    day = int(date_str[0])
    month = int(date_str[1])
    return f'{year}-{month:02d}-{day:02d}'

def extract_program_year(soup):
    text = soup.get_text(' ', strip=True)
    match = re.search(r'Koncerty\s+(20\d{2})', text)
    if match:
        return int(match.group(1))
    years = [int(year) for year in re.findall(r'20\d{2}', text)]
    if years:
        return max(years)
    return datetime.now().year

def find_concert_links(soup):
    a_tags = soup.find_all('a', class_='n2-ow')
    
    concert_links = []
    for a in a_tags:
        if a.text == 'Viac informácií':
            concert_links.append(a.get('href'))
    for a in soup.find_all('a', class_='concert', href=True):
        concert_links.append(a.get('href'))
    concert_links = list(set(concert_links))
    concert_links = [f'https://vivamusica.sk{link}' if link.startswith('/') else link for link in concert_links]
    return concert_links
    
def extract_concert_info(url, year):
    r = requests.get(url, timeout=20)
    soup = BeautifulSoup(r.content, 'html.parser')
    main_text = soup.find('div', class_='main-text') or soup
    
    title_tag = main_text.find('h1')
    date_tag = main_text.find('p', class_='time')
    place_and_time = main_text.find('div', class_='miesto-a-cas')
    if title_tag is None or date_tag is None or place_and_time is None:
        return None

    title = title_tag.text.strip()
    subtitle_tag = main_text.find('p', class_='concert-podtitul')
    subtitle = subtitle_tag.text.strip() if subtitle_tag else ''

    date = date_tag.text.strip()
    formatted_date = convert_date(date, year)
    if date_cls.fromisoformat(formatted_date) < date_cls.today():
        return None
    place = place_and_time.find('span').text.strip()
    time_tag = place_and_time.find('span', class_='cas')
    time = time_tag.text.strip() if time_tag else None
    
    descs = soup.find_all('div', class_='concert-desc')
    description = '\n\n'.join(desc.text.strip() for desc in descs)

    return {
        'title': f'{title}: {subtitle}' if subtitle else title,
        'date': formatted_date,
        'url': url,
        'venue': place,
        'time_from': time.replace('.', ':') if time else None,
        'description': description
    }
    
class VivaMusicaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='vivamusica_sk',
        source='Viva Musica!',
        source_url='https://www.vivamusica.sk',
        columns=['title', 'date', 'url', 'time_from', 'venue', 'description'],
        front_fields=[
            ('city', 'Bratislava'),
            ('source_url', 'https://www.vivamusica.sk'),
            ('source', 'Viva Musica!'),
        ],
    )

    def scrape(self):
        url = 'https://www.vivamusica.sk'
        r = requests.get(url, timeout=20)
        soup = BeautifulSoup(r.content, 'html.parser')
        year = extract_program_year(soup)

        concert_links = find_concert_links(soup)
        concerts = [extract_concert_info(link, year) for link in concert_links]
        return [concert for concert in concerts if concert is not None]


def main():
    VivaMusicaCrawler().run()


if __name__ == '__main__':
    main()
