def format_date(date):
    dd, mm, yy = date.split('.')
    dd, mm, yy = int(dd.strip()), int(mm.strip()), int(yy.strip())
    return f'{yy}-{mm:02d}-{dd:02d}'