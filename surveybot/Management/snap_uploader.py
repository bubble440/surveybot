"""
snap_uploader.py
----------------
Upload optionnel de screenshots vers Cloudflare R2 (API S3-compatible).

Activation : variable d'env SNAP_ENABLED=1 injectée via accounts.json.
Si SNAP_ENABLED est absent ou != "1", toutes les fonctions sont des no-ops silencieux.

Variables d'env requises quand SNAP_ENABLED=1 :
  SNAP_R2_ACCOUNT_ID        — Cloudflare account ID
  SNAP_R2_ACCESS_KEY_ID     — clé R2 (Access Key ID)
  SNAP_R2_SECRET_ACCESS_KEY — clé R2 (Secret)
  SNAP_R2_BUCKET            — nom du bucket R2

Nommage des objets dans le bucket :
  {account_id}/s{survey_num}_{step:03d}_{label}.png

  Exemple : unknown/s3_002_dom_5blocks.png
  - s{N}   : numéro de survey dans la session (incrémenté via new_survey())
  - {NNN}  : ordre du snap dans ce survey (remis à 0 à chaque new_survey())
  - {label}: contexte court (start, loop, dom_Nblocks, no_survey)

Compteurs :
  - new_survey() : à appeler au début de chaque solve_full_survey()
  - upload_png() : incrémente le step automatiquement
"""

import os
import time

_TAG = "SNAP_R2"

# Compteurs de session (module-level = partagés dans le process)
_survey_num: int = 0
_step_num: int = 0


def new_survey() -> None:
    """
    Démarre un nouveau contexte survey.
    Incrémente le numéro de survey, remet le step à 0.
    A appeler au début de chaque solve_full_survey().
    """
    global _survey_num, _step_num
    _survey_num += 1
    _step_num = 0


def _is_enabled() -> bool:
    return os.getenv("SNAP_ENABLED", "").strip() == "1"


def _get_account_id() -> str:
    try:
        from Management.guards.runtime_guard import get_guard
        guard = get_guard()
        return getattr(guard, "account_id", "unknown") or "unknown"
    except Exception:
        return "unknown"


def _build_client():
    import boto3
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

    Nommage : {account_id}/s{survey_num}_{step:03d}_{label}.png
    Le step est incremente automatiquement a chaque appel.

    - No-op silencieux si SNAP_ENABLED != "1".
    - Jamais bloquant : toute exception est loggee et avalee.
    """
    if not _is_enabled():
        return

    global _step_num

    try:
        from Survey.log_utils import log_info
    except ImportError:
        log_info = lambda tag, msg: print(f"[{tag}] {msg}", flush=True)

    try:
        _step_num += 1
        bucket = os.environ["SNAP_R2_BUCKET"]
        account_id = _get_account_id()
        # s0 = snaps hors survey (no_survey, demarrage) ; s1+ = surveys
        key = f"{account_id}/s{_survey_num}_{_step_num:03d}_{label}.png"

        client = _build_client()
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=png_bytes,
            ContentType="image/png",
        )
        log_info(_TAG, f"uploaded -> r2://{bucket}/{key}")

    except Exception as e:
        log_info(_TAG, f"[ERROR] upload failed label={label} : {type(e).__name__}: {e}")