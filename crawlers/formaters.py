def format_date(date):
    dd, mm, yy = date.split('.')
    dd, mm, yy = int(dd.strip()), int(mm.strip()), int(yy.strip())
    return f'{yy}-{mm:02d}-{dd:02d}'

def clean_string(text):
    text = text.replace('\u200b', '')
    text = text.replace('\xa0', ' ').strip()
    return text