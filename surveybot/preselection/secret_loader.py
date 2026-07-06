# preselection/secret_loader.py
# Chargement robuste des secrets (ENV + fallback local)

from __future__ import annotations
import json, logging, os

log = logging.getLogger("secret_loader")

# Clés PAR_RECEPTEUR : ressources qui appartiennent au récepteur (exploitant de la
# machine) et sont partagées entre tous ses bots — éditables sans recompilation via
# receiver_config.json (fichier séparé, non versionné, non compilé). Auparavant
# dupliquées à tort dans chaque entrée d'accounts.json (PAR_BOT).
RECEIVER_CONFIG_KEYS = [
    "OPENAI_API_KEY", "TWO_CAPTCHA_KEY",
    "telegram_bot_token", "telegram_chat_id",
    "payout_name", "payout_revolut_tag",
]

def _receiver_config_path() -> str:
    """
    Chemin de receiver_config.json : à côté de l'exécutable/du script du bot (racine
    surveybot\\), un niveau au-dessus de preselection\\ — même convention que
    accounts.json / pids\\ (cf. launch.py::_pid_path).
    """
    preselection_dir = os.path.dirname(os.path.abspath(__file__))
    bot_root = os.path.dirname(preselection_dir)
    return os.path.join(bot_root, "receiver_config.json")

def _from_receiver_config_file() -> dict:
    """
    Charge les clés PAR_RECEPTEUR depuis receiver_config.json (une seule instance par
    machine, partagée par tous les bots qui y tournent — pas un fichier par bot).

    Comportement si le fichier est absent : dict vide, silencieusement. C'est un cas
    normal (dev/attach, ou récepteur n'ayant pas encore créé le fichier) — pas d'erreur,
    et aucune autre source n'est tentée en repli pour ces clés à ce stade (l'éventuel
    override par ENV unitaire reste géré séparément, avec une priorité plus haute, dans
    load_remote_secrets() ci-dessous).
    Le fichier est relu à chaque appel : pas de cache ni de rechargement à chaud, comme
    le reste de ce module (load_config() recalcule aussi à chaque appel).
    """
    path = _receiver_config_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        log.warning("receiver_config.json illisible (%s) — ignoré : %s", path, e)
        return {}
    if not isinstance(raw, dict):
        log.warning("receiver_config.json doit contenir un objet JSON — ignoré (%s).", path)
        return {}
    return {k: v for k, v in raw.items() if k in RECEIVER_CONFIG_KEYS}

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
    Stratégie d'empilement (ordre croissant de priorité — chaque étape peut écraser
    la précédente pour une même clé) :
      1) receiver_config.json — valeurs PAR_RECEPTEUR par défaut pour la machine
      2) TOPSURVEYS_SECRET_JSON (si présent)
      3) ENV unitaires directs
      4) Overrides ENV nommés (pratique en CI/CD : permettent un override ponctuel
         sans toucher receiver_config.json)
    """
    data = {}
    data.update(_from_receiver_config_file())
    data.update(_from_env_json())
    data.update(_from_direct_env_keys())
    data.update(_from_env_overrides())
    return data
