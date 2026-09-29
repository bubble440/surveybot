from __future__ import annotations

"""
fleet_case_upload.py — Partie A (uploader) du transport fleet des failure_cases
vers la machine de dev (SURVEYBOT_AUTOFIX_PLAN.md, section « Outils
complémentaires » : « Le transport fleet lui-même ... reste à ce jour un
prompt rédigé mais NON implémenté »).

Tourne sur CHAQUE machine de production. Nouveau mécanisme de transport,
additif, sous Survey/ — ne touche à aucun extracteur, aucune stratégie de
dispatch, ni à Survey/autofix/failure_case_builder.py (Phase 2, non modifié). Lecture
seule sur le contenu déjà produit par cette phase (manifest.json,
artifacts/) : ce module ne le modifie jamais, il se contente de le lire pour
l'uploader, puis écrit un marqueur local dédié SIBLING de manifest.json
(fleet_upload_state.json, jamais dans artifacts/, jamais un champ ajouté à
manifest.json lui-même).

Conventions R2 vérifiées dans ce dépôt avant écriture, réutilisées telles
quelles (seul client R2 déjà existant : Management/snap_uploader.py — boto3,
API S3-compatible Cloudflare) :
  - construction du client : endpoint https://{account_id}.r2.cloudflarestorage.com,
    credentials exclusivement depuis l'environnement, region_name="auto".
  - résolution de l'identifiant de machine/bot à associer à chaque objet :
    Management.guards.runtime_guard.get_guard().account_id, repli "unknown"
    si le guard n'est pas initialisé ou l'import échoue (jamais une exception
    propagée) — même logique que Management/snap_uploader.py::_get_account_id,
    dupliquée ici à l'identique plutôt qu'importée (ce module ne dépend pas de
    SNAP_ENABLED/scrot, qui n'ont aucun rapport avec le transport de cases).
Nouvelles variables d'environnement dédiées à CE transport (namespace
FLEET_R2_*, jamais réutilisé celui de SNAP_R2_* qui pointe vers un bucket
distinct pour des screenshots — objets sans rapport) :
  FLEET_R2_ACCOUNT_ID / FLEET_R2_ACCESS_KEY_ID / FLEET_R2_SECRET_ACCESS_KEY /
  FLEET_R2_BUCKET — toutes requises, jamais de repli en dur (RÈGLES STRICTES).

Nommage des objets dans le bucket (même esprit que snap_uploader.py :
{account_id}/{session_id}/... — ici l'identifiant de machine/bot remplace la
session, le case_id remplace le couple survey/step) :
  {prefix}/{machine_id}/{case_id}/manifest.json
  {prefix}/{machine_id}/{case_id}/artifacts/...   (récursif, y compris frames/)

RÈGLES STRICTES appliquées :
  - Une seule stratégie de transport : un seul mécanisme d'upload
    (boto3 put_object), jamais de fallback empilé ; client construit avec
    retries={"max_attempts": 1} (pas de retry boto3 additionnel sur une
    stratégie déjà unique).
  - Budget de temps explicite par case (budget_s), vérifié par
    time.monotonic() (même mécanisme que
    Survey/autofix/extractor_integrity_gate.py::DEFAULT_TIME_BUDGET_S) ET par les
    timeouts connect/read du client boto3 lui-même — aucun appel réseau ne
    peut pendre indéfiniment, à aucun des deux niveaux.
  - Échec ou dépassement de budget d'un case n'empêche jamais les autres
    d'être tentés : toute exception est capturée par case, jamais propagée
    hors de la boucle d'upload_pending_cases().
  - Vérification positive et distincte après upload : chaque objet uploadé
    est revérifié par head_object avant que le case ne soit considéré
    « uploadé et vérifié ». Le marqueur local dédié (fleet_upload_state.json)
    n'est écrit QUE si cette vérification réussit pour la TOTALITÉ des
    objets du case — jamais avant, jamais sur la seule confiance dans le
    code de retour de put_object.
  - Nettoyage local (sous-partie A2) : ne supprime jamais un case dont le
    marqueur "uploadé et vérifié" est absent, quel que soit son âge. Chaque
    suppression est individuellement journalisée (jamais groupée/silencieuse).
"""

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.autofix.autofix_worktree import _is_safe_case_id
from Survey.log_utils import log_debug, log_info

_TAG = "[FLEET_UPLOAD]"
SCHEMA_VERSION = "1.0"

MARKER_FILENAME = "fleet_upload_state.json"
DEFAULT_PREFIX = "failure_cases"
DEFAULT_UPLOAD_TIMEOUT_S = 60.0
DEFAULT_RETENTION_DAYS = 7.0
_CALL_TIMEOUT_S = 20.0  # connect/read timeout par appel S3 individuel

_STATUS_UPLOADED_VERIFIED = "UPLOADED_VERIFIED"
_STATUS_UPLOAD_FAILED = "UPLOAD_FAILED"
_STATUS_VERIFY_FAILED = "VERIFY_FAILED"
_STATUS_TIMEOUT = "TIMEOUT"
_STATUS_SKIPPED_NO_FILES = "SKIPPED_NO_FILES"


class FleetUploadError(Exception):
    """Véritable erreur d'usage (racine introuvable, credentials R2
    manquantes) — jamais levée pour l'échec d'un case précis, qui reste
    local à ce case (cf. CaseUploadResult.status)."""


def _get_machine_id() -> str:
    """Identifiant de machine/bot à associer à chaque case uploadé — même
    résolution, même repli que Management/snap_uploader.py::_get_account_id."""
    try:
        from Management.guards.runtime_guard import get_guard
        guard = get_guard()
        return getattr(guard, "account_id", "unknown") or "unknown"
    except Exception:
        return "unknown"


def _build_client(*, call_timeout_s: float = _CALL_TIMEOUT_S):
    """Client boto3 S3-compatible R2 — même construction que
    Management/snap_uploader.py::_build_client. Import boto3 différé (comme
    snap_uploader.py) : pas de dépendance dure au moment de l'import de ce
    module. Une seule tentative par appel (max_attempts=1) : pas de retry
    boto3 empilé sur la stratégie de transport déjà unique de ce module."""
    import boto3
    from botocore.config import Config

    try:
        account_id = os.environ["FLEET_R2_ACCOUNT_ID"]
        access_key = os.environ["FLEET_R2_ACCESS_KEY_ID"]
        secret_key = os.environ["FLEET_R2_SECRET_ACCESS_KEY"]
    except KeyError as exc:
        raise FleetUploadError(
            f"variable d'environnement R2 manquante : {exc} — upload annulé "
            "(jamais de credentials en dur, RÈGLES STRICTES)."
        ) from None

    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(
            connect_timeout=min(10.0, call_timeout_s),
            read_timeout=call_timeout_s,
            retries={"max_attempts": 1},
        ),
    )


def _get_bucket() -> str:
    try:
        return os.environ["FLEET_R2_BUCKET"]
    except KeyError:
        raise FleetUploadError(
            "FLEET_R2_BUCKET non défini — credentials R2 incomplètes, upload annulé."
        ) from None


def _load_marker(case_dir: Path) -> "Optional[dict]":
    path = case_dir / MARKER_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_pending_case_ids(failure_cases_root: Path, *, warnings: list) -> "list[str]":
    """Cases sans marqueur "uploadé et vérifié" — jamais une supposition sur
    l'état distant, uniquement le marqueur local dédié."""
    if not failure_cases_root.is_dir():
        raise FleetUploadError(
            f"racine failure_cases introuvable ou n'est pas un dossier : {failure_cases_root}"
        )
    pending: list[str] = []
    for entry in sorted(failure_cases_root.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "manifest.json").is_file():
            warnings.append(f"{entry} : pas de manifest.json, ignoré (pas un case Phase 2)")
            continue
        if not _is_safe_case_id(entry.name):
            warnings.append(f"{entry.name} : case_id non sûr comme composant de clé R2, ignoré")
            continue
        marker = _load_marker(entry)
        if marker is not None and marker.get("verified") is True:
            continue
        pending.append(entry.name)
    return pending


def _case_files(case_dir: Path) -> "list[tuple[Path, str]]":
    """(chemin local, clé relative) pour manifest.json + tout artifacts/ (récursif,
    y compris frames/). Le marqueur local (MARKER_FILENAME) n'est jamais
    lui-même uploadé — il documente l'état LOCAL du transport, pas le contenu
    du case produit par la Phase 2."""
    files: list[tuple[Path, str]] = []
    manifest = case_dir / "manifest.json"
    if manifest.is_file():
        files.append((manifest, "manifest.json"))
    artifacts_dir = case_dir / "artifacts"
    if artifacts_dir.is_dir():
        for path in sorted(artifacts_dir.rglob("*")):
            if path.is_file():
                files.append((path, path.relative_to(case_dir).as_posix()))
    return files


def _object_key(prefix: str, machine_id: str, case_id: str, relpath: str) -> str:
    return f"{prefix}/{machine_id}/{case_id}/{relpath}"


@dataclass
class CaseUploadResult:
    case_id: str
    status: str
    files: int = 0
    error: "Optional[str]" = None


def upload_case(
    case_dir: Path,
    *,
    client,
    bucket: str,
    prefix: str,
    machine_id: str,
    budget_s: float,
) -> CaseUploadResult:
    """Upload puis vérifie (head_object) chaque fichier d'un case. Un
    dépassement du budget de temps, à l'upload comme à la vérification,
    interrompt CE case (jamais les autres, cf. upload_pending_cases)."""
    case_id = case_dir.name
    files = _case_files(case_dir)
    if not files:
        log_info(_TAG, f"{case_id} : aucun fichier à uploader (manifest.json absent), ignoré")
        return CaseUploadResult(case_id=case_id, status=_STATUS_SKIPPED_NO_FILES)

    deadline = time.monotonic() + budget_s
    uploaded_keys: list[str] = []

    for local_path, relpath in files:
        if time.monotonic() > deadline:
            log_info(
                _TAG,
                f"{case_id} : budget ({budget_s}s) dépassé pendant l'upload, "
                f"{len(files) - len(uploaded_keys)} fichier(s) non tenté(s)",
            )
            return CaseUploadResult(
                case_id=case_id, status=_STATUS_TIMEOUT, files=len(uploaded_keys),
                error=f"budget de {budget_s}s dépassé (upload)",
            )
        key = _object_key(prefix, machine_id, case_id, relpath)
        try:
            with open(local_path, "rb") as f:
                client.put_object(Bucket=bucket, Key=key, Body=f.read())
        except Exception as exc:
            log_info(_TAG, f"{case_id} : échec upload {relpath} : {type(exc).__name__}: {exc}")
            return CaseUploadResult(
                case_id=case_id, status=_STATUS_UPLOAD_FAILED, files=len(uploaded_keys),
                error=f"{relpath}: {exc}",
            )
        uploaded_keys.append(key)

    # Vérification positive et distincte — jamais une confiance dans le seul
    # code de retour de put_object (RÈGLES STRICTES).
    for key in uploaded_keys:
        if time.monotonic() > deadline:
            log_info(_TAG, f"{case_id} : budget ({budget_s}s) dépassé pendant la vérification")
            return CaseUploadResult(
                case_id=case_id, status=_STATUS_TIMEOUT, files=len(uploaded_keys),
                error=f"budget de {budget_s}s dépassé (vérification)",
            )
        try:
            client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            log_info(_TAG, f"{case_id} : vérification échouée pour {key} : {type(exc).__name__}: {exc}")
            return CaseUploadResult(
                case_id=case_id, status=_STATUS_VERIFY_FAILED, files=len(uploaded_keys),
                error=f"{key}: {exc}",
            )

    return CaseUploadResult(case_id=case_id, status=_STATUS_UPLOADED_VERIFIED, files=len(uploaded_keys))


def _write_marker(
    case_dir: Path, result: CaseUploadResult, *, machine_id: str, bucket: str, prefix: str
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    marker = {
        "schema_version": SCHEMA_VERSION,
        "case_id": result.case_id,
        "machine_id": machine_id,
        "bucket": bucket,
        "prefix": prefix,
        "uploaded_at": now,
        "verified_at": now,
        "verified": True,
        "files_uploaded": result.files,
    }
    (case_dir / MARKER_FILENAME).write_text(
        json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@dataclass
class UploadRunResult:
    generated_at: str
    machine_id: str
    candidates: int
    results: "list[CaseUploadResult]" = field(default_factory=list)
    warnings: "list[str]" = field(default_factory=list)


def upload_pending_cases(
    *,
    failure_cases_root: "str | Path" = "failure_cases",
    machine_id: "Optional[str]" = None,
    prefix: str = DEFAULT_PREFIX,
    budget_s: float = DEFAULT_UPLOAD_TIMEOUT_S,
    call_timeout_s: float = _CALL_TIMEOUT_S,
) -> UploadRunResult:
    """Upload tous les cases pas encore marqués "uploadé et vérifié". Une
    exception sur un case précis ne doit jamais empêcher les suivants
    d'être tentés (RÈGLES STRICTES)."""
    root = Path(failure_cases_root)
    warnings: list[str] = []
    case_ids = list_pending_case_ids(root, warnings=warnings)
    resolved_machine_id = machine_id or _get_machine_id()

    log_info(_TAG, f"{len(case_ids)} case(s) en attente d'upload (machine_id={resolved_machine_id})")

    bucket = _get_bucket()
    client = _build_client(call_timeout_s=call_timeout_s)

    results: list[CaseUploadResult] = []
    for case_id in case_ids:
        case_dir = root / case_id
        try:
            result = upload_case(
                case_dir,
                client=client,
                bucket=bucket,
                prefix=prefix,
                machine_id=resolved_machine_id,
                budget_s=budget_s,
            )
        except Exception as exc:
            log_info(_TAG, f"{case_id} : échec inattendu : {type(exc).__name__}: {exc}")
            result = CaseUploadResult(case_id=case_id, status=_STATUS_UPLOAD_FAILED, error=str(exc))

        if result.status == _STATUS_UPLOADED_VERIFIED:
            try:
                _write_marker(case_dir, result, machine_id=resolved_machine_id, bucket=bucket, prefix=prefix)
            except OSError as exc:
                warnings.append(f"{case_id} : marqueur non écrit ({exc}) — sera retenté au run suivant")
                log_debug(_TAG, f"marker write failed {case_dir}: {exc}")

        results.append(result)

    return UploadRunResult(
        generated_at=datetime.now(timezone.utc).isoformat(),
        machine_id=resolved_machine_id,
        candidates=len(case_ids),
        results=results,
        warnings=warnings,
    )


@dataclass
class CleanupResult:
    case_id: str
    removed: bool
    reason: "Optional[str]" = None
    uploaded_at: "Optional[str]" = None
    verified_at: "Optional[str]" = None


def _parse_iso(value: Any) -> "Optional[datetime]":
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cleanup_verified_cases(
    *,
    failure_cases_root: "str | Path" = "failure_cases",
    retention_days: float = DEFAULT_RETENTION_DAYS,
) -> "list[CleanupResult]":
    """Sous-partie A2 : supprime failure_cases/<case_id>/ pour chaque case
    dont le marqueur local porte verified=true ET dont verified_at dépasse
    retention_days. Ne supprime JAMAIS un case dont ce marqueur est absent
    ou verified=false, quel que soit son âge — jamais une suppression basée
    sur la seule ancienneté sans vérification positive préalable (RÈGLES
    STRICTES). Chaque suppression est individuellement journalisée."""
    root = Path(failure_cases_root)
    if not root.is_dir():
        raise FleetUploadError(
            f"racine failure_cases introuvable ou n'est pas un dossier : {root}"
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    results: list[CleanupResult] = []

    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        marker = _load_marker(entry)
        if marker is None or marker.get("verified") is not True:
            continue  # jamais de suppression sans vérification positive préalable

        verified_at = _parse_iso(marker.get("verified_at"))
        if verified_at is None:
            log_debug(_TAG, f"{entry.name} : verified_at illisible, rétention non calculable, conservé")
            continue
        if verified_at > cutoff:
            continue  # pas encore assez ancien

        try:
            shutil.rmtree(entry)
        except OSError as exc:
            log_info(_TAG, f"{entry.name} : suppression échouée ({exc})")
            results.append(CleanupResult(case_id=entry.name, removed=False, reason=str(exc)))
            continue

        log_info(
            _TAG,
            f"{entry.name} : supprimé (rétention {retention_days}j dépassée, "
            f"uploaded_at={marker.get('uploaded_at')} verified_at={marker.get('verified_at')})",
        )
        results.append(
            CleanupResult(
                case_id=entry.name,
                removed=True,
                uploaded_at=marker.get("uploaded_at"),
                verified_at=marker.get("verified_at"),
            )
        )

    return results
