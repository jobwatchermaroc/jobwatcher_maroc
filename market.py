"""Veille marché hebdo — agrégation des offres pertinentes sur 7 jours.

Principe : chaque offre distincte vue pendant la semaine courante (ISO)
est comptée une seule fois (entreprises, skills, sources, salaires).
Le rapport de la semaine écoulée est envoyé le jour/heure configuré.

État persistant (dans seen_jobs.json) :
{
  "market": {
    "week": "2026-W32", "counted_ids": [<ids>],
    "companies": {<nom>: <nb>}, "skills": {<terme>: <nb>},
    "sources": {<source>: <nb>}, "salaries": [<texte>],
    "total": <nb>, "prev": {<même forme, semaine précédente>}, "prev_reported": <bool>
  }
}
"""

import logging
from datetime import date, datetime, timedelta

from scoring import matched_terms

LOG = logging.getLogger(__name__)

MAX_COUNTED_IDS = 20000  # garde-fou mémoire (ids d'offres comptés sur la semaine)
TOP_COMPANIES = 10
TOP_SKILLS = 12
MAX_SALARIES = 8


def iso_week(d: date | None = None) -> str:
    iso = (d or date.today()).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _week_range(iso_week_str: str) -> str:
    """'2026-W32' -> '03/08 – 09/08' (lundi au dimanche)."""
    try:
        year_s, week_s = iso_week_str.split("-W")
        year, week = int(year_s), int(week_s)
        jan4 = date(year, 1, 4)
        monday = jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=week - 1)
        sunday = monday + timedelta(days=6)
        return f"{monday:%d/%m} – {sunday:%d/%m}"
    except (ValueError, AttributeError):
        return str(iso_week_str)


def _empty_market() -> dict:
    return {
        "week": "",
        "counted_ids": [],
        "companies": {},
        "skills": {},
        "sources": {},
        "salaries": [],
        "total": 0,
        "prev": None,
        "prev_reported": False,
    }


def _roll(market: dict, week: str) -> dict:
    """Bascule sur la semaine courante ; archive la précédente comme rapport à envoyer."""
    if market.get("week") == week:
        return market
    if market.get("total", 0):
        market["prev"] = {
            "week": market["week"],
            "companies": market.get("companies", {}),
            "skills": market.get("skills", {}),
            "sources": market.get("sources", {}),
            "salaries": market.get("salaries", []),
            "total": market.get("total", 0),
        }
        market["prev_reported"] = False
        LOG.info("Veille marché : semaine %s clôturée (%d offres), en attente du rapport hebdo", market["week"], market["total"])
    else:
        market["prev"] = None
        market["prev_reported"] = True
    market["week"] = week
    market["counted_ids"] = []
    market["companies"] = {}
    market["skills"] = {}
    market["sources"] = {}
    market["salaries"] = []
    market["total"] = 0
    return market


def accumulate(state: dict, job: dict, taxonomy: dict, week: str | None = None) -> None:
    """Compte une offre pertinente une seule fois dans la semaine en cours."""
    market = _roll(state.setdefault("market", _empty_market()), week or iso_week())

    job_id = job.get("id")
    if not job_id:
        return
    if job_id in market["counted_ids"]:
        return
    if len(market["counted_ids"]) >= MAX_COUNTED_IDS:
        market["counted_ids"] = []
    market["counted_ids"].append(job_id)

    company = (job.get("company") or "Inconnu").strip()
    if company:
        market["companies"][company] = market["companies"].get(company, 0) + 1

    source = (job.get("source") or "inconnue").strip()
    market["sources"][source] = market["sources"].get(source, 0) + 1

    for term in matched_terms(job, taxonomy):
        market["skills"][term] = market["skills"].get(term, 0) + 1

    salary = (job.get("salary") or "").strip()
    if salary:
        market["salaries"].append(f"{company} : {salary}")

    market["total"] += 1


def format_report(prev: dict) -> str:
    """Message Telegram du rapport hebdo de la semaine écoulée."""
    week = prev.get("week") or "?"
    lines = [f"📊 <b>Veille marché — {_week_range(week)}</b>"]
    lines.append(f"Offres pertinentes vues : <b>{prev.get('total', 0)}</b>\n")

    companies = sorted(prev.get("companies", {}).items(), key=lambda kv: kv[1], reverse=True)[:TOP_COMPANIES]
    if companies:
        lines.append("🏢 <b>Entreprises qui recrutent le plus :</b>")
        lines.extend(f"  • {name} ({n})" for name, n in companies)
        lines.append("")

    skills = sorted(prev.get("skills", {}).items(), key=lambda kv: kv[1], reverse=True)[:TOP_SKILLS]
    if skills:
        lines.append("🧠 <b>Skills les plus demandés :</b>")
        lines.extend(f"  • {skill} ({n})" for skill, n in skills)
        lines.append("")

    sources = sorted(prev.get("sources", {}).items(), key=lambda kv: kv[1], reverse=True)
    if sources:
        lines.append("🌍 <b>Répartition par source :</b>")
        lines.extend(f"  • {src} ({n})" for src, n in sources)
        lines.append("")

    salaries = prev.get("salaries", [])[:MAX_SALARIES]
    if salaries:
        lines.append("💰 <b>Salaires visibles (Greenhouse) :</b>")
        lines.extend(f"  • {s}" for s in salaries)

    return "\n".join(lines)


def pending_report(state: dict, now_utc: datetime, weekday: int, hour_utc: int) -> dict | None:
    """Rapport prêt à envoyer ? (jour + heure atteints et semaine précédente non envoyée)."""
    if now_utc.weekday() != weekday or now_utc.hour < hour_utc:
        return None
    market = state.get("market") or {}
    if market.get("prev") and not market.get("prev_reported"):
        return market["prev"]
    return None


def maybe_send_report(state: dict, notifier, market_cfg: dict, now_utc: datetime) -> None:
    """Envoie le rapport hebdo quand le moment est venu (une seule fois par semaine)."""
    if not market_cfg.get("enabled", True):
        return
    weekday = int(market_cfg.get("weekday", 0))
    hour = int(market_cfg.get("hour_utc", 8))
    prev = pending_report(state, now_utc, weekday, hour)
    if prev is None:
        return
    text = format_report(prev)
    if notifier.dry_run:
        LOG.info("[dry-run] rapport hebdo simulé :\n%s", text)
    else:
        notifier.send_market_report(text)
    state["market"]["prev_reported"] = True
