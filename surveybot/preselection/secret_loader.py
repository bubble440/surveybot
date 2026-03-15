# preselection/secret_loader.py
# Chargement robuste des secrets (AWS Secrets Manager + ENV + fallback local)

from __future__ import annotations
import json, logging, os

log = logging.getLogger("secret_loader")

def _from_env_json() -> dict:
    """
    Si l’ENV TOPSURVEYS_SECRET_JSON est présent (contenant un JSON),
    on le parse et on le renvoie.
    """
    raw = os.getenv("TOPSURVEYS_SECRET_JSON", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}

def _from_secrets_manager() -> dict:
    """
    Si l’ENV TOPSURVEYS_SECRET_NAME est présent,
    on demande à AWS Secrets Manager ce secret (clé 'SecretString' au format JSON).
    """
    name = os.getenv("TOPSURVEYS_SECRET_NAME", "").strip()
    if not name:
        return {}
    region = os.getenv("TOPSURVEYS_AWS_REGION") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "eu-west-3"
    try:
        import boto3  # requis dans l’image
    except ImportError:
        log.warning("[SECRETS] boto3 non disponible, impossible de charger les secrets depuis Secrets Manager")
        return {}
    try:
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=name)
        if "SecretString" in resp and resp["SecretString"]:
            return json.loads(resp["SecretString"])
        # binaire (peu probable ici)
        if "SecretBinary" in resp and resp["SecretBinary"]:
            try:
                import base64
                return json.loads(base64.b64decode(resp["SecretBinary"]).decode("utf-8"))
            except Exception as e:
                log.warning(f"[SECRETS] décodage SecretBinary échoué. secret={name} err={e}")
                return {}
    except Exception as e:
        log.warning(f"[SECRETS] Secrets Manager inaccessible. secret={name} region={region} err={e}")
        return {}
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
    Stratégie d’empilement :
      1) TOPSURVEYS_SECRET_JSON (si présent)
      2) TOPSURVEYS_SECRET_NAME via AWS Secrets Manager
      3) Overrides ENV unitaires
    """
    data = {}
    data.update(_from_env_json())
    # n’écrase pas ce qui est déjà défini par TOPSURVEYS_SECRET_JSON
    sm = _from_secrets_manager()
    for k, v in sm.items():
        data.setdefault(k, v)
    # puis override par variables unitaires
    data.update(_from_direct_env_keys())
    data.update(_from_env_overrides())
    return data
