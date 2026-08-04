"""Parsing et filtrage des dates de publication des offres.

Formats gérés :
  - DD/MM/YYYY ou DD-MM-YYYY (Rekrute, Dreamjob)
  - AAAA-MM-JJ (ISO)
  - relatif français : "il y a 3 jours", "publié il y a 2 semaines", "aujourd'hui"
  - relatif anglais  : "Posted 2 days ago", "Today", "Yesterday"
  - "9 juin 2026" (mois en lettres, français/anglais, accents tolérés)
"""

import re
import unicodedata
from datetime import date, timedelta

MONTHS = {
    "janvier": 1, "january": 1,
    "fevrier": 2, "february": 2,
    "mars": 3, "march": 3,
    "avril": 4, "april": 4,
    "mai": 5, "may": 5,
    "juin": 6, "june": 6,
    "juillet": 7, "july": 7,
    "aout": 8, "august": 8,
    "septembre": 9, "september": 9,
    "octobre": 10, "october": 10,
    "novembre": 11, "november": 11,
    "decembre": 12, "december": 12,
}


def _no_accent(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(c) != "Mn"
    )


def _delta(n: int, unit: str) -> timedelta:
    if unit.startswith("minute"):
        return timedelta(minutes=n)
    if unit.startswith("heure") or unit.startswith("hour"):
        return timedelta(hours=n)
    if unit.startswith("semaine") or unit.startswith("week"):
        return timedelta(weeks=n)
    if unit.startswith("mois") or unit.startswith("month"):
        return timedelta(days=30 * n)
    return timedelta(days=n)


def parse_date(raw) -> date | None:
    """Renvoie la date de publication, ou None si elle n'est pas interprétable."""
    if not raw:
        return None
    s = str(raw).strip()
    low = _no_accent(s)
    today = date.today()

    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if 1 <= d <= 31 and 1 <= mo <= 12 and 1990 <= y <= today.year + 1:
            return date(y, mo, d)

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = re.search(r"il\s+y['\u2019]?\s*a\s+(\d+)\s+(jour|semaine|mois|heure|minute)", low)
    if m:
        return today - _delta(int(m.group(1)), m.group(2))

    m = re.search(r"(\d+)\s+(days?|weeks?|months?|hours?|minutes?)\s+ago", low)
    if m:
        return today - _delta(int(m.group(1)), m.group(2))

    if any(w in low for w in ("aujourd", "today", "just now")):
        return today
    if any(w in low for w in ("hier", "yesterday")):
        return today - timedelta(days=1)

    m = re.search(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", low)
    if m and m.group(2) in MONTHS:
        return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))

    return None


def is_fresh(raw_date, min_date: str | None) -> bool:
    """True si la date est >= min_date (AAAA-MM-JJ).

    Offre sans date interprétable => conservée (on ne filtre pas
    ce qu'on ne sait pas dater). min_date vide => aucune restriction.
    """
    if not min_date:
        return True
    try:
        minimum = date.fromisoformat(str(min_date).strip())
    except ValueError:
        return True
    parsed = parse_date(raw_date)
    if parsed is None:
        return True
    return parsed >= minimum
