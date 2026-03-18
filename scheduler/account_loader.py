import boto3
import json
import os

RUN_ENV     = os.getenv("RUN_ENV", "")
AWS_REGION  = os.getenv("AWS_REGION", "eu-west-3")
GCP_PROJECT = os.getenv("GCP_PROJECT")


# ============================================================================
# API publique
# ============================================================================

def load_account(account_id: str) -> dict:
    """
    Charge le secret correspondant à l'account_id depuis le gestionnaire
    de secrets du cloud actif (AWS Secrets Manager ou GCP Secret Manager)
    et retourne un dict normalisé utilisable par le scheduler.
    """
    if RUN_ENV == "gcp":
        return _load_account_gcp(account_id)
    return _load_account_aws(account_id)


# ============================================================================
# AWS – chemin inchangé
# ============================================================================

def _load_account_aws(account_id: str) -> dict:
    client = boto3.client("secretsmanager", region_name=AWS_REGION)

    try:
        resp = client.get_secret_value(SecretId=account_id)
    except client.exceptions.ResourceNotFoundException:
        raise RuntimeError(f"Secret introuvable pour {account_id}")

    secret_str = resp.get("SecretString")
    if not secret_str:
        raise RuntimeError(f"Secret vide pour {account_id}")

    return _parse_secret(account_id, json.loads(secret_str))


# ============================================================================
# GCP – Secret Manager
# ============================================================================

def _load_account_gcp(account_id: str) -> dict:
    try:
        from google.cloud import secretmanager
    except ImportError:
        raise RuntimeError(
            "google-cloud-secret-manager manquant — "
            "pip install google-cloud-secret-manager"
        )

    client = secretmanager.SecretManagerServiceClient()

    # Construire le resource name ; GCP_PROJECT requis (ou ADC via metadata)
    if GCP_PROJECT:
        project = GCP_PROJECT
    else:
        # Tentative de détection depuis ADC (Cloud Run metadata server)
        try:
            import google.auth
            _, project = google.auth.default()
        except Exception:
            project = None
        if not project:
            raise RuntimeError(
                "GCP_PROJECT manquant et non détectable depuis ADC"
            )

    name = f"projects/{project}/secrets/{account_id}/versions/latest"

    try:
        response = client.access_secret_version(name=name)
    except Exception as e:
        raise RuntimeError(f"Secret introuvable pour {account_id}: {e}")

    secret_str = response.payload.data.decode("utf-8")
    if not secret_str:
        raise RuntimeError(f"Secret vide pour {account_id}")

    return _parse_secret(account_id, json.loads(secret_str))


# ============================================================================
# Parsing commun
# ============================================================================

def _parse_secret(account_id: str, secret: dict) -> dict:
    # 🔴 POINT CRITIQUE : lecture exacte des clés
    proxy_url  = secret.get("PROXY_URL", "").strip()
    proxy_user = secret.get("PROXY_USER", "").strip()
    proxy_pass = secret.get("PROXY_PASS", "").strip()

    if not proxy_url:
        raise RuntimeError(f"Proxy manquant pour {account_id}")

    print("[DEBUG][SECRET]", secret)

    return {
        "ACCOUNT_ID": account_id,

        # 🔑 Proxy brut (PAS de parsing ici)
        "PROXY_URL":  proxy_url,
        "PROXY_USER": proxy_user,
        "PROXY_PASS": proxy_pass,

        # Autres infos
        "EMAIL":       secret.get("EMAIL"),
        "PASSWORD":    secret.get("PASSWORD"),
        "GEO_LAT":     secret.get("GEO_LAT"),
        "GEO_LON":     secret.get("GEO_LON"),
        "SURVEY_LANG": secret.get("SURVEY_LANG", "fr-FR"),
        "SURVEY_TZ":   secret.get("SURVEY_TZ", "Europe/Paris"),
    }
