import re

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

def clean_string(text):
    text = text.replace('\u200b', '')
    text = text.replace('\xa0', ' ').strip()
    return text

