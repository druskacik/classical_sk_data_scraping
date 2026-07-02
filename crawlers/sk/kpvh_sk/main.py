import re
from datetime import date as date_cls

import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from ...extractors import extract_date, extract_time
from ...formaters import format_date

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
    if date is None:
        return None
    formatted_date = format_date(date)
    if date_cls.fromisoformat(formatted_date) < date_cls.today():
        return None
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
			'date': formatted_date,
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
    if not url:
        return []
    print(url)
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    content = soup.find('div', class_='mt-pricelist')
    if content is None:
        return []
    hs = content.find_all('h3')
    composers = [clean_composer_name(h.text.strip()) for h in hs]
    return list(set(composers))

class KpvhCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='kpvh_sk',
        source='Klub priateľov vážnej hudby',
        source_url='https://www.kpvh.sk',
        columns=['title', 'date', 'url', 'time_from', 'venue'],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('city', 'Trenčín'),
            ('source_url', 'https://www.kpvh.sk'),
            ('source', 'Klub priateľov vážnej hudby'),
        ],
    )

    def scrape(self):
        url = 'https://www.kpvh.sk/sezona-2025-2026/'
        r = requests.get(url)
        soup = BeautifulSoup(r.content, 'html.parser')

        concert_divs = soup.find_all('div', class_='mt-i cf')
        concerts = [extract_concert_info(div) for div in concert_divs]
        return [concert for concert in concerts if concert is not None]

    def transform(self, df):
        if self.config.dedupe_subset:
            df.drop_duplicates(subset=self.config.dedupe_subset, inplace=True)
        df['composers'] = df['url'].apply(extract_composers)
        return df


def main():
    KpvhCrawler().run()


if __name__ == '__main__':
    main()
