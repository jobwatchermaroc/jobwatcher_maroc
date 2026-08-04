# job-watcher-maroc

Bot de veille d'emploi pour le domaine **Azure Cloud / Cloud Security / DevSecOps**, filtre sur le **Maroc + la France** et te notifie **sur Telegram** chaque offre pertinente — avec un moteur de **score de pertinence** (0-100), des notifications par tiers, une **veille marché hebdomadaire** et un **CV ATS Checker** (100 % heuristique, 0 €, sans IA).

Prêt à tourner sur **GitHub Actions** (cron toutes les 5 min) et en **local**.

## Fonctionnement

1. **Collecte** des offres depuis chaque source activée :
   - **Dreamjob API** (`/wp-json/wp/v2/posts`) — JSON temps réel, horodatage exact
   - **ATS API** (Greenhouse / Lever / SmartRecruiters / **Workable**) — API publiques sans clé, offres instantanées (France, remote, ATS internationaux)
   - **Rekrute / Indeed / Dreamjob** — scraping HTML (conservés en secours)
   - **Google X-Ray** — Google Custom Search sur les ATS (backup, nécessite les clés)
2. **Extraction** : titre, entreprise, lien, date, lieu, description, source.
3. **Filtre géo** : Maroc + France uniquement (source marocaine, ou lieu FR/MA/remote ; pays hors FR/MA exclus).
4. **Score de pertinence** (`scoring.py`) : pondération par taxonomie Azure/Cloud/DevSecOps.
5. **Dédup inter-sources** (`state.py`) : une offre = une notification, même si trouvée sur plusieurs sites (clé URL + titre/entreprise normalisé).
6. **Notification par tiers** :
   - score **≥ 80** → notification individuelle immédiate (avec score + raison du match)
   - score **50-79** → digest journalier groupé (18h UTC = 19h MA)
   - score **< 50** → ignorée
7. **Alertes santé** : Telegram si une source échoue 3 fois de suite ou est à sec depuis 7 jours.
8. **Veille marché hebdo** (`market.py`) : chaque offre pertinente vue est comptée une fois dans la semaine (entreprises, skills, sources, salaires) ; un **rapport hebdomadaire** Telegram est envoyé le lundi 8h UTC pour la semaine écoulée.
9. **Persistance** : `seen_jobs.json` enrichi (`jobs`, `keys`, `health`, `digest`, `market`) commité en git après chaque run.

## CV ATS Checker (standalone, 0 €)

Compare ton CV à la description d'une offre et donne **% de compatibilité**, les skills matchés/manquants et les certifications demandées absentes. Aucune IA : pur matching heuristique contre `skills.yaml`.

```bash
# CV en local (jamais commité — voir .gitignore)
python cv_check.py --cv ~/cv.md --url "https://.../offre..."     # depuis une URL
python cv_check.py --cv ~/cv.md --text "description de l'offre"  # depuis un texte
python cv_check.py --cv ~/cv.md --file offre.txt                 # depuis un fichier
```

Sortie : `📊 Compatibilité CV / offre : 88%` + liste des skills. Code de sortie 0 si ≥ 60 %, 1 sinon.

## Arborescence

```
job-watcher-maroc/
├── main.py                     # Orchestration (run_once)
├── config.yaml                 # Taxonomie, sources, notifications, veille marché
├── ignored.yaml                # Feedback loop : entreprises + patterns à exclure
├── scoring.py                  # Moteur de score 0-100
├── market.py                   # Veille marché hebdo (comptage + rapport)
├── cv_check.py                 # CV ATS Checker (comparateur CV vs offre, 0 €)
├── skills.yaml                 # Lexique des skills pour le CV checker
├── state.py                    # seen_jobs.json enrichi + dédup + santé
├── dates.py                    # Parsing des dates
├── notifier.py                 # Envoi Telegram (individuel, digest, alertes, rapport)
├── requirements.txt
├── seen_jobs.json              # État persistant (commité dans le repo)
├── tests/test_core.py          # Tests unitaires (scoring, dédup, santé, veille, CV)
├── scrapers/
│   ├── __init__.py
│   ├── base.py                 # HTTP, User-Agent, délais, retries, Playwright
│   ├── dreamjob_api.py         # WP REST API temps réel
│   ├── rekrute.py              # HTML
│   ├── indeed.py               # HTML
│   ├── dreamjob.py             # HTML (fallback)
│   ├── google_xray.py          # Google Custom Search JSON API
│   └── ats_api.py              # Greenhouse / Lever / SmartRecruiters / Workable
└── .github/workflows/watch.yml
```

## Configuration (config.yaml)

- `search_keywords` : mots-clés courts passés aux recherches de sites (rekrute/indeed/dreamjob/x-ray).
- `taxonomy.strong` : termes FORTS du domaine — titre (+80) ou description (+25) → azure, sentinel, defender, entra id, devsecops, cloud security…
- `taxonomy.medium` : termes VOISINS — titre (+50) ou description (+10) → cloud, devops, kubernetes, terraform, aws…
- `taxonomy.excluded_title` : si le titre en contient un → score 0 (agent de sécurité, commercial, comptable…).
- `taxonomy.ignored_companies` + `ignored.yaml` : entreprises bannies (feedback loop).
- `notifications` : `immediate_threshold` (80), `digest_threshold` (50), `digest_hour` (18 UTC), `max_new_jobs_per_run` (0 = illimité).
- `market_report` : veille hebdo — `weekday` (0 = lundi), `hour_utc` (8 UTC = 9h MA) ; le rapport porte sur la semaine **écoulée**.
- `locations` : filtre géographique Maroc + France (`moroccan_sources`, `country_terms`, `excluded_country_terms`).
- `sources.*.enabled` : activer/désactiver une source.
- `sources.ats_api.workable_boards` : comptes Workable (voodoo, qonto…).
- `sources.dreamjob_api` : `since_days` (fenêtre de fraîcheur), `per_page`, `max_pages`.
- `http` : user-agent, timeouts, délais, retries.

Les **secrets** sont lus depuis l'environnement : `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`.

## Déploiement

Workflow `.github/workflows/watch.yml` :
- cron **toutes les 5 minutes**,
- lance les **tests unitaires** puis le watcher,
- commit automatique de `seen_jobs.json` s'il a changé.

> ⚠️ Sur un repo **privé**, le quota GitHub Actions (2000 min/mois en gratuit) est très vite épuisé et les runs sont sautés. Pour un cron 5 min, un **repo public** (ou GitHub Pro) est nécessaire.

## Tester en local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Test à blanc : aucune notif, seen_jobs.json inchangé
python main.py --dry-run -v

# Tests unitaires
python -m pytest -q
```

Pour l'envoi réel :

```bash
export TELEGRAM_BOT_TOKEN="..." TELEGRAM_CHAT_ID="..."
export GOOGLE_API_KEY="..." GOOGLE_CSE_ID="..."
python main.py -v
```

## Conseils / limites

- **Pertes de source** : rekrute/dreamjob/indeed cassent si le HTML change (monitoring par les alertes santé).
- **Indeed** : souvent bloqué par captcha (0 offre — normal, les autres sources tournent).
- **Quota Google** : 100 requêtes/jour en gratuit ; l'API Custom Search s'arrête le 1er janvier 2027.
- **Faux positifs** : le score est configurable ; ajoutez des exclusions dans `ignored.yaml` au fil du temps.
