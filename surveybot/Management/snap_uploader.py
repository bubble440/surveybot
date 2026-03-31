"""
snap_uploader.py
----------------
Upload optionnel de screenshots vers Cloudflare R2 (API S3-compatible).

Activation : variable d'env SNAP_ENABLED=1 injectée via accounts.json.
Si SNAP_ENABLED est absent ou != "1", toutes les fonctions sont des no-ops silencieux.

Variables d'env requises quand SNAP_ENABLED=1 :
  SNAP_R2_ACCOUNT_ID        — Cloudflare account ID (ex: abc123def456...)
  SNAP_R2_ACCESS_KEY_ID     — clé R2 (Access Key ID)
  SNAP_R2_SECRET_ACCESS_KEY — clé R2 (Secret)
  SNAP_R2_BUCKET            — nom du bucket R2

Nommage des objets dans le bucket :
  {account_id}_{session_heure_GMT2}/{label}_{timestamp}.png

  - account_id    : récupéré depuis RuntimeGuard (ex: topsurveys_bot_001)
  - session_heure : heure de démarrage du process, UTC+2, calculée une seule fois
                    au premier upload (ex: 20h04). Stable pour toute la durée du bot.
  - label         : identifiant du point d'injection (ex: "start_solve_full_survey")
  - timestamp     : heure précise de l'upload (UTC)

  Exemple de clé : topsurveys_bot_001_20h04/start_solve_full_survey_20260331_200537.png

  Deux machines du même compte lancées à des moments différents auront des dossiers
  distincts grâce au suffixe horaire (écart minimum ~2 min en pratique).

Comportement en cas d'erreur :
  - Upload échoue → log [SNAP_R2][ERROR] + continue (jamais bloquant)
  - Dépendance boto3 absente → log [SNAP_R2][ERROR] + continue
"""

import os
import time
from datetime import datetime, timezone, timedelta

# Tag de logging cohérent avec les conventions du projet
_TAG = "SNAP_R2"

# Dossier R2 calculé une seule fois au premier upload, stable pour toute la session.
# Format : {account_id}_{HH}h{MM} en heure locale UTC+2.
_session_folder: str | None = None


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


def _get_session_folder() -> str:
    """
    Retourne le dossier R2 de session, calculé une seule fois par process.
    Format : {account_id}_{HH}h{MM} (heure UTC+2 au démarrage du premier upload).
    Stable pour toute la durée de vie du bot, même si l'heure change entre deux uploads.
    """
    global _session_folder
    if _session_folder is not None:
        return _session_folder

    account_id = _get_account_id()
    # Heure locale UTC+2 (Paris/Europe) au moment de l'initialisation
    tz_paris = timezone(timedelta(hours=2))
    now_paris = datetime.now(tz=tz_paris)
    hour_label = now_paris.strftime("%Hh%M")
    _session_folder = f"{account_id}_{hour_label}"
    return _session_folder


def _build_client():
    """
    Construit un client boto3 pointant sur l'endpoint R2 du compte Cloudflare.
    Lève une exception si boto3 est absent ou si une variable est manquante.
    """
    import boto3  # import tardif : pas de dépendance dure si SNAP_ENABLED=0

    r2_account_id = os.environ["SNAP_R2_ACCOUNT_ID"]
    endpoint = f"https://{r2_account_id}.r2.cloudflarestorage.com"

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
        label     : identifiant du point d'injection (ex: "start_solve_full_survey")
    """
    if not _is_enabled():
        return

    try:
        from Survey.log_utils import log_info, log_debug
    except ImportError:
        log_info = lambda tag, msg: print(f"[{tag}] {msg}", flush=True)
        log_debug = log_info

    try:
        bucket = os.environ["SNAP_R2_BUCKET"]
        folder = _get_session_folder()          # ex: topsurveys_bot_001_20h04
        ts = time.strftime("%Y%m%d_%H%M%S")    # timestamp UTC de l'upload
        key = f"{folder}/{label}_{ts}.png"

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