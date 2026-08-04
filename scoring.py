"""Moteur de pertinence : taxonomie Azure/Cloud/DevSecOps + score 0-100.

Remplace le filtre binaire par mots-clés par un score pondéré.
  - score >= immediate_threshold : notification individuelle immédiate
  - score >= digest_threshold    : digest journalier groupé
  - sinon                        : ignorée

Pondérations :
  - terme FORT dans le titre        : +80  (azure, sentinel, devsecops, ...)
  - terme MOYEN dans le titre       : +50  (cloud, devops, kubernetes, ...)
  - terme FORT dans la description  : +25
  - terme MOYEN dans la description : +10
  - géo MA/FR confirmée             : +5
  - entreprise ignorée OU pattern exclu dans le titre : score 0
"""

import re
import unicodedata

WEIGHTS = {
    "title_strong": 80,
    "title_medium": 50,
    "desc_strong": 25,
    "desc_medium": 10,
    "geo": 5,
}

# Maximum de hits comptabilisés par liste (évite l'inflation des scores).
HIT_CAP = 2


def no_accent(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(c) != "Mn"
    )


def normalize(value) -> str:
    """Normalisation pour matching : minuscules, sans accents, sans ponctuation."""
    if not value:
        return ""
    s = no_accent(str(value))
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_keyword(text, keyword) -> bool:
    """Mot-clé avec espace => sous-chaîne ; sans espace => mot entier (\\b).

    Termes contenant '+' ou '#' (security+, c#, c++) : sous-chaîne exacte
    (sinon "security+" matcherait "Cloud Security").

    Ex: "soc" ne matche pas "société" mais matche "SOC Analyst".
    """
    raw = no_accent(str(text)).lower()
    kw = no_accent(str(keyword)).strip().lower()
    if not kw:
        return False
    if any(ch in kw for ch in "+#"):
        return kw in raw
    text = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", raw)).strip()
    kw = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", kw)).strip()
    if " " in kw:
        return kw in text
    return re.search(rf"\b{re.escape(kw)}\b", text) is not None


def _hits(text: str, terms: list) -> int:
    count = 0
    for term in terms:
        if match_keyword(text, term):
            count += 1
            if count >= HIT_CAP:
                break
    return count


def _matches(text: str, terms: list) -> list:
    """Renvoie les termes effectivement trouvés (pour afficher la raison du match)."""
    found = []
    for term in terms:
        if match_keyword(text, term):
            found.append(term)
    return found


def score_job(job: dict, taxonomy: dict) -> tuple[int, list]:
    """Renvoie (score, raisons). Score 0 = à ignorer (exclusion/entreprise)."""
    reasons = []

    company = (job.get("company") or "").strip().lower()
    for ignored in (str(c).strip().lower() for c in taxonomy.get("ignored_companies", []) if c):
        if ignored and ignored in company:
            return 0, [f"entreprise ignorée : {ignored}"]

    title = (job.get("title") or "").lower()
    for term in (str(t).strip().lower() for t in taxonomy.get("excluded_title", []) if t):
        if term and match_keyword(title, term):
            return 0, [f"titre exclu : {term}"]

    title_text = job.get("title") or ""
    desc_text = job.get("description") or ""
    strong = [str(t) for t in taxonomy.get("strong", []) if t]
    medium = [str(t) for t in taxonomy.get("medium", []) if t]

    ts = _hits(title_text, strong)
    tm = _hits(title_text, medium)
    ds = _hits(desc_text, strong)
    dm = _hits(desc_text, medium)

    score = (
        ts * WEIGHTS["title_strong"]
        + tm * WEIGHTS["title_medium"]
        + ds * WEIGHTS["desc_strong"]
        + dm * WEIGHTS["desc_medium"]
    )

    if ts:
        reasons.append(" + ".join(_matches(title_text, strong)))
    elif tm:
        reasons.append(" + ".join(_matches(title_text, medium)))
    if ds:
        reasons.append("description: " + " + ".join(_matches(desc_text, strong)))
    if dm and not ds:
        reasons.append("description: " + " + ".join(_matches(desc_text, medium)))

    return min(100, score), reasons


def matched_terms(job: dict, taxonomy: dict) -> list:
    """Tous les termes strong+medium trouvés dans titre OU description (pour la veille)."""
    title = job.get("title") or ""
    desc = job.get("description") or ""
    found = set()
    for group in ("strong", "medium"):
        for term in (str(t) for t in taxonomy.get(group, []) if t):
            if match_keyword(title, term) or match_keyword(desc, term):
                found.add(term)
    return sorted(found)
