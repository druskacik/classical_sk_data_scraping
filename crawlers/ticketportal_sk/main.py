import esprima
from bs4 import BeautifulSoup
import requests

import pandas as pd

from ..classical import upload_concerts

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

def is_classical(category):
    if isinstance(category, list):
        return 4 in category
    if pd.isna(category):
        return False
    return category == 4

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
    r = requests.get(url)
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
    
    df_is_classical = df['category'].apply(is_classical)
    df = df[df_is_classical].copy()
    df['slug'] = df['title'].apply(lambda x: get_slug(df_out, x))
    
    return df

def extract_organizator_url(soup):
    header = soup.find('div', class_='detail-content').find('h1')
    a = header.find('a')
    if a:
        return a['href']
    return None

def extract_concert_info(div):
    title = div.find('div', itemprop='name').text.strip()
    date = div.find('div', itemprop='startDate')['content']
    
    location = div.find('div', itemprop='location')
    venue = location.find('span', itemprop='name').text.strip()
    city = location.find('div', itemprop='address').text.strip()
    return {
		'title': title,
		'date': date.split('T')[0].replace('-', '/'),
		'time_from': date.split('T')[1] if 'T' in date else None,
		'venue': venue,
		'city': city,
	}



def main():
    
    ALREADY_PARSED_ORGANIZERS = [
        'https://www.ticketportal.sk/NEvent/SLOVENSKA_FILHARMONIA'
    ]
    
    df = get_classical_concerts()
    
    concert_data = []
    for _, row in df.iterrows():
        url = f'https://www.ticketportal.sk/event/{row["slug"]}'
        print(f'Processing {url}')
        r = requests.get(url)
        soup = BeautifulSoup(r.text, 'html.parser')
        divs = soup.find_all('div', itemtype='http://schema.org/Event')
        for div in divs:
            concert_info = {
                **extract_concert_info(div),
                'url': url,
                'organizer_url': extract_organizator_url(soup),
            }
            concert_data.append(concert_info)
    
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'time_from', 'venue', 'city', 'url', 'organizer_url'])
    df.insert(0, 'source_url', 'https://www.ticketportal.sk')
    df.insert(0, 'source', 'Ticketportal.sk')
    df = df[~df['organizer_url'].isin(ALREADY_PARSED_ORGANIZERS)]
    df.drop_duplicates(subset=['title', 'date', 'time_from', 'venue', 'city', 'url'], inplace=True)
    
    save_path = 'data/ticketportal_sk.csv'
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


