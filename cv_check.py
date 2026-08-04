#!/usr/bin/env python3
"""CV ATS Checker — comparateur heuristique CV vs offre (0€, sans IA).

Usage :
  python cv_check.py --cv ~/cv.md --url "https://.../offre..."
  python cv_check.py --cv ~/cv.md --text "description de l'offre..."
  python cv_check.py --cv ~/cv.md --file offre.txt

Sortie : % de compatibilité, skills trouvés, skills manquants (par catégorie),
certifications manquantes si l'offre en exige.

Le CV est lu localement — jamais envoyé, jamais commité (voir .gitignore).
"""

import argparse
import sys
from urllib.parse import urlparse

import yaml

from scoring import match_keyword

DEFAULT_CV = "cv.md"


def load_skills(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def find_skills(text: str, skills: dict) -> dict[str, list]:
    """{catégorie: [skills trouvés]} dans un texte."""
    found: dict[str, list] = {}
    for category, terms in (skills or {}).items():
        for term in terms:
            if match_keyword(text, term):
                found.setdefault(category, []).append(term)
    return found


def _fetch_text(url: str) -> str:
    from bs4 import BeautifulSoup

    from scrapers.base import fetch_html, make_session

    session = make_session({})
    html = fetch_html(session, url, {"timeout_seconds": 20, "max_retries": 1})
    soup = BeautifulSoup(html, "lxml")
    container = (
        soup.find("main")
        or soup.find("article")
        or soup.select_one("div[class*=description], div[class*=offre], div[class*=job]")
        or soup.body
    )
    return container.get_text(" ", strip=True) if container else ""


def report(cv_path: str, jd_text: str, skills: dict) -> int:
    try:
        with open(cv_path, "r", encoding="utf-8") as fh:
            cv_text = fh.read()
    except OSError as exc:
        print(f"❌ Impossible de lire le CV {cv_path} : {exc}", file=sys.stderr)
        return 2

    cv_found = find_skills(cv_text, skills)
    jd_found = find_skills(jd_text, skills)

    cv_set = {s for terms in cv_found.values() for s in terms}
    jd_set = {s for terms in jd_found.values() for s in terms}

    matched = sorted(jd_set & cv_set)
    missing = sorted(jd_set - cv_set)

    pct = round(100 * len(matched) / len(jd_set)) if jd_set else 100
    bar = "#" * (pct // 10) + "-" * (10 - pct // 10)

    print(f"\n📊 Compatibilité CV / offre : {pct}%  [{bar}]")
    print(f"Skills demandés : {len(jd_set)} | présents dans le CV : {len(matched)} | manquants : {len(missing)}\n")

    if matched:
        print("✅ MATCHÉS :")
        for s in matched:
            print(f"   • {s}")
    if missing:
        print("\n⚠️  MANQUANTS (à mettre en avant / à apprendre) :")
        for s in missing:
            print(f"   • {s}")

    certs = skills.get("certifications", [])
    missing_certs = [c for c in certs if match_keyword(jd_text, c) and not match_keyword(cv_text, c)]
    if missing_certs:
        print("\n🎓 CERTIFICATIONS DEMANDÉES mais absentes du CV :")
        for c in missing_certs:
            print(f"   • {c}")

    return 0 if pct >= 60 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="CV ATS Checker (heuristique, 0€)")
    parser.add_argument("--cv", default=DEFAULT_CV, help="Chemin vers le CV texte/markdown")
    parser.add_argument("--skills", default="skills.yaml", help="Lexique de skills (YAML)")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="URL d'une offre (le texte est récupéré en ligne)")
    src.add_argument("--text", help="Description de l'offre en argument")
    src.add_argument("--file", help="Fichier contenant la description de l'offre")
    args = parser.parse_args()

    skills = load_skills(args.skills)

    if args.text:
        jd_text = args.text
    elif args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            jd_text = fh.read()
    else:
        parsed = urlparse(args.url)
        if parsed.scheme not in ("http", "https"):
            print("❌ URL invalide", file=sys.stderr)
            return 2
        jd_text = _fetch_text(args.url)

    return report(args.cv, jd_text, skills)


if __name__ == "__main__":
    raise SystemExit(main())
