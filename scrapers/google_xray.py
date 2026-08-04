"""X-Ray Google sur les ATS via l'API Custom Search (pas de scraping direct de Google).

Secrets requis dans l'environnement :
  GOOGLE_API_KEY  -> clé API Google Cloud (Custom Search API)
  GOOGLE_CSE_ID   -> identifiant du moteur de recherche programmable (cx)
"""

import logging
import os
import re

from .base import clean_text, make_job_id, make_session, random_delay

LOG = logging.getLogger(__name__)

API_ENDPOINT = "https://www.googleapis.com/customsearch/v1"

# Extraire une mention de date depuis le snippet Google (souvent le format
# "il y a 3 jours" / "Posted 2 days ago" / une date explicite).
DATE_IN_SNIPPET = re.compile(
    r"(?i)"
    r"(?:publié\s+)?il\s+y['\u2019]?\s*a\s+\d+\s+(?:jour|semaine|mois|heure|minute)"
    r"|(?:posted\s+)?\d+\s+(?:day|week|month|hour|minute)s?\s+ago"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{4}"
    r"|aujourd'hui|hier|today|yesterday"
)


def _extract_date(snippet: str) -> str:
    if not snippet:
        return ""
    m = DATE_IN_SNIPPET.search(snippet)
    return m.group(0) if m else ""


def _quote_keyword(kw: str) -> str:
    kw = kw.strip()
    return f'"{kw}"' if " " in kw else kw


def _build_query(source_cfg: dict, keywords: list) -> str:
    sites = [s.strip() for s in source_cfg.get("ats_sites", []) if s.strip()]
    site_q = " OR ".join(f"site:{s}" for s in sites)
    kw_q = " OR ".join(_quote_keyword(k) for k in keywords if k.strip())
    extra = [t.strip() for t in source_cfg.get("extra_terms", []) if t.strip()]
    query = f"({kw_q}) ({site_q})"
    if extra:
        query += " " + " OR ".join(_quote_keyword(t) for t in extra)
    return query


def scrape(source_cfg: dict, keywords: list, http_cfg: dict) -> list:
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    cse_id = os.environ.get("GOOGLE_CSE_ID", "").strip()
    if not api_key or not cse_id:
        LOG.warning("google_xray désactivé : définir GOOGLE_API_KEY et GOOGLE_CSE_ID")
        return []

    query = _build_query(source_cfg, keywords)
    session = make_session(http_cfg)
    timeout = float(http_cfg.get("timeout_seconds", 20))
    max_results = min(int(source_cfg.get("max_results", 10)), 100)

    jobs = []
    for start in range(1, max_results + 1, 10):
        params = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": min(10, max_results - start + 1),
            "start": start,
        }
        if source_cfg.get("sort_by_date", True):
            params["sort"] = "date"
        date_restrict = str(source_cfg.get("date_restrict", "") or "").strip()
        if date_restrict:
            params["dateRestrict"] = date_restrict
        try:
            resp = session.get(API_ENDPOINT, params=params, timeout=timeout)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("google_xray : requête échouée : %s", exc)
            break

        if "error" in data:
            LOG.error("google_xray : erreur API : %s", data["error"].get("message"))
            break

        items = data.get("items", [])
        for item in items:
            link = item.get("link", "")
            title = clean_text(item.get("title", ""))
            snippet = clean_text(item.get("snippet", ""))
            jobs.append(
                {
                    "title": title,
                    "company": "",
                    "url": link,
                    "date": _extract_date(item.get("snippet", "")),
                    "location": "",
                    "description": snippet,
                    "source": "Google X-Ray (ATS)",
                    "id": make_job_id(url=link, title=title),
                }
            )

        if len(items) < 10:
            break
        random_delay(http_cfg)

    LOG.info("google_xray : %d offres collectées", len(jobs))
    return jobs
