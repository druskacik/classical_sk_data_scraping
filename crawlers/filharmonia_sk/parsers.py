import re

from bs4 import BeautifulSoup

def get_paragraphs_after_heading(soup, heading_text):
    """
    Find the first paragraph element that follows a specific h3 heading.
    
    Args:
        soup (BeautifulSoup): The BeautifulSoup object containing the parsed HTML
        heading_text (str): The text of the h3 heading to search for
    
    Returns:
        bs4.element.Tag or None: The first paragraph element after the heading, or None if not found
    """
    
    # Find the specific h3 heading
    target_heading = soup.find(['h1', 'h2', 'h3'], string=heading_text)
    
    if not target_heading:
        return []
    
    current_element = target_heading.next_sibling
    
    paragraphs = []
    # Collect all paragraphs until we hit the next h3 or run out of elements
    while current_element and current_element.name not in ['h1', 'h2', 'h3']:
        if current_element.name == 'p':
            paragraphs.append(current_element)
        current_element = current_element.next_sibling
    
    return paragraphs

def extract_performers(soup):
    """
    Extract performer names from HTML content by finding the paragraph after 'Účinkujú' heading
    and parsing strong tags within it. Returns a list of performer names.
    """
    performing_paragraphs = get_paragraphs_after_heading(soup, "Účinkujú")
    if len(performing_paragraphs) == 0:
        return []
    
    performing_paragraph = performing_paragraphs[0]
    performers = []
    for strong in performing_paragraph.find_all('strong', recursive=False):
        # Convert the strong tag content to string, keeping br tags
        content = str(strong)
        
        # Split by any form of br tag (<br>, </br>, <br/>, etc.)
        parts = re.split(r'</?br\s*/?>', content)
        
        # Clean up each part
        for part in parts:
            # Remove strong tags and strip whitespace
            clean_part = (part.replace('<strong>', '')
                            .replace('</strong>', '')
                            .strip())
            if clean_part:  # Only add non-empty strings
                performers.append(clean_part)
    
    return performers

def extract_date(soup):
    """
    Extract text content between h1.entry-title and the next a tag.
    
    Args:
        soup (BeautifulSoup): Parsed HTML content
        
    Returns:
        str: Text content between h1 and next a tag, or empty string if not found
    """
    # Find the h1 tag with class entry-title
    h1_tag = soup.find('h1', class_='entry-title')
    if not h1_tag:
        return ''
    
    description = []
    current = h1_tag.next_sibling
    
    # Collect text until we hit an a tag or run out of elements
    while current and current.name != 'a':
        if isinstance(current, str):
            description.append(current.strip())
        elif current.name:
            description.append(current.get_text(strip=True))
        current = current.next_sibling
            
    return ' '.join(filter(None, description))

def extract_composers(soup):
    """
    Extract composer names from HTML content by finding the paragraph after 'Skladá' heading
    and parsing strong tags within it. Returns a list of composer names.
    """

    h_program = soup.find(['h1', 'h2', 'h3'], string='Program')
    
    if not h_program:
        return []
    sibling = h_program.next_sibling

    composers = []
    while True:
        if sibling is None:
            break
        text = sibling.get_text(strip=True)
        if not text:
            sibling = sibling.next_sibling
            continue
        elif repr(sibling).startswith('<p><strong>'):
            strong_text = sibling.find('strong').get_text(strip=True)
            composers.append(strong_text)
        else:
            break
        sibling = sibling.next_sibling
    return composers
