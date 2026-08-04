"""Scraper Indeed Maroc (ma.indeed.com)."""

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

CARD_SELECTORS = "div.job_seen_beacon, div.result, div.card"
BLOCK_MARKERS = ("captcha", "unusual traffic", "are you a robot")


def _parse_card(card, base: str, seen_links: set, fetch_details: bool, session, http_cfg, render: bool):
    a = card.find("a", href=True, class_="jcs-JobTitle") or card.find("a", href=True)
    if not a:
        return None
    full = link_abs(base, a["href"])
    if full in seen_links:
        return None
    title = clean_text(a.get_text(" ", strip=True))
    if not title or len(title) < 3:
        return None
    seen_links.add(full)

    company = card_text(card, ["span[data-testid=company-name]", ".companyName", "[class*=company]"])
    location = card_text(card, ["[data-testid=job-location]", ".location", "[class*=location]"])
    date = card_text(card, ["[data-testid=job-date]", "[data-testid=jobDate]", ".date", "time"]) or clean_text(
        card.find("time").get("datetime") if card.find("time") else ""
    )
    description = card_text(card, ["[data-testid=job-snippet]", ".summary", ".snippet"])

    job = {
        "title": title,
        "company": company,
        "url": full,
        "date": date,
        "location": location,
        "description": description,
        "source": "Indeed",
        "id": make_job_id(url=full, title=title, company=company),
    }
    if fetch_details:
        job["description"] = job["description"] or fetch_detail_text(session, full, http_cfg, render=render)
    return job


def scrape(source_cfg: dict, keywords: list, http_cfg: dict) -> list:
    base = source_cfg.get("base_url", "https://ma.indeed.com").rstrip("/")
    template = source_cfg.get("search_url", "{base}/jobs?q={keyword}&limit=50")
    render = source_cfg.get("render", False)
    fetch_details = source_cfg.get("fetch_details", False)

    session = make_session(http_cfg)
    jobs, seen_links = [], set()

    for keyword in keywords:
        url = template.format(base=base, keyword=quote(keyword))
        try:
            html = fetch_html(session, url, http_cfg, render=render)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("indeed : requête échouée pour %r : %s", keyword, exc)
            continue

        if not html or any(m in html.lower() for m in BLOCK_MARKERS):
            LOG.warning("indeed : page bloquée / captcha pour %r — on passe à la suite", keyword)
            continue

        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(CARD_SELECTORS)
        for card in cards:
            job = _parse_card(card, base, seen_links, fetch_details, session, http_cfg, render)
            if job:
                jobs.append(job)

        random_delay(http_cfg)

    LOG.info("indeed : %d offres collectées", len(jobs))
    return jobs
