"""Prospection : emails publics des entreprises qui recrutent (pages Contact/Carrières).

Pour chaque offre pertinente, on identifie le domaine de l'entreprise (indice depuis
l'URL de l'offre, sinon candidats {slug}.ma/.com/.fr vérifiés) puis on crawl ses
pages contact/carrières pour extraire les emails publiés (mailto: + texte).

Seules les informations PUBLIQUES sont utilisées (pas de LinkedIn, pas d'inférence).
Résultat caché dans seen_jobs.json ("contacts") et rafraîchi chaque refresh_days.
"""

import logging
import random
import re
import time
import urllib.parse
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from scrapers.base import clean_text, make_session
from state import utcnow

LOG = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

DEFAULT_SCAN_PATHS = [
    "",
    "/contact",
    "/contactez-nous",
    "/contacts",
    "/contact-us",
    "/nous-contacter",
    "/carrieres",
    "/careers",
    "/a-propos",
    "/about",
    "/equipe",
    "/team",
    "/recrutement",
    "/rh",
]

DEFAULT_EXCLUDED = [
    "example.com",
    "domain.com",
    "yourname@",
    "support@",
    "no-reply@",
    "noreply@",
    "unsubscribe@",
    "sentry.io",
    "wixpress.com",
    "sentry-next",
]


def slugify(company: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (company or "").lower())


def is_junk(email: str, excluded: list) -> bool:
    e = email.lower()
    if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".ico")):
        return True
    if re.search(r"([_.-](2x|1x)@|@sentry\.)", e):
        return True
    for pat in excluded:
        if pat.lower() in e:
            return True
    return False


def extract_emails(text: str, excluded: list) -> set:
    found = set()
    for e in EMAIL_RE.findall(str(text or "")):
        e = e.rstrip(".").lower()
        if not is_junk(e, excluded):
            found.add(e)
    return found


def _page_emails(session, url: str, excluded: list, timeout: float) -> tuple[set, bool]:
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        LOG.debug("contacts: page %s inaccessible (%s)", url, exc)
        return set(), False
    soup = BeautifulSoup(resp.text, "lxml")
    emails = extract_emails(soup.get_text(" ", strip=True), excluded)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            addr = href.split(":", 1)[1].split("?")[0].strip().lower()
            if addr and not is_junk(addr, excluded):
                emails.add(addr)
    return emails, True


def domain_hint_from_url(company: str, url: str) -> str | None:
    """Si l'URL de l'offre contient le nom de l'entreprise, on déduit le domaine."""
    host = urllib.parse.urlparse(url or "").netloc.lower()
    slug = slugify(company)
    if not slug or not host or host in ("", "localhost"):
        return None
    if slug in host:
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    return None


def candidate_domains(company: str, cfg: dict) -> list:
    slug = slugify(company)
    first = slugify((company or "").split()[0]) if (company or "").split() else slug
    tlds = cfg.get("tlds", [".ma", ".com", ".fr", ".net"])
    cands = []
    for w in dict.fromkeys([slug, first]):
        if not w:
            continue
        for tld in tlds:
            cands.append(w + tld)
    return cands


def _verify_domain(session, company: str, domain: str, timeout: float) -> bool:
    """Le site est-il bien celui de l'entreprise ? (titre/meta/contenu contient son nom).

    Tente bare + www en https/http. Si le nom d'entreprise mentionne Maroc/Morocco,
    on exige aussi une mention locale (évite de prendre le site groupe étranger).
    Pour un sigle court (tokens < 4 lettres, ex. OCP, AXA), on exige le sigle dans le
    titre/og:site_name uniquement (plus strict, évite de tomber sur un homonyme).
    """
    tokens = [t for t in re.findall(r"[a-z0-9]{3,}", (company or "").lower())
              if t not in ("the", "maroc", "technologies", "solutions")]
    if not tokens:
        tokens = [slugify(company)]
    low = (company or "").lower()
    if any(w in low for w in ("maroc", "morocco")):
        tokens += ["maroc", "morocco", "rabat", "casablanca", "tanger", "casa"]
    strict_title_only = not any(len(t) >= 4 for t in tokens)

    for host in {domain, "www." + domain}:
        for scheme in ("https://", "http://"):
            try:
                resp = session.get(scheme + host + "/", timeout=timeout, allow_redirects=True)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                title = (soup.title.string if soup.title else "") or ""
                og = (soup.find("meta", attrs={"property": "og:site_name"}) or {}).get("content") or ""
                head = title + " " + og
                hay = (head + " " + resp.text[:6000]).lower()
                if strict_title_only:
                    if any(t in head.lower() for t in tokens):
                        return True
                elif any(t in hay for t in tokens):
                    return True
            except requests.RequestException:
                continue
    return False


def resolve_domain(session, company: str, url: str, cfg: dict, timeout: float) -> str | None:
    hint = domain_hint_from_url(company, url)
    if hint:
        # L'URL de l'offre est le meilleur signal : si le domaine ne se vérifie pas,
        # on ne devine PAS de remplaçant (risque de tomber sur un homonyme).
        return hint if _verify_domain(session, company, hint, timeout) else None
    for domain in candidate_domains(company, cfg):
        if _verify_domain(session, company, domain, timeout):
            return domain
    return None


def crawl_emails(session, domain: str, cfg: dict, timeout: float) -> set:
    emails: set = set()
    paths = cfg.get("scan_paths", DEFAULT_SCAN_PATHS)
    max_pages = int(cfg.get("max_pages", 10))
    excluded = cfg.get("excluded_emails", DEFAULT_EXCLUDED)
    hosts = [domain, "www." + domain]
    working_host = None
    for i, path in enumerate(paths):
        if i >= max_pages:
            break
        suffix = path if path.startswith("/") else "/" + path
        for host in (working_host or hosts):
            page_emails, ok = _page_emails(session, f"https://{host}{suffix}", excluded, timeout)
            if ok:
                working_host = host
                if page_emails:
                    LOG.info("contacts: %d email(s) sur %s", len(page_emails), f"https://{host}{suffix}")
                emails |= page_emails
                break
        if i < len(paths) - 1:
            _delay(cfg)
    return emails


def _delay(cfg: dict) -> None:
    d = cfg.get("delay_seconds") or {}
    low = float(d.get("min", 0.5))
    high = float(d.get("max", 1.5))
    time.sleep(random.uniform(low, high))


def get_contacts(state: dict, job: dict, cfg: dict, session=None) -> dict | None:
    """Emails publics d'une entreprise (cache hebdo). Renvoie {'emails': [...], 'scraped': bool}.

    session injectable pour les tests.
    """
    company = (job.get("company") or "").strip()
    if not company:
        return None
    key = company.lower()
    cache = state.setdefault("contacts", {})
    entry = cache.get(key)

    refresh = timedelta(days=int(cfg.get("refresh_days", 7)))
    if entry and entry.get("updated_at"):
        try:
            if utcnow() - datetime.fromisoformat(entry["updated_at"]) < refresh:
                return {"emails": entry.get("emails", []), "scraped": False}
        except ValueError:
            pass

    if session is None:
        session = make_session({})

    timeout = float(cfg.get("timeout_seconds", 10))
    domain = (entry or {}).get("domain") or resolve_domain(session, company, job.get("url", ""), cfg, timeout)

    emails = []
    if domain:
        emails = sorted(crawl_emails(session, domain, cfg, timeout))

    entry = {
        "domain": domain or "",
        "emails": emails,
        "updated_at": utcnow().isoformat(timespec="seconds"),
    }
    cache[key] = entry
    return {"emails": emails, "scraped": True}
