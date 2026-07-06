from __future__ import annotations
import os
from .secret_loader import load_remote_secrets
from Survey.log_utils import log_info

def load_config() -> dict:
    """
    Source unique de secrets : load_remote_secrets() (ENV unitaires,
    TOPSURVEYS_SECRET_JSON, Secrets Manager — cf. secret_loader.py).
    """
    merged = dict(load_remote_secrets())

    # Normalisation légère des clés attendues ailleurs dans le code
    # (main.py lit: Email, Password, openai_api_key, payout_name, payout_revolut_tag,
    #  telegram_bot_token, telegram_chat_id)

    # 🔁 Normalisation des clés (Secrets Manager → code)
    key_aliases = {
        "ACTION_DEBUG_TARGET":    "action_debug_target",
        "CAPTCHA_PROVIDER":       "captcha_provider",
        "DOM_CONTEXT_DEBUG":      "dom_context_debug",
        "LOG_LEVEL":              "log_level",
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

    # Variables GLOBAL_CONFIG : source unique de vérité = import direct depuis
    # global_config.py (cf. config.py). Jamais réinjectées dans os.environ ici —
    # un fallback os.getenv les rendrait écrasables via une variable d'environnement
    # définie avant le lancement du bot, ce qui annulerait la protection du build figé.
    _GLOBAL_CONFIG_KEYS = {
        "PLATFORM", "STATE_BACKEND", "STATE_TABLE", "STATE_TTL_DAYS",
        "SURVEY_BROWSER_BIN", "SURVEY_HEADLESS", "SNAP_ENABLED",
        "UPDATE_CHECK_ENABLED", "UPDATE_MANIFEST_URL",
    }

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
        if src in _GLOBAL_CONFIG_KEYS:
            continue
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
