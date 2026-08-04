"""Envoi de notifications Telegram via la Bot API.

Secrets lus depuis l'environnement (jamais en dur dans le code) :
  TELEGRAM_BOT_TOKEN  -> token fourni par @BotFather
  TELEGRAM_CHAT_ID    -> identifiant du chat/du destinataire
"""

import html
import logging
import os

import requests

LOG = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _escape(value) -> str:
    return html.escape(str(value or ""), quote=True)


def format_job_message(job: dict) -> str:
    lines = ["\U0001F6A8 <b>Nouvelle offre d'emploi</b>"]
    lines.append(f"<b>{_escape(job.get('title', 'Sans titre'))}</b>")
    if job.get("score") is not None:
        score = int(job["score"])
        emoji = "\U0001F525" if score >= 80 else "\U0001F4CB"
        lines.append(f"{emoji} Score pertinence : <b>{score}/100</b>")
    if job.get("reasons"):
        lines.append(f"\U0001F50D {_escape(' · '.join(job['reasons']))}")
    if job.get("company"):
        lines.append(f"\U0001F3E2 {_escape(job['company'])}")
    if job.get("location"):
        lines.append(f"\U0001F4CD {_escape(job['location'])}")
    if job.get("date"):
        lines.append(f"\U0001F4C5 {_escape(job['date'])}")
    if job.get("source"):
        lines.append(f"\U0001F9F0 {_escape(job['source'])}")
    if job.get("emails"):
        lines.append(f"\U0001F4E8 <b>{len(job['emails'])} contact(s) public(s) :</b>")
        lines.append(_escape(" · ".join(job["emails"])))
    url = job.get("url")
    if url:
        lines.append(f"\U0001F517 <a href=\"{_escape(url)}\">Voir l'offre</a>")
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, telegram_cfg: dict, dry_run: bool = False):
        self.dry_run = dry_run
        self.enabled = telegram_cfg.get("enabled", True)
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.api_url = telegram_cfg.get("api_url", DEFAULT_API_URL)
        self.disable_web_preview = telegram_cfg.get("disable_web_page_preview", True)
        self.max_length = int(telegram_cfg.get("max_message_length", 4096))

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.token) and bool(self.chat_id)

    def send_message(self, text: str) -> bool:
        if self.dry_run:
            LOG.info("[dry-run] message Telegram simulé :\n%s", text)
            return True
        if not self.ready:
            LOG.warning(
                "Telegram non configuré (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID absents ou désactivés). Envoi ignoré."
            )
            return False

        for chunk in _chunks(text, self.max_length):
            url = self.api_url.format(token=self.token)
            payload = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": self.disable_web_preview,
            }
            try:
                resp = requests.post(url, json=payload, timeout=15)
                data = resp.json()
                if not data.get("ok"):
                    LOG.error("Erreur API Telegram : %s", data)
                    return False
            except requests.RequestException as exc:
                LOG.error("Échec envoi Telegram : %s", exc)
                return False
        return True

    def send_new_jobs(self, jobs: list) -> int:
        sent = 0
        for job in jobs:
            if self.send_message(format_job_message(job)):
                sent += 1
        LOG.info("Notifications Telegram envoyées : %d/%d", sent, len(jobs))
        return sent

    def send_digest(self, jobs: list) -> None:
        """Digest journalier des offres de score moyen (50-79)."""
        if not jobs:
            return
        lines = [f"\U0001F4E2 <b>📋 {len(jobs)} offres pertinentes (score moyen)</b>"]
        for job in jobs[:40]:
            score = int(job.get("score") or 0)
            title = _escape((job.get("title") or "?")[:90])
            source = _escape(job.get("source", ""))
            url = job.get("url")
            contact = ""
            if job.get("emails"):
                contact = " | 📬 " + _escape(job["emails"][0])
            if url:
                lines.append(f"• [{score}] <a href=\"{_escape(url)}\">{title}</a> ({source}){contact}")
            else:
                lines.append(f"• [{score}] {title} ({source}){contact}")
        if len(jobs) > 40:
            lines.append(f"… et {len(jobs) - 40} de plus")
        self.send_message("\n".join(lines))

    def send_health_alert(self, source: str, message: str) -> None:
        """Alerte quand une source est en panne ou semble morte."""
        self.send_message(f"⚠️ <b>Source en alerte : {_escape(source)}</b>\n{_escape(message)}")

    def send_market_report(self, text: str) -> None:
        """Rapport hebdo de veille marché (texte déjà formaté en HTML)."""
        self.send_message(text)

    def send_summary(self, jobs: list) -> None:
        """Résumé groupé pour les offres au-delà du plafond individuel."""
        if not jobs:
            return
        lines = [f"\U0001F4E2 <b>{len(jobs)} autres offres correspondantes</b>"]
        for job in jobs[:60]:
            title = _escape(job.get("title", "?")[:80])
            source = _escape(job.get("source", ""))
            url = job.get("url")
            if url:
                lines.append(f"• <a href=\"{_escape(url)}\">{title}</a> ({source})")
            else:
                lines.append(f"• {title} ({source})")
        if len(jobs) > 60:
            lines.append(f"… et {len(jobs) - 60} de plus")
        self.send_message("\n".join(lines))


def _chunks(text: str, size: int):
    return [text[i : i + size] for i in range(0, len(text), size)]
