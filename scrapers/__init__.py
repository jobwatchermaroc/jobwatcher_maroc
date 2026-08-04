"""Registre des sources. Le nom d'un module doit correspondre à une clé de
`config.yaml` -> `sources:` (ex: `rekrute`, `dreamjob_api`, `ats_api`).
Chaque module expose `scrape(source_cfg, keywords, http_cfg) -> list[dict]`."""

from . import ats_api, dreamjob, dreamjob_api, google_xray, indeed, rekrute

__all__ = ["ats_api", "dreamjob", "dreamjob_api", "google_xray", "indeed", "rekrute"]
