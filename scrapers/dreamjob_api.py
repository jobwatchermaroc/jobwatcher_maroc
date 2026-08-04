"""Scraper Dreamjob.ma via l'API REST WordPress (temps réel, JSON).

Endpoint : /wp-json/wp/v2/posts — renvoie les offres avec horodatage exact.
Beaucoup plus rapide et fiable que le scraping HTML : 1 requête, zéro parsing.

Filtres appliqués :
  - only on garde les permaliens /emploi/ (on ignore concours/resultats/alwadifa)
  - paramètre `after` : ne remonte que les offres publiées depuis N jours
"""

import logging
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from .base import clean_text, make_job_id, make_session

LOG = logging.getLogger(__name__)

API_URL = "https://www.dreamjob.ma/wp-json/wp/v2/posts"
FIELDS = "id,date,link,title,excerpt"


def _excerpt_html(value) -> str:
    if not value:
        return ""
    rendered = value.get("rendered") if isinstance(value, dict) else value
    try:
        return clean_text(BeautifulSoup(rendered, "lxml").get_text(" ", strip=True))
    except Exception:  # noqa: BLE001
        return clean_text(rendered)


def _parse_post(item: dict) -> dict | None:
    link = clean_text(item.get("link", ""))
    if not link or "/emploi/" not in link.lower():
        return None
    title = clean_text((item.get("title") or {}).get("rendered", ""))
    if not title or len(title) < 3:
        return None
    return {
        "title": title,
        "company": "",
        "url": link,
        "date": clean_text(item.get("date", "")),
        "location": "",
        "description": _excerpt_html(item.get("excerpt")),
        "source": "Dreamjob API",
        "id": make_job_id(url=link, title=title),
    }


def scrape(source_cfg: dict, keywords: list, http_cfg: dict) -> list:
    session = make_session(http_cfg)
    timeout = float(http_cfg.get("timeout_seconds", 20))

    since_days = int(source_cfg.get("since_days", 3))
    after = (datetime.utcnow() - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%S")

    jobs, seen_links = [], set()
    for page in range(1, int(source_cfg.get("max_pages", 2)) + 1):
        params = {
            "per_page": int(source_cfg.get("per_page", 50)),
            "page": page,
            "_fields": FIELDS,
            "after": after,
            "orderby": "date",
            "order": "desc",
        }
        try:
            resp = session.get(API_URL, params=params, timeout=timeout)
            resp.raise_for_status()
            items = resp.json()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("dreamjob_api : requête échouée (page %d) : %s", page, exc)
            break

        if not isinstance(items, list) or not items:
            break
        for item in items:
            job = _parse_post(item)
            if not job:
                continue
            if job["url"] in seen_links:
                continue
            seen_links.add(job["url"])
            jobs.append(job)
        if len(items) < int(source_cfg.get("per_page", 50)):
            break  # dernière page atteinte (évite un 400 inutile)

    LOG.info("dreamjob_api : %d offres collectées (depuis %d jours)", len(jobs), since_days)
    return jobs
