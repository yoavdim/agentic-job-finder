#!/usr/bin/env python3
"""Posting-date normalization for job listings.

Listings state their age in wildly different ways ("3 days ago", "Posted July 20, 2026",
"reposted last week", "2026-07"). The shortlist stores one form only: `📅 posted YYYY-MM-DD`
in the Notes column, because the liveness sweep's age cut parses that field.

Relative ages are resolved against an explicit `today` so results are reproducible in tests
and in a run that spans midnight.
"""
import re
from datetime import datetime, timedelta

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

ISO_FULL_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
ISO_MONTH_RE = re.compile(r"\b(\d{4})-(\d{2})\b")
US_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
MONTH_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s*(\d{4}))?\b", re.I)
RELATIVE_RE = re.compile(
    r"\b(\d+)\+?\s*(minute|min|hour|hr|day|week|month)s?\s+ago\b", re.I)
WORD_RELATIVE = {
    "today": 0, "just posted": 0, "just now": 0, "yesterday": 1,
    "last week": 7, "a week ago": 7, "this week": 3, "last month": 30,
    "a month ago": 30,
}

# "posted 30+ days ago" style ceilings — treat as at least that old.
PLUS_DAYS_RE = re.compile(r"\b(\d+)\+\s*days?\s+ago\b", re.I)


def _today(today):
    if isinstance(today, datetime):
        return today
    if today:
        return datetime.strptime(today, "%Y-%m-%d")
    return datetime.now()


def _iso(d):
    return d.strftime("%Y-%m-%d")


def parse_posting_date(text, today=None):
    """Best-effort absolute date for a listing's stated age.

    Returns an ISO date string, or "" when the text states no date. Never guesses:
    an unparseable string yields "" so the caller can leave the row undated (per prefs,
    undated rows are not penalized).
    """
    if not text:
        return ""
    s = str(text).strip()
    base = _today(today)
    low = s.lower()

    m = ISO_FULL_RE.search(s)
    if m:
        return m.group(0)

    m = PLUS_DAYS_RE.search(low)
    if m:
        return _iso(base - timedelta(days=int(m.group(1))))

    m = RELATIVE_RE.search(low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"minute": 0, "min": 0, "hour": 0, "hr": 0,
                "day": 1, "week": 7, "month": 30}[unit] * n
        return _iso(base - timedelta(days=days))

    for phrase, days in WORD_RELATIVE.items():
        if phrase in low:
            return _iso(base - timedelta(days=days))

    m = MONTH_NAME_RE.search(s)
    if m:
        mon = MONTHS[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else base.year
        try:
            d = datetime(year, mon, day)
        except ValueError:
            return ""
        # A month-name date with no year that lands in the future belongs to last year.
        if not m.group(3) and d > base:
            d = datetime(year - 1, mon, day)
        return _iso(d)

    m = US_SLASH_RE.search(s)
    if m:
        mo, day, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yr < 100:
            yr += 2000
        try:
            return _iso(datetime(yr, mo, day))
        except ValueError:
            return ""

    m = ISO_MONTH_RE.search(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"

    return ""


POSTED_NOTE_RE = re.compile(r"📅\s*(?:re)?posted\s*\S+", re.I)


def posted_note(iso_date):
    return f"📅 posted {iso_date}" if iso_date else ""


def add_posted_note(notes, iso_date):
    """Append `📅 posted <date>` to a Notes cell, replacing any existing posted note."""
    note = posted_note(iso_date)
    if not note:
        return notes or ""
    existing = (notes or "").strip()
    if POSTED_NOTE_RE.search(existing):
        return POSTED_NOTE_RE.sub(note, existing, count=1)
    return f"{existing} · {note}" if existing else note


def age_days(iso_date, today=None):
    """Days between `iso_date` and today, or None if unparseable."""
    if not iso_date:
        return None
    try:
        return (_today(today) - datetime.strptime(iso_date, "%Y-%m-%d")).days
    except ValueError:
        return None
