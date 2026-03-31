"""
snap_uploader.py
----------------
Upload optionnel de screenshots vers Cloudflare R2 (API S3-compatible).

Activation : variable d'env SNAP_ENABLED=1 injectée via accounts.json.
Si SNAP_ENABLED est absent ou != "1", toutes les fonctions sont des no-ops silencieux.

Variables d'env requises quand SNAP_ENABLED=1 :
  SNAP_R2_ACCOUNT_ID      — Cloudflare account ID (ex: abc123def456...)
  SNAP_R2_ACCESS_KEY_ID   — clé R2 (Access Key ID)
  SNAP_R2_SECRET_ACCESS_KEY — clé R2 (Secret)
  SNAP_R2_BUCKET          — nom du bucket R2

Nommage des objets dans le bucket : {account_id}/{label}_{timestamp}.png
  account_id est récupéré depuis RuntimeGuard (getattr guard.account_id).
  Si RuntimeGuard n'est pas disponible, fallback sur "unknown".

Comportement en cas d'erreur :
  - Upload échoue → log [SNAP_R2][ERROR] + continue (jamais bloquant)
  - Dépendance boto3 absente → log [SNAP_R2][ERROR] + continue
"""

import os
import time

# Tag de logging cohérent avec les conventions du projet
_TAG = "SNAP_R2"


def _is_enabled() -> bool:
    return os.getenv("SNAP_ENABLED", "").strip() == "1"


def _get_account_id() -> str:
    """Récupère l'account_id depuis RuntimeGuard si disponible."""
    try:
        from Management.guards.runtime_guard import get_guard
        guard = get_guard()
        return getattr(guard, "account_id", "unknown") or "unknown"
    except Exception:
        return "unknown"


def _build_client():
    """
    Construit un client boto3 pointant sur l'endpoint R2 du compte Cloudflare.
    Lève une exception si boto3 est absent ou si une variable est manquante.
    """
    import boto3  # import tardif : pas de dépendance dure si SNAP_ENABLED=0

    account_id = os.environ["SNAP_R2_ACCOUNT_ID"]
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["SNAP_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["SNAP_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def upload_png(png_bytes: bytes, label: str) -> None:
    """
    Upload un screenshot PNG vers R2.

    - No-op silencieux si SNAP_ENABLED != "1".
    - Jamais bloquant : toute exception est catchée et loggée.

    Args:
        png_bytes : contenu brut du screenshot (driver.get_screenshot_as_png())
        label     : identifiant libre (ex: "after_continue_click", "error_pwd_step")
    """
    if not _is_enabled():
        return

    try:
        from Survey.log_utils import log_info, log_debug
    except ImportError:
        # Fallback si log_utils non disponible dans le contexte appelant
        log_info = lambda tag, msg: print(f"[{tag}] {msg}", flush=True)
        log_debug = log_info

    try:
        bucket = os.environ["SNAP_R2_BUCKET"]
        account_id = _get_account_id()
        ts = time.strftime("%Y%m%d_%H%M%S")
        key = f"{account_id}/{label}_{ts}.png"

        client = _build_client()
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=png_bytes,
            ContentType="image/png",
        )
        log_info(_TAG, f"uploaded → r2://{bucket}/{key}")

    except Exception as e:
        log_info(_TAG, f"[ERROR] upload failed for label={label} : {type(e).__name__}: {e}")
