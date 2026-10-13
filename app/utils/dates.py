from datetime import datetime


def calendar_week(value):
    """Week number within the value's calendar year.

    ISO rollover days at the start/end of a calendar year are folded into that
    calendar year's first/last week so a December date never appears as week 1.
    """
    if not value:
        return None
    iso_year, iso_week, _ = value.isocalendar()
    if iso_year < value.year:
        return 1
    if iso_year > value.year:
        return datetime(value.year, 12, 28).isocalendar().week
    return int(iso_week)