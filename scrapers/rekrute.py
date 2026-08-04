"""Scraper Rekrute.com (recherche par mot-clé).

Structure actuelle des résultats (vérifiée en 2026) :
  <li class="post-id">
    <a class="titreJob" href="/offre-emploi-...">Titre | Ville</a>
    <img class="photo" title="Entreprise">
    <em class="date"><span>du</span> <span>09/06/2026</span> ...</em>
    <div class="info"><span>description...</span></div>
"""

import logging
import re
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

COMPANY_LINK_RE = re.compile(r"emploi-recrutement-")
OFFER_ID_RE = re.compile(r"(\d{4,})(?:\.html|$)")


def _offer_id(url: str) -> str:
    m = OFFER_ID_RE.search(url or "")
    return m.group(1) if m else ""


def _is_job_link(href: str, patterns: list) -> bool:
    href_l = href.lower()
    return any(p in href_l for p in patterns)


def _split_title(title: str):
    """Rekrute ajoute la ville au titre : "Poste | Casablanca (Maroc)"."""
    title, loc = title, ""
    if "|" in title:
        parts = [p.strip() for p in title.split("|")]
        title = parts[0]
        loc = " | ".join(parts[1:])
    return title, loc


def _parse_card(card, base: str, seen_links: set, fetch_details: bool, session, http_cfg, render: bool):
    a = card.select_one("a.titreJob") or card.select_one("h2 a[href]") or card.find("a", href=True)
    if not a:
        return None
    full = link_abs(base, a["href"])
    if full in seen_links:
        return None
    raw_title = clean_text(a.get_text(" ", strip=True))
    if not raw_title or len(raw_title) < 5:
        return None
    seen_links.add(full)

    title, location = _split_title(raw_title)
    company = ""
    img = card.select_one("img.photo")
    if img and (img.get("title") or img.get("alt")):
        company = clean_text(img.get("title") or img.get("alt"))
    if not company:
        company_a = card.find("a", href=COMPANY_LINK_RE)
        if company_a:
            company = clean_text(company_a.get_text(" ", strip=True))

    date_el = card.select_one("em.date span")
    date = clean_text(date_el.get_text(" ", strip=True)) if date_el else card_text(card, ["em.date", "[class*=date]"])

    description_el = card.select_one("div.info span")
    description = clean_text(description_el.get_text(" ", strip=True)) if description_el else ""

    job = {
        "title": title,
        "company": company,
        "url": full,
        "date": date,
        "location": location,
        "description": description,
        "source": "Rekrute",
        "offer_id": _offer_id(full),
        "id": make_job_id(url=full, title=title, company=company),
    }
    if fetch_details and not description:
        job["description"] = fetch_detail_text(session, full, http_cfg, render=render)
    return job


def _generic_parse(a, base: str, seen_links: set):
    full = link_abs(base, a["href"])
    if full in seen_links:
        return None
    title = clean_text(a.get_text(" ", strip=True))
    if not title or len(title) < 5:
        return None
    seen_links.add(full)
    title, location = _split_title(title)
    return {
        "title": title,
        "company": "",
        "url": full,
        "date": "",
        "location": location,
        "description": "",
        "source": "Rekrute",
        "id": make_job_id(url=full, title=title),
    }


def scrape(source_cfg: dict, keywords: list, http_cfg: dict) -> list:
    base = source_cfg.get("base_url", "https://www.rekrute.com").rstrip("/")
    template = source_cfg.get("search_url", "{base}/offres.html?keyword={keyword}&keywordNew=1")
    patterns = source_cfg.get("job_link_patterns", ["/offre-emploi-", "/offre-emploi", "/emploi-et-offres"])
    render = source_cfg.get("render", False)
    fetch_details = source_cfg.get("fetch_details", False)

    session = make_session(http_cfg)
    jobs, seen_links = [], set()

    for keyword in keywords:
        url = template.format(base=base, keyword=quote(keyword))
        try:
            html = fetch_html(session, url, http_cfg, render=render)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("rekrute : requête échouée pour %r : %s", keyword, exc)
            continue

        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("li.post-id")
        if cards:
            for card in cards:
                job = _parse_card(card, base, seen_links, fetch_details, session, http_cfg, render)
                if job:
                    jobs.append(job)
        else:
            # Secours : structure HTML différente (site modifié)
            for a in soup.find_all("a", href=True):
                if not _is_job_link(a["href"], patterns):
                    continue
                job = _generic_parse(a, base, seen_links)
                if job:
                    jobs.append(job)

        random_delay(http_cfg)

    LOG.info("rekrute : %d offres collectées", len(jobs))
    return jobs
