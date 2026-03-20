# preselection/secret_loader.py
# Chargement robuste des secrets (ENV + fallback local)

from __future__ import annotations
import json, logging, os

log = logging.getLogger("secret_loader")

def _from_env_json() -> dict:
    """
    Si l'ENV TOPSURVEYS_SECRET_JSON est présent (contenant un JSON),
    on le parse et on le renvoie.
    """
    raw = os.getenv("TOPSURVEYS_SECRET_JSON", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}

def _from_env_overrides() -> dict:
    """
    Surcharges directes par ENV (pratique en CI/CD).
    """
    mapping = {
        "Email": "TOPSURVEYS_EMAIL",
        "Password": "TOPSURVEYS_PASSWORD",
        "openai_api_key": "OPENAI_API_KEY",
        "payout_name": "PAYOUT_NAME",
        "payout_revolut_tag": "PAYOUT_REVOLUT_TAG",
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
        "telegram_chat_id": "TELEGRAM_CHAT_ID",
    }
    out = {}
    for key, envvar in mapping.items():
        v = os.getenv(envvar)
        if v is not None and v != "":
            out[key] = v
    return out

def _from_direct_env_keys() -> dict:
    keys = [
        "EMAIL", "PASSWORD",
        "PROXY_URL", "PROXY_USER", "PROXY_PASS",
        "GEO_LAT", "GEO_LON",
        "SURVEY_LANG", "SURVEY_TZ",
        "ACCOUNT_ID",
        "OPENAI_API_KEY", "PAYOUT_NAME", "PAYOUT_REVOLUT_TAG",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    ]
    out = {}
    for k in keys:
        v = os.getenv(k)
        if v is not None and v != "":
            out[k] = v
    return out

def load_remote_secrets() -> dict:
    """
    Stratégie d'empilement :
      1) TOPSURVEYS_SECRET_JSON (si présent)
      2) Overrides ENV unitaires
    """
    data = {}
    data.update(_from_env_json())
    data.update(_from_direct_env_keys())
    data.update(_from_env_overrides())
    return data
