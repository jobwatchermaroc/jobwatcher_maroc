"""Tests du moteur de scoring, de la dédup, de la veille marché et du CV checker."""

import os
from datetime import datetime, timedelta

import pytest

from scoring import normalize, score_job, match_keyword, matched_terms
import state as state_mod
import market as market_mod
import cv_check


TAXONOMY = {
    "strong": ["azure", "azure security", "devsecops", "cloud security", "sentinel", "defender"],
    "medium": ["cloud", "devops", "kubernetes", "terraform", "security", "aws"],
    "excluded_title": ["agent de sécurité", "commercial"],
    "ignored_companies": ["rh express"],
}


def test_normalize():
    assert normalize("  Développeur Azure (H/F)  ") == "developpeur azure h f"
    assert normalize("CI/CD Engineer") == "ci cd engineer"


def test_match_keyword_word_boundary():
    assert match_keyword("SOC Analyst", "soc")
    assert not match_keyword("Société Générale", "soc")


def test_match_keyword_special_chars():
    assert match_keyword("CompTIA Security+ requis", "security+")
    assert not match_keyword("expérience en Cloud Security", "security+")
    assert match_keyword("C# / .NET", "c#")
    assert not match_keyword("Langage C", "c#")


def test_azure_devops_engineer_immediate():
    job = {"title": "Azure DevOps Engineer", "company": "ACME", "description": ""}
    score, reasons = score_job(job, TAXONOMY)
    assert score >= 80
    assert reasons


def test_cloud_security_immediate():
    job = {"title": "Cloud Security Engineer", "company": "ACME", "description": ""}
    score, _ = score_job(job, TAXONOMY)
    assert score >= 80


def test_generic_devops_digest():
    job = {"title": "DevOps Engineer", "company": "ACME", "description": ""}
    score, _ = score_job(job, TAXONOMY)
    assert 50 <= score < 80


def test_description_only_boost():
    job = {"title": "Ingénieur", "company": "ACME", "description": "Expérience en cloud security et Azure exigée."}
    score, _ = score_job(job, TAXONOMY)
    assert score >= 50


def test_excluded_title_zero():
    job = {"title": "Commercial Cloud", "company": "ACME", "description": "azure"}
    score, reasons = score_job(job, TAXONOMY)
    assert score == 0
    assert reasons


def test_ignored_company_zero():
    job = {"title": "Azure Engineer", "company": "RH Express", "description": ""}
    score, _ = score_job(job, TAXONOMY)
    assert score == 0


def test_unrelated_zero():
    job = {"title": "Caissier", "company": "ACME", "description": ""}
    score, _ = score_job(job, TAXONOMY)
    assert score < 50


def test_score_capped_at_100():
    job = {
        "title": "Azure Security Engineer DevSecOps",
        "company": "ACME",
        "description": "cloud security, azure, sentinel, defender",
    }
    score, _ = score_job(job, TAXONOMY)
    assert score <= 100


def test_dedup_same_title_company():
    state = state_mod.empty_state()
    j1 = {"id": "a", "title": "Azure Engineer", "company": "ACME", "source": "Rekrute"}
    j2 = {"id": "b", "title": "Azure Engineer", "company": "ACME", "source": "Dreamjob"}
    assert state_mod.is_new(j1, state) == (True, "a")
    state_mod.record(j1, state, 90)
    new, _ = state_mod.is_new(j2, state)
    assert not new


def test_dedup_different_company_ok():
    state = state_mod.empty_state()
    j1 = {"id": "a", "title": "Azure Engineer", "company": "ACME", "source": "Rekrute"}
    j2 = {"id": "b", "title": "Azure Engineer", "company": "OCP", "source": "Dreamjob"}
    state_mod.record(j1, state, 90)
    new, _ = state_mod.is_new(j2, state)
    assert new


def test_dedup_same_url():
    state = state_mod.empty_state()
    j1 = {"id": "x", "title": "Azure Engineer", "company": "ACME", "url": "https://a/1"}
    j2 = {"id": "x", "title": "Azure Engineer", "company": "ACME", "url": "https://a/1#frag"}
    assert state_mod.is_new(j1, state) == (True, "x")
    state_mod.record(j1, state, 90)
    new, _ = state_mod.is_new(j2, state)
    assert not new


def test_health_failures_accumulate():
    state = state_mod.empty_state()
    state_mod.bump_health(state, "indeed", ok=False, count=0, error="captcha")
    state_mod.bump_health(state, "indeed", ok=False, count=0, error="captcha")
    state_mod.bump_health(state, "indeed", ok=False, count=0, error="captcha")
    assert state["health"]["indeed"]["failures"] == 3


def test_health_never_ok_no_dry_alert():
    from main import _health_alert

    state = state_mod.empty_state()
    state_mod.bump_health(state, "google_xray", ok=True, count=0)  # désactivée, 0 offre
    assert _health_alert(state["health"], "google_xray") is None  # pas de dernier "ok" -> pas d'alerte


def test_health_dry_after_ok_alerts():
    from main import _health_alert

    state = state_mod.empty_state()
    state_mod.bump_health(state, "rekrute", ok=True, count=5)
    # force le dernier ok à il y a 8 jours
    state["health"]["rekrute"]["last_ok_at"] = (state_mod.utcnow().replace(microsecond=0) - timedelta(days=8)).isoformat()
    state["health"]["rekrute"]["last_ok_count"] = 0
    alert = _health_alert(state["health"], "rekrute")
    assert alert is not None
    assert "aucune offre" in alert


def test_matched_terms():
    job = {"title": "Azure DevOps Engineer", "company": "ACME", "description": "Terraform"}
    terms = matched_terms(job, TAXONOMY)
    assert "azure" in terms
    assert "devops" in terms
    assert "terraform" in terms


def test_market_count_distinct_offers_once():
    state = state_mod.empty_state()
    j1 = {"id": "a", "title": "Azure Engineer", "company": "ACME", "source": "Rekrute"}
    j2 = {"id": "b", "title": "DevSecOps", "company": "OCP", "source": "Dreamjob"}
    market_mod.accumulate(state, j1, TAXONOMY, week="2026-W31")
    market_mod.accumulate(state, j2, TAXONOMY, week="2026-W31")
    market_mod.accumulate(state, j1, TAXONOMY, week="2026-W31")  # déjà comptée
    m = state["market"]
    assert m["total"] == 2
    assert m["companies"]["ACME"] == 1
    assert m["companies"]["OCP"] == 1
    assert len(m["counted_ids"]) == 2


def test_market_roll_archives_prev_week():
    state = state_mod.empty_state()
    j1 = {"id": "a", "title": "Azure Engineer", "company": "ACME", "source": "Rekrute"}
    market_mod.accumulate(state, j1, TAXONOMY, week="2026-W31")
    market_mod.accumulate(state, j1, TAXONOMY, week="2026-W32")  # bascule de semaine
    m = state["market"]
    assert m["week"] == "2026-W32"
    assert m["prev"]["total"] == 1
    assert m["prev"]["companies"]["ACME"] == 1
    assert m["total"] == 1  # offre re-comptée sur la nouvelle semaine
    assert not m["prev_reported"]


def test_market_report_trigger_only_when_due():
    state = state_mod.empty_state()
    j1 = {"id": "a", "title": "Azure Engineer", "company": "ACME", "source": "Rekrute"}
    market_mod.accumulate(state, j1, TAXONOMY, week="2026-W31")
    market_mod.accumulate(state, j1, TAXONOMY, week="2026-W32")
    monday_8 = datetime(2026, 8, 3, 8, 0)  # lundi 03/08/2026
    tuesday_8 = datetime(2026, 8, 4, 8, 0)
    monday_7 = datetime(2026, 8, 3, 7, 0)
    assert market_mod.pending_report(state, monday_7, weekday=0, hour_utc=8) is None
    assert market_mod.pending_report(state, tuesday_8, weekday=0, hour_utc=8) is None
    assert market_mod.pending_report(state, monday_8, weekday=0, hour_utc=8) is not None


def test_market_report_sent_once_per_week():
    class FakeNotifier:
        dry_run = True
        sent = 0

        def send_market_report(self, text):  # pragma: no cover — jamais appelé en dry-run
            self.sent += 1

    state = state_mod.empty_state()
    j1 = {"id": "a", "title": "Azure Engineer", "company": "ACME", "source": "Rekrute"}
    market_mod.accumulate(state, j1, TAXONOMY, week="2026-W31")
    market_mod.accumulate(state, j1, TAXONOMY, week="2026-W32")
    notifier = FakeNotifier()
    market_mod.maybe_send_report(state, notifier, {"enabled": True, "weekday": 0, "hour_utc": 8}, datetime(2026, 8, 3, 8, 30))
    assert state["market"]["prev_reported"] is True
    assert notifier.sent == 0  # dry-run : pas d'envoi réel
    # Deuxième passage le même jour : ne renvoie rien
    market_mod.maybe_send_report(state, notifier, {"enabled": True, "weekday": 0, "hour_utc": 8}, datetime(2026, 8, 3, 10, 0))
    assert state["market"]["prev_reported"] is True


def test_market_format_report():
    text = market_mod.format_report({
        "week": "2026-W32",
        "total": 12,
        "companies": {"ACME": 5, "OCP": 3},
        "skills": {"azure": 8, "devsecops": 4},
        "sources": {"rekrute": 7, "dreamjob api": 5},
        "salaries": ["ACME : 45000-55000 USD"],
    })
    assert "12" in text
    assert "ACME (5)" in text
    assert "azure (8)" in text
    assert "03/08" in text


def test_cv_check_find_skills():
    skills = cv_check.load_skills("skills.yaml")
    found = cv_check.find_skills("Nous cherchons un ingénieur Azure Kubernetes Terraform", skills)
    flat = {s for terms in found.values() for s in terms}
    assert "azure" in flat
    assert "kubernetes" in flat
    assert "terraform" in flat


def test_cv_check_report_matching(tmp_path):
    skills = cv_check.load_skills("skills.yaml")
    cv = tmp_path / "cv.md"
    cv.write_text("Expérience : Azure, Kubernetes, Terraform, Docker, CI/CD, Python", encoding="utf-8")
    jd = "Profil : Azure Engineer, Kubernetes, Terraform, Docker, CI/CD, Go requis"
    code = cv_check.report(str(cv), jd, skills)
    assert code == 0  # >= 60% de compatibilité
    assert "go" in cv_check.find_skills(jd, skills)["langages"]  # présent dans l'offre


def test_cv_check_report_weak_match(tmp_path):
    skills = cv_check.load_skills("skills.yaml")
    cv = tmp_path / "cv.md"
    cv.write_text("Expérience : comptabilité, finance, RH", encoding="utf-8")
    jd = "Profil : Azure Sentinel, Kubernetes, Terraform, Docker, Go, AWS exigés"
    code = cv_check.report(str(cv), jd, skills)
    assert code == 1  # faible compatibilité
