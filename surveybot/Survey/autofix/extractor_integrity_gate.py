from __future__ import annotations

"""Phase 11-A : compare la baseline Git, le code à base_sha et le patch.

Le registre et l'algorithme de hash à base_sha sont la référence de confiance.
Une déclaration explicite hors du worktree peut autoriser un changement direct
du core, sous réserve du diagnostic confirmé et du rejeu du patch. Le résultat
reste soumis à la revue humaine ultérieure. Aucune baseline n'est réécrite ici.
La partie 11-B (rejeu DOM de régression) reste différée.
"""

import importlib.util
import json
import math
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info
from Survey.autofix.static_validator import StaticValidationError, _resolve_package_root

_TAG = "[EXTRACTOR_INTEGRITY_GATE]"
SCHEMA_VERSION = "2.0"
UNCHANGED = "UNCHANGED"
EXPECTED_CHANGE = "EXPECTED_CHANGE"
UNEXPECTED_CHANGE = "UNEXPECTED_CHANGE"
BASELINE_MISMATCH = "BASELINE_MISMATCH"
_SHA = re.compile(r"[0-9a-fA-F]{40,64}\Z")

# Budget de temps unique pour le parcours du registre — au-delà, abandon
# contrôlé (entrées restantes comptées en erreur), jamais un blocage illimité.
DEFAULT_TIME_BUDGET_S = 30.0
MAX_REGISTRY_ENTRIES = 4096
MAX_GIT_BLOB_BYTES = 8 * 1024 * 1024
MAX_BASE_SOURCE_BYTES = 64 * 1024 * 1024


class IntegrityGateError(Exception):
    """Refus contrôlé — précondition manquante (worktree, validation statique,
    registre absent/illisible). Jamais un effet de bord partiel."""


class IntegrityGateExistsError(IntegrityGateError):
    """La sortie cible existe déjà et la régénération n'a pas été demandée."""


class IntegrityGateBudgetError(IntegrityGateError):
    """Une source Git dépasse le budget explicite de lecture."""


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"{path} illisible ({exc})"
    except ValueError as exc:  # json.JSONDecodeError est une sous-classe de ValueError
        return None, f"{path} JSON invalide ({exc})"


@dataclass
class PreconditionResult:
    satisfied: bool
    case_id: Optional[str]
    reasons: "list[str]" = field(default_factory=list)
    worktree: Optional[dict] = None
    package_root: Optional[Path] = None


def check_preconditions(
    *,
    worktree_manifest_path: "str | Path",
    validation_static_path: "str | Path",
) -> PreconditionResult:
    """Vérifie toutes les conditions ensemble ; ne s'arrête jamais à la
    première raison rencontrée (même principe que Survey/autofix/autofix_worktree.py::
    check_eligibility et Survey/autofix/patch_replay.py::check_preconditions). Lecture
    seule : ne recalcule ni la Phase 7 ni la Phase 8.

    worktree_manifest_path/validation_static_path sont les CHEMINS COMPLETS
    vers worktree.json/validation_static.json (pas un dossier qui les
    contiendrait) — même convention que les façades CLI des Phases 8/9/10."""
    worktree_manifest_path = Path(worktree_manifest_path)
    validation_static_path = Path(validation_static_path)
    reasons: "list[str]" = []

    validation, validation_err = _load_json(validation_static_path)
    if validation_err or not isinstance(validation, dict):
        reasons.append(f"validation_static.json (Phase 8) {validation_err or 'ne contient pas un objet JSON'}")
    elif validation.get("verdict") != "ACCEPTED":
        reasons.append(
            f"validation_static.json.verdict={validation.get('verdict')!r} — \"ACCEPTED\" requis "
            "(la Phase 8 doit avoir accepté le patch avant toute vérification d'intégrité ; jamais "
            "recalculé ici)"
        )

    worktree, worktree_err = _load_json(worktree_manifest_path)
    package_root: Optional[Path] = None
    if worktree_err or not isinstance(worktree, dict):
        reasons.append(f"worktree.json (Phase 7) {worktree_err or 'ne contient pas un objet JSON'}")
    else:
        for key in ("case_id", "branch", "worktree_path", "base_sha"):
            if not worktree.get(key):
                reasons.append(f"worktree.json.{key} absent ou vide")
        wt_path = worktree.get("worktree_path")
        if isinstance(wt_path, str) and wt_path:
            wt_dir = Path(wt_path)
            if not wt_dir.is_dir():
                reasons.append(f"worktree_path {wt_path!r} n'existe pas ou n'est pas un dossier")
            elif not (wt_dir / ".git").exists():
                reasons.append(f"worktree_path {wt_path!r} ne ressemble pas à un worktree Git (.git absent)")
            else:
                try:
                    package_root = _resolve_package_root(wt_dir)
                except StaticValidationError as exc:
                    reasons.append(str(exc))

    if isinstance(worktree, dict) and isinstance(validation, dict):
        for key in ("branch", "base_sha"):
            if validation.get(key) and validation[key] != worktree.get(key):
                reasons.append(f"{key} incohérent entre worktree.json et validation_static.json")

    if package_root is not None:
        integrity_py = package_root / "Survey" / "extractor_integrity.py"
        integrity_json = package_root / "Survey" / "extractor_integrity.json"
        if not integrity_py.is_file():
            reasons.append(
                f"Survey/extractor_integrity.py introuvable dans le worktree résolu : {integrity_py} "
                "— refus explicite plutôt qu'une racine devinée"
            )
        if not integrity_json.is_file():
            reasons.append(
                f"Survey/extractor_integrity.json introuvable dans le worktree résolu : {integrity_json} "
                "— refus explicite plutôt qu'une racine devinée"
            )

    case_ids = {
        "worktree.json": str(worktree.get("case_id") or "") if isinstance(worktree, dict) else "",
        "validation_static.json": str(validation.get("case_id") or "") if isinstance(validation, dict) else "",
        "worktree_manifest_path.parent": worktree_manifest_path.parent.name,
        "validation_static_path.parent": validation_static_path.parent.name,
    }
    distinct = set(case_ids.values())
    resolved_case_id: Optional[str] = None
    if len(distinct) != 1 or "" in distinct:
        reasons.append(f"case_id incohérent entre les sources : {case_ids}")
    else:
        resolved_case_id = next(iter(distinct))
        if any(sep in resolved_case_id for sep in ("/", "\\")) or ".." in resolved_case_id:
            reasons.append(f"case_id {resolved_case_id!r} n'est pas un composant de chemin sûr")

    return PreconditionResult(
        satisfied=not reasons,
        case_id=resolved_case_id,
        reasons=reasons,
        worktree=worktree if isinstance(worktree, dict) else None,
        package_root=package_root,
    )


def _load_base_extractor_integrity(integrity_py_path: Path):
    """Charge l'algorithme de hash de base_sha, hors du worktree patché."""
    module_name = f"_extractor_integrity_base_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, integrity_py_path)
    if spec is None or spec.loader is None:
        raise IntegrityGateError(f"impossible de charger {integrity_py_path} comme module Python")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # jamais un chargement partiel silencieux
        raise IntegrityGateError(
            f"échec de chargement de {integrity_py_path} : {type(exc).__name__}: {exc}"
        ) from exc

    if not hasattr(module, "_load_registry") or not hasattr(module, "_hash_function"):
        raise IntegrityGateError(
            f"{integrity_py_path} ne définit pas _load_registry/_hash_function attendues — "
            "fonctions gelées introuvables, jamais de réimplémentation de repli"
        )
    return module


def _git(repo: Path, *args: str) -> bytes:
    if args and args[0] == "show":
        try:
            size = int(_git(repo, "cat-file", "-s", args[1]).strip())
        except (ValueError, IndexError) as exc:
            raise IntegrityGateError("taille de blob Git invalide") from exc
        if size > MAX_GIT_BLOB_BYTES:
            raise IntegrityGateBudgetError("blob Git supérieur au budget de lecture")
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IntegrityGateError(f"lecture Git impossible : {type(exc).__name__}") from exc
    if proc.returncode:
        raise IntegrityGateError(
            f"lecture Git impossible ({' '.join(args[:2])}) : {proc.stderr.decode('utf-8', 'replace').strip()[:300]}"
        )
    return proc.stdout


def _git_path(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as exc:
        raise IntegrityGateError(f"chemin hors du worktree Git : {path}") from exc


def _source_path(repo: Path, survey_dir: Path, file_name: str) -> tuple[Path, str]:
    if (
        not file_name.endswith(".py") or "\\" in file_name or ":" in file_name
        or file_name.startswith("/") or "\x00" in file_name
    ):
        raise IntegrityGateError(f"chemin de fonction protégée invalide : {file_name!r}")
    path = (survey_dir / file_name).resolve()
    return path, _git_path(repo, path)


def _expected_declarations(
    expected_changes_path: "str | Path | None", diagnosis_path: "str | Path | None",
    patch_replay_path: "str | Path | None", *, case_id: str, base_sha: str,
    branch: str, worktree_dir: Path, registry: dict,
) -> tuple[dict[str, dict], list[dict]]:
    """Une intention locale explicite n'est valide qu'avec les preuves Phase 4/9."""
    if expected_changes_path is None:
        return {}, []
    path = Path(expected_changes_path)
    errors: list[dict] = []
    try:
        path.resolve().relative_to(worktree_dir.resolve())
    except ValueError:
        pass
    else:
        return {}, [{"key": "<expected_changes>", "reason": "déclaration dans le worktree patché"}]
    data, err = _load_json(path)
    if err or not isinstance(data, dict):
        return {}, [{"key": "<expected_changes>", "reason": err or "déclaration JSON invalide"}]
    if data.get("case_id") != case_id or data.get("base_sha") != base_sha:
        errors.append({"key": "<expected_changes>", "reason": "case_id ou base_sha incohérent"})
    if data.get("schema_version") != "1.0":
        errors.append({"key": "<expected_changes>", "reason": "schema_version de déclaration invalide"})
    changes = data.get("changes")
    if not isinstance(changes, list) or not 0 < len(changes) <= len(registry):
        errors.append({"key": "<expected_changes>", "reason": "changes absent, vide ou non borné"})
        changes = []
    declarations: dict[str, dict] = {}
    for item in changes:
        key = item.get("function_id") if isinstance(item, dict) else None
        if not isinstance(key, str) or key not in registry or key in declarations:
            errors.append({"key": "<expected_changes>", "reason": "fonction inconnue ou dupliquée"})
            continue
        if not _SHA.fullmatch(str(item.get("base_hash", ""))) or not _SHA.fullmatch(str(item.get("patched_hash", ""))):
            errors.append({"key": key, "reason": "hash d'intention invalide"})
            continue
        declarations[key] = item
    for label, evidence_path in (("diagnosis", diagnosis_path), ("patch_replay", patch_replay_path)):
        if evidence_path is not None:
            try:
                Path(evidence_path).resolve().relative_to(worktree_dir.resolve())
            except ValueError:
                pass
            else:
                errors.append({"key": "<expected_changes>", "reason": f"preuve {label} dans le worktree patché"})
    diagnosis, diag_err = _load_json(Path(diagnosis_path)) if diagnosis_path else (None, "diagnostic non fourni")
    replay, replay_err = _load_json(Path(patch_replay_path)) if patch_replay_path else (None, "rejeu non fourni")
    diagnosis_replay = diagnosis.get("replay") if isinstance(diagnosis, dict) else None
    replay_worktree = replay.get("worktree") if isinstance(replay, dict) else None
    if diag_err or not isinstance(diagnosis, dict) or (
        diagnosis.get("case_id") != case_id or diagnosis.get("confidence_global") != "certain"
        or diagnosis.get("case_incomplete") is not False
        or diagnosis.get("stage") not in ("extraction", "action")
        or not isinstance(diagnosis_replay, dict) or diagnosis_replay.get("verdict") != "REPRODUIT"
    ):
        errors.append({"key": "<expected_changes>", "reason": "diagnostic confirmé du même case absent ou invalide"})
    if replay_err or not isinstance(replay, dict) or (
        replay.get("case_id") != case_id or replay.get("refused") is not False
        or replay.get("stage") != (diagnosis.get("stage") if isinstance(diagnosis, dict) else None)
        or replay.get("outcome") != "CORRECTIF_CONFIRME"
        or replay.get("patch_validated") is not True
        or not isinstance(replay_worktree, dict)
        or replay_worktree.get("base_sha") != base_sha
        or replay_worktree.get("branch") != branch
        or Path(replay_worktree.get("path") or "").resolve() != worktree_dir.resolve()
    ):
        errors.append({"key": "<expected_changes>", "reason": "rejeu confirmé sur ce worktree absent ou invalide"})
    return (declarations if not errors else {}), errors


@dataclass
class IntegrityCheckResult:
    case_id: str
    branch: str
    base_sha: str
    registry_path: str
    total_entries: int
    checked_entries: int
    functions: "list[dict]" = field(default_factory=list)
    mismatches: "list[dict]" = field(default_factory=list)
    errors: "list[dict]" = field(default_factory=list)
    budget_exceeded: bool = False
    warnings: "list[str]" = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return "ACCEPTED" if not self.errors and all(
            row["state"] in (UNCHANGED, EXPECTED_CHANGE) for row in self.functions
        ) and self.checked_entries == self.total_entries else "REJECTED"

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "branch": self.branch,
            "base_sha": self.base_sha,
            "registry_path": self.registry_path,
            "total_entries": self.total_entries,
            "checked_entries": self.checked_entries,
            "functions": self.functions,
            "state_counts": {
                state: sum(row["state"] == state for row in self.functions)
                for state in (UNCHANGED, EXPECTED_CHANGE, UNEXPECTED_CHANGE, BASELINE_MISMATCH)
            },
            "mismatches": self.mismatches,
            "errors": self.errors,
            "budget_exceeded": self.budget_exceeded,
            "verdict": self.verdict,
            "review_required": any(row["state"] == EXPECTED_CHANGE for row in self.functions),
            "warnings": self.warnings,
        }


def check_extractor_integrity(
    worktree_manifest_path: "str | Path",
    validation_static_path: "str | Path",
    *,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    expected_changes_path: "str | Path | None" = None,
    diagnosis_path: "str | Path | None" = None,
    patch_replay_path: "str | Path | None" = None,
) -> IntegrityCheckResult:
    """Vérifie l'intégrité des fonctions gelées du worktree décrit par
    worktree_manifest_path/validation_static_path. Lève IntegrityGateError si
    une précondition manque — jamais un résultat partiel dans ce cas."""
    precondition = check_preconditions(
        worktree_manifest_path=worktree_manifest_path,
        validation_static_path=validation_static_path,
    )
    if not precondition.satisfied:
        raise IntegrityGateError(
            "préconditions non satisfaites pour la vérification d'intégrité (Phase 11, partie A) : "
            + " ; ".join(precondition.reasons)
        )

    if not math.isfinite(time_budget_s) or time_budget_s <= 0:
        raise IntegrityGateError("time_budget_s doit être fini et positif")
    worktree = precondition.worktree or {}
    package_root = precondition.package_root
    if package_root is None:
        # Ne peut pas arriver si satisfied=True, mais évite toute hypothèse fragile.
        raise IntegrityGateError("racine de paquet du worktree non résolue malgré des préconditions satisfaites")

    survey_dir = package_root / "Survey"
    integrity_py = survey_dir / "extractor_integrity.py"
    integrity_json = survey_dir / "extractor_integrity.json"

    worktree_dir = Path(worktree["worktree_path"]).resolve()
    repo = Path(_git(worktree_dir, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if repo != worktree_dir:
        raise IntegrityGateError("worktree_path ne désigne pas la racine du worktree Git")
    base_sha = str(worktree["base_sha"])
    if not _SHA.fullmatch(base_sha):
        raise IntegrityGateError("base_sha invalide")
    resolved_sha = _git(repo, "rev-parse", "--verify", f"{base_sha}^{{commit}}").decode().strip()
    if resolved_sha.lower() != base_sha.lower():
        raise IntegrityGateError("base_sha ne désigne pas le commit immuable attendu")
    _git(repo, "merge-base", "--is-ancestor", base_sha, "HEAD")
    branch = _git(repo, "symbolic-ref", "--short", "HEAD").decode().strip()
    if branch != str(worktree["branch"]):
        raise IntegrityGateError("branche Git du worktree incohérente avec worktree.json")
    registry_git_path = _git_path(repo, integrity_json)
    algorithm_git_path = _git_path(repo, integrity_py)
    base_registry_bytes = _git(repo, "show", f"{base_sha}:{registry_git_path}")
    base_algorithm_bytes = _git(repo, "show", f"{base_sha}:{algorithm_git_path}")
    warnings: "list[str]" = []
    mismatches: "list[dict]" = []
    errors: "list[dict]" = []
    functions: "list[dict]" = []
    checked = 0
    budget_exceeded = False
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="surveybot-integrity-") as temporary:
        temp_root = Path(temporary)
        base_survey = temp_root / Path(_git_path(repo, survey_dir))
        base_survey.mkdir(parents=True)
        (base_survey / "extractor_integrity.py").write_bytes(base_algorithm_bytes)
        (base_survey / "extractor_integrity.json").write_bytes(base_registry_bytes)
        module = _load_base_extractor_integrity(base_survey / "extractor_integrity.py")
        try:
            registry = module._load_registry(base_survey)
        except Exception as exc:
            raise IntegrityGateError(f"registre à base_sha illisible : {type(exc).__name__}") from exc
        if not isinstance(registry, dict) or not 0 < len(registry) <= MAX_REGISTRY_ENTRIES:
            raise IntegrityGateError("registre à base_sha vide, malformé ou trop grand")
        total_entries = len(registry)
        current_registry, current_err = _load_json(integrity_json)
        if current_err or current_registry != registry:
            errors.append({"key": "<registry>", "reason": "registre patché absent, illisible ou différent de celui à base_sha"})
        try:
            current_algorithm = integrity_py.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            base_algorithm = base_algorithm_bytes.decode("utf-8-sig").replace("\r\n", "\n")
            if current_algorithm != base_algorithm:
                errors.append({"key": "<hash_algorithm>", "reason": "extractor_integrity.py modifié par le patch"})
        except (OSError, UnicodeError):
            errors.append({"key": "<hash_algorithm>", "reason": "extractor_integrity.py illisible"})
        declarations, declaration_errors = _expected_declarations(
            expected_changes_path, diagnosis_path, patch_replay_path,
            case_id=precondition.case_id or "", base_sha=base_sha, branch=branch,
            worktree_dir=worktree_dir, registry=registry,
        )
        errors.extend(declaration_errors)
        cached_sources: dict[str, bool] = {}
        total_base_source_bytes = 0
        keys = sorted(registry)
        for index, key in enumerate(keys):
            if time.monotonic() - start > time_budget_s:
                budget_exceeded = True
                for remaining_key in keys[index:]:
                    errors.append({"key": remaining_key, "reason": "budget_exceeded : fonction non vérifiée"})
                break
            checked += 1
            entry = registry[key]
            if not isinstance(entry, dict) or not _SHA.fullmatch(str(entry.get("hash", ""))) or key.count("::") != 1:
                errors.append({"key": key, "reason": "entrée de baseline invalide"})
                continue
            file_name, function_name = key.split("::", 1)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", function_name):
                errors.append({"key": key, "reason": "nom de fonction invalide"})
                continue
            try:
                _, git_path = _source_path(repo, survey_dir, file_name)
            except IntegrityGateError as exc:
                errors.append({"key": key, "reason": str(exc)})
                continue
            if git_path not in cached_sources:
                try:
                    source_bytes = _git(repo, "show", f"{base_sha}:{git_path}")
                except IntegrityGateBudgetError as exc:
                    errors.append({"key": key, "reason": str(exc)})
                    continue
                except IntegrityGateError:
                    cached_sources[git_path] = False
                else:
                    total_base_source_bytes += len(source_bytes)
                    if total_base_source_bytes > MAX_BASE_SOURCE_BYTES:
                        errors.append({"key": key, "reason": "budget cumulé des sources Git dépassé"})
                        for remaining_key in keys[index + 1:]:
                            errors.append({"key": remaining_key, "reason": "budget_exceeded : fonction non vérifiée"})
                        budget_exceeded = True
                        break
                    temp_file = temp_root / git_path
                    temp_file.parent.mkdir(parents=True, exist_ok=True)
                    temp_file.write_bytes(source_bytes)
                    cached_sources[git_path] = True
            base_hash = None
            base_error = None
            if not cached_sources[git_path]:
                base_error = "fichier absent à base_sha"
            else:
                try:
                    base_hash = module._hash_function(base_survey, file_name, function_name)
                except Exception as exc:
                    base_error = f"fonction illisible à base_sha : {type(exc).__name__}"
            patched_hash = None
            patch_error = None
            try:
                patched_hash = module._hash_function(survey_dir, file_name, function_name)
            except Exception as exc:
                patch_error = f"fonction patchée introuvable ou illisible : {type(exc).__name__}"
            baseline_hash = entry["hash"]
            if base_hash != baseline_hash:
                state = BASELINE_MISMATCH
            elif patched_hash == base_hash:
                state = UNCHANGED
            else:
                declaration = declarations.get(key)
                state = EXPECTED_CHANGE if (
                    declaration and declaration["base_hash"] == base_hash
                    and declaration["patched_hash"] == patched_hash and patched_hash is not None
                ) else UNEXPECTED_CHANGE
            row = {
                "key": key, "state": state, "baseline_hash": baseline_hash,
                "base_hash": base_hash, "patched_hash": patched_hash,
                "changed_by_patch": base_hash != patched_hash,
            }
            if state == EXPECTED_CHANGE:
                row["evidence"] = {
                    "expected_changes_path": str(Path(expected_changes_path).resolve()),
                    "diagnosis_path": str(Path(diagnosis_path).resolve()),
                    "patch_replay_path": str(Path(patch_replay_path).resolve()),
                }
            if base_error:
                row["base_error"] = base_error
            if patch_error:
                row["patch_error"] = patch_error
            functions.append(row)
            if state != UNCHANGED:
                mismatches.append({
                    "key": key, "state": state, "expected_hash": baseline_hash,
                    "base_hash": base_hash, "actual_hash": patched_hash,
                })
        unused_declarations = sorted(set(declarations) - {
            row["key"] for row in functions if row["state"] == EXPECTED_CHANGE
        })
        for key in unused_declarations:
            errors.append({"key": key, "reason": "déclaration de changement attendu non concordante"})

    return IntegrityCheckResult(
        case_id=precondition.case_id or "",
        branch=branch,
        base_sha=base_sha,
        registry_path=str(integrity_json),
        total_entries=total_entries,
        checked_entries=checked,
        functions=functions,
        mismatches=mismatches,
        errors=errors,
        budget_exceeded=budget_exceeded,
        warnings=warnings,
    )


def _output_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "extractor_integrity_check.json"


def write_extractor_integrity_check(
    worktree_manifest_path: "str | Path",
    validation_static_path: "str | Path",
    *,
    out_root: "str | Path" = "extractor_integrity_checks",
    force: bool = False,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    expected_changes_path: "str | Path | None" = None,
    diagnosis_path: "str | Path | None" = None,
    patch_replay_path: "str | Path | None" = None,
) -> Path:
    """Exécute check_extractor_integrity et écrit le résultat sous
    out_root/<case_id>/extractor_integrity_check.json. Lève
    IntegrityGateExistsError si la sortie existe déjà et force=False — jamais
    d'écrasement silencieux. Ne modifie rien d'autre (jamais le worktree, la
    branche autofix, ni un artefact d'une phase précédente)."""
    worktree_manifest_path = Path(worktree_manifest_path)
    manifest, err = _load_json(worktree_manifest_path)
    if err or not isinstance(manifest, dict):
        raise IntegrityGateError(f"worktree.json (Phase 7) {err or 'ne contient pas un objet JSON'}")
    case_id = str(manifest.get("case_id") or "")
    if not case_id:
        raise IntegrityGateError(f"worktree.json (Phase 7) sans case_id exploitable : {worktree_manifest_path}")

    out_root = Path(out_root)
    out_dir, out_file = _output_paths(out_root, case_id)

    if out_dir.exists():
        if not force:
            raise IntegrityGateExistsError(
                f"vérification d'intégrité déjà existante : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise IntegrityGateError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de extractor_integrity_check.json) — suppression refusée, vérifier manuellement"
            )
        log_debug(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    result = check_extractor_integrity(
        worktree_manifest_path, validation_static_path, time_budget_s=time_budget_s,
        expected_changes_path=expected_changes_path, diagnosis_path=diagnosis_path,
        patch_replay_path=patch_replay_path,
    )

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_info(
        _TAG,
        f"vérification d'intégrité case={result.case_id} verdict={result.verdict} "
        f"entrées={result.checked_entries}/{result.total_entries} "
        f"mismatches={len(result.mismatches)} erreurs={len(result.errors)} -> {out_file}",
    )
    return out_file
