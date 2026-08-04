#!/usr/bin/env python3
"""Job watcher — Azure/Cloud Security/DevSecOps (Maroc + France).

Pipeline (run_once) :
  1. Charge config.yaml
  2. Interroge chaque source activée (une source en échec ne bloque pas les autres)
  3. Filtre géo (MA/FR uniquement) puis score chaque offre (scoring.py)
  4. Déduplique (URL + titre/entreprise normalisé) via state.py
  5. Notifie Telegram par tiers : score >= 80 individuel immédiat,
     score 50-79 digest journalier, < 50 ignoré
  6. Alertes santé (3 échecs consécutifs ou source à sec depuis 7 jours)
  7. Veille marché hebdo : comptage des offres pertinentes de la semaine
     (entreprises, skills, sources, salaires) + rapport hebdomadaire (market.py)
  8. Persiste seen_jobs.json (commité par GitHub Actions)

Usage :
  python main.py                 # exécution normale
  python main.py --dry-run -v    # test local : aucune notif, seen_jobs inchangé
"""

import argparse
import logging
import sys
from datetime import date, datetime

import yaml

import scrapers
import state as state_mod
import market as market_mod
import contacts as contacts_mod
from dates import is_fresh, parse_date
from notifier import TelegramNotifier
from scoring import score_job

LOG = logging.getLogger("job-watcher")


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    taxonomy = cfg.get("taxonomy") or {}
    if not isinstance(taxonomy.get("strong"), list) or not taxonomy["strong"]:
        LOG.error("config.yaml doit contenir taxonomy.strong (liste non vide)")
        raise SystemExit(1)
    return cfg


def load_ignored(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError:
        return {}
    return data if isinstance(data, dict) else {}


def _geo_bonus(job: dict, loc_cfg: dict) -> bool:
    """True si le lieu confirme MA/FR (source marocaine ou terme pays présent)."""
    source = (job.get("source") or "").lower()
    moroccan = [str(s).lower() for s in loc_cfg.get("moroccan_sources", []) if s]
    if any(s in source for s in moroccan):
        return True
    loc = (job.get("location") or "").lower()
    return any(term in loc for term in (str(t).lower() for t in loc_cfg.get("country_terms", []) if t))


def location_allowed(job: dict, loc_cfg: dict) -> bool:
    """Filtre géographique : uniquement MAROC + FRANCE (ou lieu vide/ambigu)."""
    source = (job.get("source") or "").lower()
    moroccan = [str(s).lower() for s in loc_cfg.get("moroccan_sources", []) if s]
    if any(s in source for s in moroccan):
        return True
    loc = (job.get("location") or "").lower()
    for term in (str(t).lower() for t in loc_cfg.get("excluded_country_terms", []) if t):
        if term in loc:
            return False
    for term in (str(t).lower() for t in loc_cfg.get("country_terms", []) if t):
        if term in loc:
            return True
    return True


def _health_alert(health: dict, source: str) -> str | None:
    """Message d'alerte si la source dépasse 3 échecs ou est à sec depuis 7 jours."""
    h = health.get(source) or {}
    now = state_mod.utcnow()
    failures = h.get("failures", 0)
    if failures >= 3:
        last_alert = h.get("last_alert_at") or ""
        try:
            since = (now - datetime.fromisoformat(last_alert)).days if last_alert else 999
        except ValueError:
            since = 999
        if since >= 1:
            h["last_alert_at"] = now.isoformat(timespec="seconds")
            return f"{failures} échecs consécutifs — dernière erreur : {h.get('last_error', 'inconnue')}"
    count = h.get("last_ok_count", 0)
    last_ok = h.get("last_ok_at") or ""
    if count == 0 and last_ok:
        # Seulement si la source a déjà produit au moins une fois : une source
        # jamais "ok" (désactivée par design, ex. google_xray sans clés, ou
        # indeed bloqué dès le départ) ne doit pas déclencher d'alerte.
        try:
            since_ok = (now - datetime.fromisoformat(last_ok)).days
        except ValueError:
            since_ok = 999
        if since_ok >= 7:
            h["last_alert_at"] = now.isoformat(timespec="seconds")
            return f"aucune offre collectée depuis {since_ok} jours — source probablement morte ou filtres trop stricts"


def run_once(cfg: dict, args) -> int:
    taxonomy = cfg.get("taxonomy", {})
    loc_cfg = cfg.get("locations", {})
    notif_cfg = cfg.get("notifications", {})
    min_date = str(cfg.get("min_date", "") or "").strip()
    http_cfg = cfg.get("http", {})
    seen_cfg = cfg.get("seen", {})
    seen_path = seen_cfg.get("file", "seen_jobs.json")
    search_keywords = cfg.get("search_keywords", [])
    ignored = load_ignored(cfg.get("ignored", {}).get("file", "ignored.yaml"))
    if ignored.get("companies"):
        taxonomy = {**taxonomy, "ignored_companies": taxonomy.get("ignored_companies", []) + ignored["companies"]}
    if ignored.get("title_patterns"):
        taxonomy = {**taxonomy, "excluded_title": taxonomy.get("excluded_title", []) + ignored["title_patterns"]}

    immediate_threshold = int(notif_cfg.get("immediate_threshold", 80))
    digest_threshold = int(notif_cfg.get("digest_threshold", 50))
    digest_hour = int(notif_cfg.get("digest_hour", 18))
    notify_limit = int(notif_cfg.get("max_new_jobs_per_run", 0))
    contacts_cfg = cfg.get("contacts", {})
    contacts_enabled = contacts_cfg.get("enabled", True) and not args.dry_run

    sources_cfg = cfg.get("sources", {})
    active = [name for name, sc in sources_cfg.items() if sc.get("enabled", False)]
    LOG.info("Sources actives : %s", ", ".join(active) or "(aucune)")

    notifier = TelegramNotifier(cfg.get("telegram", {}), dry_run=args.dry_run)
    state = state_mod.load_state(seen_path)

    all_jobs = []
    for name in active:
        module = getattr(scrapers, name, None)
        if module is None:
            LOG.error("Module scraper inconnu : %s (clé de sources.* != nom de module)", name)
            continue
        try:
            jobs = module.scrape(sources_cfg[name], search_keywords, http_cfg)
            all_jobs.extend(jobs)
            state_mod.bump_health(state, name, ok=True, count=len(jobs))
            LOG.info("%s : %d offres collectées", name, len(jobs))
        except Exception as exc:  # noqa: BLE001 — une source en échec ne doit pas tout casser
            LOG.exception("Scraper %s en échec : %s", name, exc)
            state_mod.bump_health(state, name, ok=False, count=0, error=str(exc))

    immediate, digest_buffer, skipped = [], [], 0
    for job in all_jobs:
        if not location_allowed(job, loc_cfg):
            LOG.info("AUTRE PAYS : [%s] %s (lieu: %r)", job.get("source"), job.get("title"), job.get("location"))
            continue
        if not is_fresh(job.get("date"), min_date):
            LOG.info("HORS PÉRIODE : [%s] %s (date: %r)", job.get("source"), job.get("title"), job.get("date"))
            continue
        score, reasons = score_job(job, taxonomy)
        if _geo_bonus(job, loc_cfg):
            score = min(100, score + 5)
        job["score"] = score
        job["reasons"] = reasons
        if score == 0 or score < digest_threshold:
            skipped += 1
            continue
        market_mod.accumulate(state, job, taxonomy)
        new, _ = state_mod.is_new(job, state)
        if not new:
            continue
        if score >= immediate_threshold:
            immediate.append(job)
        else:
            digest_buffer.append(job)
        state_mod.record(job, state, score)

    if contacts_enabled:
        session = contacts_mod.make_session(http_cfg)
        budget = int(contacts_cfg.get("max_lookups_per_run", 3))
        max_emails = int(contacts_cfg.get("max_emails", 4))
        targets = immediate if contacts_cfg.get("only_immediate", True) else immediate + digest_buffer
        for job in targets:
            if budget <= 0:
                LOG.info("Prospection : budget de %d recherche(s)/run atteint", int(contacts_cfg.get("max_lookups_per_run", 3)))
                break
            info = contacts_mod.get_contacts(state, job, contacts_cfg, session)
            if info:
                if info.get("scraped"):
                    budget -= 1
                emails = info.get("emails") or []
                if emails:
                    job["emails"] = emails[:max_emails]
                    LOG.info("Contacts [%s] : %s", job.get("company"), ", ".join(job["emails"]))

    immediate.sort(key=lambda j: parse_date(j.get("date")) or date.min, reverse=True)
    LOG.info(
        "Score: %d immédiates (>=%d), %d en digest (%d-%d), %d ignorées",
        len(immediate),
        immediate_threshold,
        len(digest_buffer),
        digest_threshold,
        immediate_threshold - 1,
        skipped,
    )
    for job in immediate:
        LOG.info("OFFRE IMMÉDIATE (%d) : [%s] %s | %s | %s", job["score"], job.get("source"), job.get("title"), job.get("company"), job.get("url"))

    if not args.dry_run:
        for name in active:
            alert = _health_alert(state.get("health", {}), name)
            if alert:
                notifier.send_health_alert(name, alert)
                LOG.warning("Alerte santé [%s] : %s", name, alert)

    if immediate:
        if notify_limit and len(immediate) > notify_limit:
            notifier.send_new_jobs(immediate[:notify_limit])
            notifier.send_digest(immediate[notify_limit:])
        else:
            notifier.send_new_jobs(immediate)

    digest = state.setdefault("digest", {})
    digest.setdefault("buffer", []).extend(digest_buffer)
    if len(digest["buffer"]) > 500:
        digest["buffer"] = digest["buffer"][-500:]

    now_utc = state_mod.utcnow()
    last_sent = digest.get("last_sent_date") or ""
    if digest["buffer"] and now_utc.hour >= digest_hour and last_sent != now_utc.strftime("%Y-%m-%d"):
        notifier.send_digest(digest["buffer"])
        digest["buffer"] = []
        digest["last_sent_date"] = now_utc.strftime("%Y-%m-%d")

    market_mod.maybe_send_report(state, notifier, cfg.get("market_report", {}), now_utc)

    if not args.dry_run:
        state_mod.save_state(seen_path, state, int(seen_cfg.get("max_entries", 5000)))
    else:
        LOG.info("Dry-run : seen_jobs.json NON mis à jour")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Job watcher — Azure/Cloud Security/DevSecOps MA+FR")
    parser.add_argument("-c", "--config", default="config.yaml", help="Chemin vers config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Pas d'envoi Telegram ni de mise à jour de seen_jobs.json")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs détaillés")
    args = parser.parse_args()

    setup_logging(args.verbose)
    cfg = load_config(args.config)
    return run_once(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
