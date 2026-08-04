"""Helpers partagés par tous les scrapers : HTTP, délais, retries, rendu JS, extraction."""

import hashlib
import logging
import random
import re
import time

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def make_session(http_cfg: dict) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": http_cfg.get("user_agent", DEFAULT_UA),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    return session


def random_delay(http_cfg: dict) -> None:
    low = float(http_cfg.get("min_delay_seconds", 2))
    high = float(http_cfg.get("max_delay_seconds", 6))
    if high < low:
        low, high = high, low
    time.sleep(random.uniform(low, high))


def fetch_html(session: requests.Session, url: str, http_cfg: dict, render: bool = False) -> str:
    """Récupère le HTML d'une page, avec retries et fallback Playwright."""
    timeout = float(http_cfg.get("timeout_seconds", 20))
    retries = int(http_cfg.get("max_retries", 2))
    if render:
        return render_html(url, timeout)
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            if "captcha" in resp.url.lower() or "temporary" in resp.url.lower():
                LOG.warning("Possible page de blocage/captcha : %s", resp.url)
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            LOG.warning(
                "Requête échouée (essai %d/%d) pour %s : %s",
                attempt + 1,
                retries + 1,
                url,
                exc,
            )
            time.sleep((attempt + 1) * 2)
    raise last_exc


def render_html(url: str, timeout: float) -> str:
    """Rendu JS via Playwright (headless Chromium). Import paresseux."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=DEFAULT_UA)
            page.goto(url, timeout=int(timeout * 1000), wait_until="domcontentloaded")
            return page.content()
        finally:
            browser.close()


def fetch_detail_text(session: requests.Session, url: str, http_cfg: dict, render: bool = False, max_chars: int = 1200) -> str:
    """Extrait un extrait texte de la page détail d'une offre (pour le filtrage)."""
    try:
        html = fetch_html(session, url, http_cfg, render=render)
        soup = BeautifulSoup(html, "lxml")
        container = (
            soup.find("main")
            or soup.find("article")
            or soup.select_one("div[class*=description], div[class*=offre], div[class*=job]")
            or soup.body
        )
        text = container.get_text(" ", strip=True) if container else ""
        return clean_text(text)[:max_chars]
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Impossible de récupérer le détail %s : %s", url, exc)
        return ""


def make_job_id(url: str = "", title: str = "", company: str = "") -> str:
    """Identifiant stable : hash de l'URL (ou titre+entreprise si pas d'URL)."""
    key = (url or "").split("#")[0] if url else ""
    if not key:
        key = f"{title} | {company}".strip().lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def clean_text(value) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def card_text(card, selectors):
    """Retourne le texte du premier élément correspondant à l'un des sélecteurs."""
    for sel in selectors:
        el = card.select_one(sel)
        if el and clean_text(el.get_text(" ", strip=True)):
            return clean_text(el.get_text(" ", strip=True))
    return ""


def link_abs(base: str, href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base + href
    return base + "/" + href
