#def load_config():
#    base_dir = os.path.dirname(os.path.dirname(__file__))
#    config_path = os.path.join(base_dir, "config")
#    with open(config_path, encoding="utf-8") as f:
#        return json.load(f)

from __future__ import annotations
import json, os
from pathlib import Path
from .secret_loader import load_remote_secrets
from Survey.log_utils import log_info

def _load_local_config() -> dict:
    base_dir = Path(__file__).resolve().parent.parent
    candidates = [
        base_dir / "Utils" / "config",
    ]

    for p in candidates:
        try:
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def load_config() -> dict:
    """
    Ordre de priorité (du plus fort au plus faible):
      1) Overrides ENV unitaires & TOPSURVEYS_SECRET_JSON & Secrets Manager (via secret_loader)
      2) Fichier local config.json (dev)
    """
    local = _load_local_config()
    remote = load_remote_secrets()

    # on part du local puis on écrase par le remote (prioritaire)
    merged = dict(local)
    merged.update(remote)

    # Normalisation légère des clés attendues ailleurs dans le code
    # (main.py lit: Email, Password, openai_api_key, payout_name, payout_revolut_tag,
    #  telegram_bot_token, telegram_chat_id)

    # 🔁 Normalisation des clés (Secrets Manager → code)
    key_aliases = {
        "ACTION_DEBUG_TARGET":    "action_debug_target",
        "CAPTCHA_PROVIDER":       "captcha_provider",
        "DATABASE_URL":           "database_url",
        "DOM_CONTEXT_DEBUG":      "dom_context_debug",
        "LOG_LEVEL":              "log_level",
        "LICENSE_KEY":            "license_key",
        "OPENAI_API_KEY":         "openai_api_key",
        "payout_name":            "payout_name",
        "payout_revolut_tag":     "payout_revolut_tag",
        "PLATFORM":               "platform",
        "RUN_ENV":                "run_env",
        "SNAP_ENABLED":           "snap_enabled",
        "SURVEY_TZ":              "survey_tz",
        "STATE_BACKEND":          "state_backend",
        "STATE_TABLE":            "state_table",
        "SURVEY_HEADLESS":        "survey_headless",
        "SURVEY_CTX_DEBUG":       "survey_ctx_debug",
        "telegram_chat_id":       "telegram_chat_id",
        "TWO_CAPTCHA_KEY":        "two_captcha_key",
        "telegram_bot_token":     "telegram_bot_token",
        "UPDATE_CHECK_ENABLED":   "update_check_enabled",
        "UPDATE_MANIFEST_URL":    "update_manifest_url"

    }

    for src, dst in key_aliases.items():
        if src in merged:
            merged[dst] = merged[src]

    # 🧩 Réinjection dans os.environ (mécanisme centralisé unique).
    # Tout le code applicatif (State/account_state.py, Survey/log_utils.py,
    # captcha/recaptcha_handler.py, preselection/license_guard.py, ...) lit ces
    # valeurs via os.getenv/os.environ avec la casse d'origine (ex: DATABASE_URL),
    # sans jamais consulter le dict retourné ici. Sans cette réinjection, une
    # valeur définie uniquement dans le fichier de config globale et absente de
    # l'environnement du process est silencieusement ignorée.
    # Priorité stricte préservée : on ne touche jamais une clé déjà présente
    # dans os.environ (accounts.json, script de lancement, secrets Fly.io, ...).
    _injected = 0
    for src, dst in key_aliases.items():
        if src in os.environ:
            continue
        val = merged.get(dst)
        if val is None:
            continue
        os.environ[src] = str(val)
        _injected += 1
    if _injected:
        log_info("CONFIG_LOADER", f"{_injected} clé(s) de la config globale injectée(s) dans os.environ")

    return merged
