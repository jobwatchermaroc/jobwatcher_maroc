"""Scraper Dreamjob.ma (recherche WordPress : /?s=<mot-clé>).

Structure actuelle des résultats (vérifiée en 2026) :
  <article class="jeg_post">
    <h3 class="jeg_post_title"><a href="/emploi/<slug>/">Titre</a></h3>
    <div class="jeg_meta_date"><a>12/09/2024</a></div>
    <div class="jeg_post_excerpt"><p>description...</p></div>
"""

import logging
from urllib.parse import quote

from bs4 import BeautifulSoup

from .base import (
    card_text,
    clean_text,
    fetch_detail_text,
    fetch_html,
    link_abs,
    make_job_id,
    make_session,
    random_delay,
)

LOG = logging.getLogger(__name__)

CARD_SELECTORS = "article.jeg_post, article.post, article.offer, article.offre, li.offer, li.offre"


def _is_job_link(href: str, patterns: list) -> bool:
    href_l = href.lower()
    return any(p in href_l for p in patterns)


def _parse_card(card, base: str, seen_links: set, fetch_details: bool, session, http_cfg, render: bool):
    a = card.select_one("h3.jeg_post_title a") or card.select_one("h2 a[href]") or card.find("a", href=True)
    if not a:
        return None
    full = link_abs(base, a["href"])
    if full in seen_links:
        return None
    title = clean_text(a.get_text(" ", strip=True))
    if not title or len(title) < 3:
        return None
    seen_links.add(full)

    job = {
        "title": title,
        "company": card_text(card, [".jeg_meta_company", ".company", ".entreprise", "[class*=company]"]),
        "url": full,
        "date": card_text(card, [".jeg_meta_date a", ".jeg_meta_date", ".date", "time"]),
        "location": card_text(card, [".jeg_meta_location", ".location", ".ville"]),
        "description": card_text(card, [".jeg_post_excerpt p", ".jeg_post_excerpt", "[class*=excerpt]"]),
        "source": "Dreamjob",
        "id": make_job_id(url=full, title=title),
    }
    if fetch_details:
        job["description"] = job["description"] or fetch_detail_text(session, full, http_cfg, render=render)
    return job


def _generic_parse(a, base: str, patterns: list, seen_links: set):
    if not _is_job_link(a["href"], patterns):
        return None
    full = link_abs(base, a["href"])
    if full in seen_links:
        return None
    title = clean_text(a.get_text(" ", strip=True))
    if not title or len(title) < 3:
        return None
    seen_links.add(full)
    return {
        "title": title,
        "company": "",
        "url": full,
        "date": "",
        "location": "",
        "description": "",
        "source": "Dreamjob",
        "id": make_job_id(url=full, title=title),
    }


def scrape(source_cfg: dict, keywords: list, http_cfg: dict) -> list:
    base = source_cfg.get("base_url", "https://www.dreamjob.ma").rstrip("/")
    template = source_cfg.get("search_url", "{base}/?s={keyword}")
    patterns = source_cfg.get("job_link_patterns", ["/emploi/", "/emploi-public/"])
    render = source_cfg.get("render", False)
    fetch_details = source_cfg.get("fetch_details", False)

    session = make_session(http_cfg)
    jobs, seen_links = [], set()

    for keyword in keywords:
        url = template.format(base=base, keyword=quote(keyword))
        try:
            html = fetch_html(session, url, http_cfg, render=render)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("dreamjob : requête échouée pour %r : %s", keyword, exc)
            continue

        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(CARD_SELECTORS)
        if cards:
            for card in cards:
                job = _parse_card(card, base, seen_links, fetch_details, session, http_cfg, render)
                if job:
                    jobs.append(job)
        else:
            # Secours : structure HTML différente (site modifié)
            for a in soup.find_all("a", href=True):
                job = _generic_parse(a, base, patterns, seen_links)
                if job:
                    jobs.append(job)

        random_delay(http_cfg)

    LOG.info("dreamjob : %d offres collectées", len(jobs))
    return jobs
