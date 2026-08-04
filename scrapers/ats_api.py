"""Scraping des API JSON publiques des ATS : Greenhouse, Lever, SmartRecruiters.

Aucune clé n'est nécessaire : ce sont des endpoints publics qui alimentent
les fiches de poste (boards). Chaque board listé dans config.yaml est
interrogé ; les offres sont triées par date (les plus récentes d'abord) et
plafonnées à `max_jobs_per_company`.

Documentation :
  - Greenhouse       : https://developers.greenhouse.io/job-board.html
  - Lever (postings) : https://github.com/lever/postings-api
  - SmartRecruiters  : https://dev.smartrecruiters.com/customer-api/

Cette source apporte notamment des offres REMOTE / ONSITE à l'international
(France comprise) et des stages PFE ("Intern", "Stage", "Alternance").
"""

import datetime as dt
import logging
import re

from bs4 import BeautifulSoup

from .base import clean_text, make_job_id, make_session, random_delay

LOG = logging.getLogger(__name__)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{board}?mode=json"
SMARTRECRUITERS_URL = "https://api.smartrecruiters.com/v1/companies/{board}/postings?limit=100"
WORKABLE_URL = "https://apply.workable.com/api/v1/widget/accounts/{board}"


def _iso_date(value) -> str:
    """'2026-07-24T03:46:55-04:00' -> '2026-07-24' (format accepté par dates.py)."""
    if not value:
        return ""
    m = re.search(r"\d{4}-\d{1,2}-\d{1,2}", str(value))
    return m.group(0) if m else ""


def _salary_str(comp: dict) -> str:
    """'{"min": 45000, "max": 55000, "currency": "USD"}' -> '45000-55000 USD'."""
    if not isinstance(comp, dict):
        return ""
    try:
        low = comp.get("min") or ""
        high = comp.get("max") or ""
        currency = str(comp.get("currency") or "").upper()
        parts = [p for p in (str(low) if low else "", str(high) if high else "") if p]
        if not parts:
            return ""
        return "-".join(parts) + (f" {currency}" if currency else "")
    except (TypeError, ValueError):
        return ""


def _strip_html(value) -> str:
    if not value:
        return ""
    try:
        return clean_text(BeautifulSoup(value, "lxml").get_text(" ", strip=True))
    except Exception:  # noqa: BLE001
        return clean_text(value)


def _sort_newest(jobs: list, date_key: str, limit: int) -> list:
    jobs.sort(key=lambda j: j.get(date_key) or "", reverse=True)
    return jobs[:limit]


def _greenhouse(board: str, session, timeout: float, max_jobs: int) -> list:
    url = GREENHOUSE_URL.format(board=board)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    raw = resp.json().get("jobs", [])

    jobs = []
    for item in raw:
        title = clean_text(item.get("title", ""))
        if not title:
            continue
        loc = (item.get("location") or {}).get("name") or ""
        link = clean_text(item.get("absolute_url", ""))
        compensation = item.get("compensation") or {}
        jobs.append(
            {
                "title": title,
                "company": clean_text(item.get("company_name", board)),
                "url": link,
                "date": _iso_date(item.get("first_published") or item.get("updated_at")),
                "location": clean_text(loc),
                "description": _strip_html(item.get("content", ""))[:3000],
                "salary": _salary_str(compensation),
                "source": f"ATS·Greenhouse·{board}",
                "id": make_job_id(url=link, title=title, company=board),
            }
        )
    jobs = _sort_newest(jobs, "date", max_jobs)
    LOG.info("ats_api/greenhouse[%s] : %d offres (gardées %d)", board, len(raw), len(jobs))
    return jobs


def _lever(board: str, session, timeout: float, max_jobs: int) -> list:
    url = LEVER_URL.format(board=board)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    raw = resp.json()
    if not isinstance(raw, list):
        return []

    jobs = []
    for item in raw:
        title = clean_text(item.get("text", ""))
        if not title:
            continue
        cat = item.get("categories") or {}
        link = clean_text(item.get("hostedUrl", ""))
        date = ""
        created = item.get("createdAt")
        if created:
            try:
                date = dt.datetime.fromtimestamp(int(created) / 1000).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                date = ""
        jobs.append(
            {
                "title": title,
                "company": clean_text(item.get("company", board)),
                "url": link,
                "date": date,
                "location": clean_text(cat.get("location", "")),
                "description": clean_text(item.get("descriptionPlain", ""))[:3000],
                "source": f"ATS·Lever·{board}",
                "id": make_job_id(url=link, title=title, company=board),
            }
        )
    jobs = _sort_newest(jobs, "date", max_jobs)
    LOG.info("ats_api/lever[%s] : %d offres (gardées %d)", board, len(raw), len(jobs))
    return jobs


def _smartrecruiters(board: str, session, timeout: float, max_jobs: int) -> list:
    url = SMARTRECRUITERS_URL.format(board=board)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    raw = (resp.json() or {}).get("content", [])

    jobs = []
    for item in raw:
        title = clean_text(item.get("name", ""))
        if not title:
            continue
        loc = item.get("location") or {}
        location = clean_text(" ".join(p for p in (loc.get("city"), loc.get("region"), loc.get("fullLocation")) if p))
        if loc.get("remote") and "remote" not in location.lower():
            location = clean_text(f"{location} (Remote)")
        link = f"https://jobs.smartrecruiters.com/{board}/{item.get('id')}"
        exp = (item.get("experienceLevel") or {}).get("label", "")
        jobs.append(
            {
                "title": title,
                "company": clean_text((item.get("company") or {}).get("name", board)),
                "url": link,
                "date": _iso_date(item.get("releasedDate")),
                "location": location,
                "description": exp,
                "source": f"ATS·SmartRecruiters·{board}",
                "id": make_job_id(url=link, title=title, company=board),
            }
        )
    jobs = _sort_newest(jobs, "date", max_jobs)
    LOG.info("ats_api/smartrecruiters[%s] : %d offres (gardées %d)", board, len(raw), len(jobs))
    return jobs


def _workable(board: str, session, timeout: float, max_jobs: int) -> list:
    url = WORKABLE_URL.format(board=board)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json() or {}

    jobs = []
    for item in data.get("jobs", []):
        title = clean_text(item.get("title", ""))
        if not title:
            continue
        city = (item.get("city") or {}) or {}
        remote = bool(item.get("telecommuting"))
        location = clean_text(
            " ".join(
                p
                for p in (
                    city.get("name"),
                    (item.get("state") or {}).get("name") if isinstance(item.get("state"), dict) else item.get("state"),
                    (item.get("country") or {}).get("name") if isinstance(item.get("country"), dict) else item.get("country"),
                )
                if p
            )
        )
        if remote and "remote" not in location.lower():
            location = clean_text(f"{location} (Remote)")
        jobs.append(
            {
                "title": title,
                "company": clean_text((item.get("organizationName") or board)),
                "url": clean_text(item.get("url", "")),
                "date": "",
                "location": location,
                "description": clean_text(item.get("description", ""))[:3000],
                "source": f"ATS·Workable·{board}",
                "id": make_job_id(url=clean_text(item.get("url", "")), title=title, company=board),
            }
        )
    jobs = _sort_newest(jobs, "date", max_jobs)
    LOG.info("ats_api/workable[%s] : %d offres (gardées %d)", board, len(data.get("jobs", [])), len(jobs))
    return jobs


def scrape(source_cfg: dict, keywords: list, http_cfg: dict) -> list:
    session = make_session(http_cfg)
    timeout = float(http_cfg.get("timeout_seconds", 20))
    max_jobs = int(source_cfg.get("max_jobs_per_company", 60))

    # Délai court et paramétrable : les API publiques n'ont pas d'anti-bot,
    # pas besoin du délai générique (2-6 s) prévu pour les pages HTML.
    local_http = dict(http_cfg)
    delay = source_cfg.get("delay_seconds", {}) or {}
    local_http["min_delay_seconds"] = delay.get("min", http_cfg.get("min_delay_seconds", 0.8))
    local_http["max_delay_seconds"] = delay.get("max", http_cfg.get("max_delay_seconds", 1.8))

    jobs = []

    for board in (b.strip() for b in source_cfg.get("greenhouse_boards", []) if b.strip()):
        try:
            jobs += _greenhouse(board, session, timeout, max_jobs)
        except Exception as exc:  # noqa: BLE001 — un board en échec ne bloque pas les autres
            LOG.warning("ats_api/greenhouse[%s] : échec : %s", board, exc)
        random_delay(local_http)

    for board in (b.strip() for b in source_cfg.get("lever_boards", []) if b.strip()):
        try:
            jobs += _lever(board, session, timeout, max_jobs)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("ats_api/lever[%s] : échec : %s", board, exc)
        random_delay(local_http)

    for board in (b.strip() for b in source_cfg.get("smartrecruiters_boards", []) if b.strip()):
        try:
            jobs += _smartrecruiters(board, session, timeout, max_jobs)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("ats_api/smartrecruiters[%s] : échec : %s", board, exc)
        random_delay(local_http)

    for board in (b.strip() for b in source_cfg.get("workable_boards", []) if b.strip()):
        try:
            jobs += _workable(board, session, timeout, max_jobs)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("ats_api/workable[%s] : échec : %s", board, exc)
        random_delay(local_http)

    LOG.info("ats_api : %d offres collectées", len(jobs))
    return jobs
