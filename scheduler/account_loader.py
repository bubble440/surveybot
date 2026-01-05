import boto3
import json
import os

AWS_REGION = os.getenv("AWS_REGION", "eu-west-3")

def load_account(account_id: str) -> dict:
    """
    Charge le secret AWS Secrets Manager correspondant à l'account_id
    et retourne un dict normalisé utilisable par le scheduler.
    """

    client = boto3.client("secretsmanager", region_name=AWS_REGION)

    try:
        resp = client.get_secret_value(SecretId=account_id)
    except client.exceptions.ResourceNotFoundException:
        raise RuntimeError(f"Secret introuvable pour {account_id}")

    secret_str = resp.get("SecretString")
    if not secret_str:
        raise RuntimeError(f"Secret vide pour {account_id}")

    secret = json.loads(secret_str)

    # 🔴 POINT CRITIQUE : lecture exacte des clés
    proxy_url = secret.get("PROXY_URL", "").strip()
    proxy_user = secret.get("PROXY_USER", "").strip()
    proxy_pass = secret.get("PROXY_PASS", "").strip()

    if not proxy_url:
        raise RuntimeError(f"Proxy manquant pour {account_id}")
    
    print("[DEBUG][SECRET]", secret)

    return {
        "ACCOUNT_ID": account_id,

        # 🔑 Proxy brut (PAS de parsing ici)
        "PROXY_URL": proxy_url,
        "PROXY_USER": proxy_user,
        "PROXY_PASS": proxy_pass,

        # Autres infos
        "EMAIL": secret.get("EMAIL"),
        "PASSWORD": secret.get("PASSWORD"),
        "GEO_LAT": secret.get("GEO_LAT"),
        "GEO_LON": secret.get("GEO_LON"),
        "SURVEY_LANG": secret.get("SURVEY_LANG", "fr-FR"),
        "SURVEY_TZ": secret.get("SURVEY_TZ", "Europe/Paris"),
    }
