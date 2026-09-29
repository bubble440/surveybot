from __future__ import annotations

"""
fleet_case_import.py — Partie B (importeur) du transport fleet des
failure_cases, tourne sur la machine de dev. Contrepartie de
Survey/autofix/fleet_case_upload.py (Partie A) : télécharge depuis le stockage R2
partagé les cases pas encore présents localement, sous
failure_cases/<case_id>/, exactement comme la Phase 2
(Survey/autofix/failure_case_builder.py::build_failure_case) les produit
(manifest.json + artifacts/) — jamais de structure de dossier différente.

Réutilise directement (jamais réimplémenté) le client R2 et la résolution de
credentials de Survey/autofix/fleet_case_upload.py::_build_client/_get_bucket : même
bucket, mêmes variables d'environnement FLEET_R2_* — « Une seule stratégie de
transport (upload/download) » (RÈGLES STRICTES) implique un seul point de
construction du client des deux côtés, pas deux implémentations qui
pourraient diverger. case_id est revalidé avec
Survey.autofix.autofix_worktree._is_safe_case_id (même garde-fou que la Phase 7 :
un case_id doit rester un composant de chemin local sûr) avant toute
écriture sur disque — les clés distantes proviennent normalement des seules
machines de la fleet, mais un case_id est ici une donnée relue depuis un
stockage partagé, jamais supposée sûre par construction.

N'écrit jamais dans manifest.json ni dans aucun autre fichier déjà produit
par la Phase 2 : le seul artefact propre à ce module est un fichier annexe
SIBLING, fleet_origin.json (machine d'origine, horodatage d'upload/d'import,
« live_validation_possible »: false — la Phase 10 exige déjà un
--cdp-endpoint explicite que personne ne peut fournir pour un case dont la
machine d'origine n'est pas physiquement/réseau accessible depuis la
machine de dev ; ce marqueur documente cette impossibilité structurelle
sans empêcher techniquement une tentative). L'horodatage d'upload est lu
depuis le champ LastModified réel de l'objet manifest.json côté R2 (donnée
serveur vérifiable), jamais un horodatage auto-déclaré transporté séparément.

Refus explicite (jamais un écrasement silencieux) si un case_id existe déjà
localement, sauf --force explicite. Un case dont le téléchargement échoue ou
dépasse son budget de temps est entièrement nettoyé localement (même
principe que Survey/autofix/failure_case_builder.py : « un case partiellement écrit
est pire qu'aucun case ») — jamais un case à moitié présent qui laisserait
croire à un import réussi.

Cet import ne déclenche lui-même AUCUNE suppression côté stockage partagé ni
côté machine d'origine : le nettoyage prod reste entièrement du ressort de
Survey/autofix/fleet_case_upload.py::cleanup_verified_cases (Partie A2), sur son
propre délai de rétention, indépendant du moment où le dev importe.
"""

import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from Survey.autofix.autofix_worktree import _is_safe_case_id
from Survey.autofix.fleet_case_upload import (
    DEFAULT_PREFIX,
    SCHEMA_VERSION,
    _CALL_TIMEOUT_S,
    _build_client,
    _get_bucket,
)
from Survey.autofix.fleet_case_upload import FleetUploadError as _R2ConfigError
from Survey.log_utils import log_debug, log_info

_TAG = "[FLEET_IMPORT]"

ORIGIN_FILENAME = "fleet_origin.json"
DEFAULT_DOWNLOAD_TIMEOUT_S = 60.0
_MAX_LISTED_OBJECTS = 200_000  # garde-fou, même esprit que update_checker.py::_MAX_ZIP_ENTRIES

_STATUS_IMPORTED = "IMPORTED"
_STATUS_SKIPPED_EXISTS = "SKIPPED_EXISTS"
_STATUS_SKIPPED_AMBIGUOUS_ORIGIN = "SKIPPED_AMBIGUOUS_ORIGIN"
_STATUS_FAILED = "FAILED"
_STATUS_TIMEOUT = "TIMEOUT"


class FleetImportError(Exception):
    """Véritable erreur d'usage (credentials R2 manquantes, racine locale non
    créable) — jamais levée pour l'échec d'un case précis, qui reste local à
    ce case (cf. CaseImportResult.status)."""


def _build_client_or_raise(call_timeout_s: float):
    try:
        return _build_client(call_timeout_s=call_timeout_s)
    except _R2ConfigError as exc:
        raise FleetImportError(str(exc)) from exc


def _get_bucket_or_raise() -> str:
    try:
        return _get_bucket()
    except _R2ConfigError as exc:
        raise FleetImportError(str(exc)) from exc


def _parse_key(key: str, prefix: str) -> "Optional[tuple[str, str, str]]":
    """clé -> (machine_id, case_id, relpath), ou None si la clé ne respecte
    pas la structure {prefix}/{machine_id}/{case_id}/{relpath} attendue
    (jamais deviné : une clé inattendue est ignorée, pas interprétée)."""
    prefix_full = prefix.rstrip("/") + "/"
    if not key.startswith(prefix_full):
        return None
    remainder = key[len(prefix_full):]
    parts = remainder.split("/", 2)
    if len(parts) != 3:
        return None
    machine_id, case_id, relpath = parts
    if not machine_id or not case_id or not relpath:
        return None
    return machine_id, case_id, relpath


def _list_remote_cases(client, *, bucket: str, prefix: str) -> "tuple[dict, list[str]]":
    """Énumère les objets sous prefix/ et les regroupe par case_id. Un
    case_id porté par plus d'un machine_id distinct (ne devrait jamais
    arriver vu la structure de clé, mais jamais supposé impossible) est
    marqué ambigu — jamais deviné quelle origine retenir."""
    warnings: list[str] = []
    cases: "dict[str, dict]" = {}

    paginator = client.get_paginator("list_objects_v2")
    total = 0
    truncated = False
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for obj in page.get("Contents", []):
            total += 1
            if total > _MAX_LISTED_OBJECTS:
                truncated = True
                break
            parsed = _parse_key(obj["Key"], prefix)
            if parsed is None:
                continue
            machine_id, case_id, relpath = parsed
            entry = cases.setdefault(
                case_id, {"machine_id": machine_id, "files": {}, "ambiguous": False}
            )
            if entry["machine_id"] != machine_id:
                entry["ambiguous"] = True
            entry["files"][relpath] = (obj["Key"], obj.get("LastModified"))
        if truncated:
            break

    if truncated:
        warnings.append(
            f"plus de {_MAX_LISTED_OBJECTS} objets distants sous {prefix}/ — listing tronqué"
        )

    return cases, warnings


@dataclass
class CaseImportResult:
    case_id: str
    status: str
    machine_id: "Optional[str]" = None
    files: int = 0
    error: "Optional[str]" = None


def _download_case(
    client, *, bucket: str, case_id: str, machine_id: str, files: dict, local_dir: Path, budget_s: float
) -> CaseImportResult:
    deadline = time.monotonic() + budget_s
    local_dir.mkdir(parents=True, exist_ok=False)

    manifest_last_modified = None
    downloaded = 0
    try:
        for relpath, (key, last_modified) in sorted(files.items()):
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"budget de {budget_s}s dépassé ({downloaded}/{len(files)} fichier(s))"
                )
            target = local_dir / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            resp = client.get_object(Bucket=bucket, Key=key)
            target.write_bytes(resp["Body"].read())
            downloaded += 1
            if relpath == "manifest.json":
                manifest_last_modified = last_modified
    except TimeoutError as exc:
        log_info(_TAG, f"{case_id} : {exc} — case nettoyé localement (partiel, jamais conservé tel quel)")
        shutil.rmtree(local_dir, ignore_errors=True)
        return CaseImportResult(
            case_id=case_id, status=_STATUS_TIMEOUT, machine_id=machine_id, files=downloaded, error=str(exc)
        )
    except Exception as exc:
        log_info(_TAG, f"{case_id} : échec téléchargement : {type(exc).__name__}: {exc}")
        shutil.rmtree(local_dir, ignore_errors=True)
        return CaseImportResult(
            case_id=case_id, status=_STATUS_FAILED, machine_id=machine_id, files=downloaded,
            error=f"{type(exc).__name__}: {exc}",
        )

    if not (local_dir / "manifest.json").is_file():
        log_info(_TAG, f"{case_id} : manifest.json absent du case distant — import refusé")
        shutil.rmtree(local_dir, ignore_errors=True)
        return CaseImportResult(
            case_id=case_id, status=_STATUS_FAILED, machine_id=machine_id, files=downloaded,
            error="manifest.json absent du case distant",
        )

    uploaded_at = manifest_last_modified.isoformat() if manifest_last_modified else None
    origin = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "machine_id": machine_id,
        "uploaded_at": uploaded_at,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "live_validation_possible": False,
    }
    (local_dir / ORIGIN_FILENAME).write_text(
        json.dumps(origin, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log_info(_TAG, f"{case_id} : importé ({downloaded} fichier(s), origine machine_id={machine_id})")
    return CaseImportResult(case_id=case_id, status=_STATUS_IMPORTED, machine_id=machine_id, files=downloaded)


def import_available_cases(
    *,
    failure_cases_root: "str | Path" = "failure_cases",
    prefix: str = DEFAULT_PREFIX,
    force: bool = False,
    budget_s: float = DEFAULT_DOWNLOAD_TIMEOUT_S,
    call_timeout_s: float = _CALL_TIMEOUT_S,
) -> "tuple[list[CaseImportResult], list[str]]":
    """Liste les cases disponibles côté stockage partagé et télécharge ceux
    pas encore présents localement (refus explicite sinon, sauf force=True).
    Ne déclenche AUCUNE suppression côté stockage partagé ni côté machine
    d'origine. Un échec sur un case précis n'empêche jamais les autres
    d'être tentés."""
    root = Path(failure_cases_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FleetImportError(f"racine locale failure_cases non créable : {root} ({exc})") from exc

    bucket = _get_bucket_or_raise()
    client = _build_client_or_raise(call_timeout_s)

    remote_cases, warnings = _list_remote_cases(client, bucket=bucket, prefix=prefix)
    log_info(_TAG, f"{len(remote_cases)} case(s) distant(s) trouvé(s) sous {prefix}/")

    results: list[CaseImportResult] = []
    for case_id in sorted(remote_cases):
        info = remote_cases[case_id]

        if not _is_safe_case_id(case_id):
            warnings.append(f"{case_id} : case_id non sûr comme composant de chemin local, ignoré")
            continue

        if info["ambiguous"]:
            results.append(
                CaseImportResult(
                    case_id=case_id, status=_STATUS_SKIPPED_AMBIGUOUS_ORIGIN,
                    error="plusieurs machine_id distincts pour ce case_id — origine ambiguë, jamais devinée",
                )
            )
            continue

        machine_id = info["machine_id"]
        local_dir = root / case_id
        if local_dir.exists():
            if not force:
                results.append(
                    CaseImportResult(
                        case_id=case_id, status=_STATUS_SKIPPED_EXISTS, machine_id=machine_id,
                        error="case_id déjà présent localement (utiliser --force pour écraser)",
                    )
                )
                continue
            log_debug(_TAG, f"{case_id} : écrasement forcé (--force) d'un case local existant")
            shutil.rmtree(local_dir)

        try:
            result = _download_case(
                client, bucket=bucket, case_id=case_id, machine_id=machine_id,
                files=info["files"], local_dir=local_dir, budget_s=budget_s,
            )
        except Exception as exc:
            log_info(_TAG, f"{case_id} : échec inattendu : {type(exc).__name__}: {exc}")
            shutil.rmtree(local_dir, ignore_errors=True)
            result = CaseImportResult(case_id=case_id, status=_STATUS_FAILED, machine_id=machine_id, error=str(exc))

        results.append(result)

    return results, warnings
