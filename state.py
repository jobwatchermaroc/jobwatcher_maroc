"""État persistant de l'outil (commité en git entre chaque run).

Format de seen_jobs.json :
{
  "jobs":  { "<id>": {"source": ..., "score": ..., "seen_at": "ISO", "key": "norme"} },
  "keys":  { "<titre+entreprise normalisé>": "<id>" },   # dédup inter-sources
  "health": { "<source>": {"failures": 0, "last_ok_at": "ISO", "last_run": "ISO", "last_error": ""} },
  "digest": { "buffer": [ <offres 50-79> ], "last_sent_date": "YYYY-MM-DD" }
}
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from scoring import normalize

LOG = logging.getLogger(__name__)

DEDUP_WINDOW_DAYS = 30  # fenêtre de dédup titre+entreprise


def utcnow() -> datetime:
    """Naive datetime UTC (compatible 3.11+ et 3.12+, sans dépréciation)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def empty_state() -> dict:
    return {
        "jobs": {},
        "keys": {},
        "health": {},
        "digest": {"buffer": [], "last_sent_date": ""},
        "market": {},
        "contacts": {},
    }


def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return empty_state()
        return data
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        LOG.warning("Fichier %s illisible ou absent — on repart de zéro", path)
        return empty_state()


def save_state(path: str, state: dict, max_entries: int) -> None:
    jobs = state.get("jobs", {})
    if len(jobs) > max_entries:
        keep = list(jobs.items())[-max_entries:]
        state["jobs"] = dict(keep)
        state["keys"] = {v.get("key"): k for k, v in state["jobs"].items() if v.get("key")}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    LOG.info("%s mis à jour : %d offres connues", path, len(state.get("jobs", {})))


def dedup_key(job: dict) -> str:
    """Clé normalisée titre+entreprise pour détecter la même offre inter-sources."""
    title = normalize(job.get("title"))
    company = normalize(job.get("company"))
    if not title:
        return ""
    return f"{title} | {company}".strip(" |")


def is_new(job: dict, state: dict) -> tuple[bool, str]:
    """(nouveau?, id). Gère : id connu, clé normalisée connue dans la fenêtre."""
    job_id = job.get("id")
    jobs = state.get("jobs", {})
    if job_id and job_id in jobs:
        return False, job_id or ""

    key = dedup_key(job)
    if key:
        keys = state.get("keys", {})
        known_id = keys.get(key)
        if known_id and known_id in jobs:
            seen_at = jobs[known_id].get("seen_at") or ""
            try:
                when = datetime.fromisoformat(seen_at)
                if utcnow() - when < timedelta(days=DEDUP_WINDOW_DAYS):
                    return False, known_id
            except ValueError:
                return False, known_id
    return True, job_id or ""


def record(job: dict, state: dict, score: int) -> None:
    """Ajoute une offre au state (déjà notifiée ou mise en digest)."""
    job_id = job.get("id")
    key = dedup_key(job)
    entry = {
        "source": job.get("source", ""),
        "score": score,
        "seen_at": utcnow().isoformat(timespec="seconds"),
    }
    if key:
        entry["key"] = key
        state.setdefault("keys", {})[key] = job_id
    state.setdefault("jobs", {})[job_id] = entry


def bump_health(state: dict, source: str, ok: bool, count: int, error: str = "") -> None:
    """Enregistre la santé d'une source. Renvoie True si elle franchit le seuil d'alerte."""
    health = state.setdefault("health", {})
    cur = health.setdefault(source, {"failures": 0, "last_ok_at": "", "last_run": "", "last_error": ""})
    cur["last_run"] = utcnow().isoformat(timespec="seconds")
    if ok:
        cur["failures"] = 0
        cur["last_error"] = ""
        if count:
            cur["last_ok_at"] = cur["last_run"]
            cur["last_ok_count"] = count
    else:
        cur["failures"] = cur.get("failures", 0) + 1
        cur["last_error"] = error
