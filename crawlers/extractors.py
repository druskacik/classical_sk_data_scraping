import re

import pandas as pd

CITIES_WITH_POSTAL_CODE = pd.read_csv('data/cities_with_postal_code.csv', dtype={'postal_code': str})
CITIES_BY_POPULATION = pd.read_csv('data/cities_by_population.csv', dtype={'postal_code': str})
SPECIAL_CITIES = {
	'8': 'Bratislava',
	'040': 'Košice',
	'949': 'Nitra',
	'010': 'Žilina',
 	'011': 'Žilina',
 	'95050': 'Nitra',
  	'940': 'Nové Zámky',
	'927': 'Šaľa',
    '97201': 'Bojnice',
    '91451': 'Trenčianske Teplice',
    '08501': 'Bardejov',
    '08631': 'Bardejov',
}

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
    # Format with dots and optional spaces: 13. 4. 2025 or 13.4.2025
    date_pattern = r'(\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*(?:\d{4}|\d{2}))'
    match = re.search(date_pattern, text)
    if match:
        return match.group(1)
    
    # Alternative format with slashes
    date_pattern = r'(\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))'
    match = re.search(date_pattern, text)
    if match:
        return match.group(1)
    
    return None

def extract_postal_code(text):
    """
    Extract the first occurrence of a postal code (5 digits, potentially with a space after the third digit).
    
    Args:
        text (str): The text to search for postal code.
        
    Returns:
        str or None: The extracted postal code, or None if no postal code is found.
    """
    # Pattern for 5 digits in a row (e.g., 12345)
    postal_pattern = r'(\d{5})'
    match = re.search(postal_pattern, text)
    if match:
        return match.group(1)
    
    # Pattern for 3 digits followed by a space and then 2 digits (e.g., 123 45)
    postal_pattern_with_space = r'(\d{3}\s\d{2})'
    match = re.search(postal_pattern_with_space, text)
    if match:
        # Remove the space to standardize the format
        return match.group(1).replace(' ', '')
    
    return None


def extract_city(text):
    """
    Extract the city from a string using the postal code.
    
    Args:
        text (str): The text to search for city.
        
    Returns:
        str or None: The extracted city, or None if no city is found.
    """
    postal_code = extract_postal_code(text)
    if postal_code:
        potential_cities = CITIES_WITH_POSTAL_CODE[CITIES_WITH_POSTAL_CODE['postal_code'] == postal_code]['city'].values
        for city in potential_cities:
            if city in text:
                return city
        for key, value in SPECIAL_CITIES.items():
            if postal_code.startswith(key):
                return value
        
    for city in CITIES_BY_POPULATION['city'].values:
        if city in text:
            return city
    return None

def clean_string(text):
    text = text.replace('\u200b', '')
    text = text.replace('\xa0', ' ').strip()
    return text

