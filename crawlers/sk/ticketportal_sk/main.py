import esprima
from bs4 import BeautifulSoup
import requests
import re

import pandas as pd

from ...base import BaseCrawler, CrawlerConfig

ALREADY_PARSED_ORGANIZERS = [
    'https://www.ticketportal.sk/NEvent/SLOVENSKA_FILHARMONIA'
]
MUSIC_PATTERNS = [
    r'\borgan\w*',
    r'\bfilharm\w*',
    r'\bkomorn\w*',
    r'\borchester\b',
    r'\borchestra\b',
    r'\bsymfon\w*',
    r'\bsymphon\w*',
    r'\bpiano\b',
    r'\bklav[ií]r\w*',
    r'\bopera\b',
    r'\bopern\w*',
    r'\bbach\b',
    r'\bmozart\b',
    r'\bverdi\b',
]

def extract_variable(ast, name):
    for node in ast.body:
        if node.type == "VariableDeclaration" and node.declarations[0].id.name == name:
            return node.declarations[0].init
    return None

def eval_element(element):
    if element.type == 'Literal':
        return element.value
    elif element.type == 'ArrayExpression':
        return [eval_element(e) for e in element.elements]
    elif element.type == 'UnaryExpression':
        return eval(f"{element.operator}{element.argument.value}")
    else:
        raise ValueError(f"Unknown element type: {element.type}")
    
def array_to_df(array, columns):
	# Create a DataFrame directly from the list without reshaping
	# This handles the case where elements of elems are lists
	if columns is None:
		columns = ['id', 'title', 'id_0', 'category', 'id_1', 'img', 'id_podujatie_out', 'zvyraznenie', 'score']
	data = []
	for i in range(0, len(array), len(columns)):
		if i + len(columns) - 1 < len(array):  # Ensure we have a complete row
			row = {}
			for j in range(len(columns)):
				row[columns[j]] = array[i+j]
			data.append(row)

	df = pd.DataFrame(data)
	return df

def is_relevant_music_event(title):
    title = str(title).lower()
    return any(re.search(pattern, title) for pattern in MUSIC_PATTERNS)

def get_slug(df_out, title):
    try:
        return df_out.loc[title]['slug']
    except:
        # Find rows where index starts with the title
        matching_rows = df_out[df_out.index.str.startswith(title)]
        if not matching_rows.empty:
            return matching_rows.iloc[0]['slug']
        return None

def get_classical_concerts():
    url = 'https://tpskprodcdn.azureedge.net/Grid/Data?v=1&lang=SK'
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    text = r.text
    ast = esprima.parse(text)
    
    events_out = extract_variable(ast, "data_podujatie_out")
    events_out = eval_element(events_out)
    df_out = array_to_df(events_out, ['id', 'title', 'slug', 'x_0', 'x_1'])
    df_out.set_index('title', inplace=True)
    
    events = extract_variable(ast, "data_podujatie")
    events = eval_element(events)
    df = array_to_df(events, ['id', 'title', 'id_0', 'category', 'id_1', 'img', 'id_podujatie_out', 'zvyraznenie', 'score'])
    df = df[['id', 'title', 'category']].copy()
    
    df = df[df['title'].apply(is_relevant_music_event)].copy()
    df['slug'] = df['title'].apply(lambda x: get_slug(df_out, x))
    df = df[df['slug'].notna()].copy()
    
    return df

def extract_organizator_url(soup):
    header = soup.find('div', class_='detail-content').find('h1')
    a = header.find('a')
    if a:
        return a['href']
    return None

def extract_concert_info(div):
    title_tag = div.find('div', class_='event') or div.find('div', itemprop='name')
    title = title_tag.get_text(' ', strip=True)
    title = re.sub(r'\s+\d{1,2}\.\d{1,2}\.\d{4}\s+od\s+\d{1,2}:\d{2}\s+hod\.$', '', title).strip()
    date = div.find('div', itemprop='startDate')['content']
    
    location = div.find('div', itemprop='location')
    venue = location.find('span', itemprop='name').text.strip()
    city = location.find('div', itemprop='address').text.strip()
    return {
			'title': title,
			'date': date.split('T')[0],
		'time_from': date.split('T')[1] if 'T' in date else None,
		'venue': venue,
		'city': city,
	}
    
def extract_description(soup):
    popis_section = soup.find('section', class_='popis')
    if popis_section:
        # Remove the ticket-guarantee-container div if it exists
        guarantee_div = popis_section.find('div', class_='ticket-guarantee-container')
        if guarantee_div:
            guarantee_div.decompose()
        return popis_section.get_text().strip()
    return None


class TicketportalCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ticketportal_sk',
        source='Ticketportal.sk',
        source_url='https://www.ticketportal.sk',
        columns=['title', 'date', 'time_from', 'venue', 'city', 'url', 'organizer_url', 'description'],
        upload_target='potential',
        front_fields=[
            ('source_url', 'https://www.ticketportal.sk'),
            ('source', 'Ticketportal.sk'),
        ],
    )

    def scrape(self):
        df = get_classical_concerts()

        concert_data = []
        for _, row in df.iterrows():
            url = f'https://www.ticketportal.sk/event/{row["slug"]}'
            print(f'Processing {url}')
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            soup = BeautifulSoup(r.text, 'html.parser')
            description = extract_description(soup)
            divs = soup.find_all('div', itemtype='http://schema.org/Event')
            for div in divs:
                try:
                    concert_info = {
                        **extract_concert_info(div),
                        'url': url,
                        'organizer_url': extract_organizator_url(soup),
                        'description': description,
                    }
                    concert_data.append(concert_info)
                except Exception as e:
                    print(f'Error processing div in {url}: {div}')
                    print(e)

        return concert_data

    def transform(self, df):
        df = df[~df['organizer_url'].isin(ALREADY_PARSED_ORGANIZERS)]
        df.drop_duplicates(subset=['title', 'date', 'time_from', 'venue', 'city', 'url'], inplace=True)
        return df


def main():
    TicketportalCrawler().run()

if __name__ == '__main__':
    main()
