from __future__ import annotations

"""Phase 11, partie A — vérification d'intégrité des fonctions "gelées" (BEM)
d'un patch autofix déjà validé statiquement par la Phase 8, avant tout rejeu
(Phase 9) ou toute validation live (Phase 10).

Complément déterministe à Survey/extractor_integrity.py (hash SHA256 par
fonction protégée) : ce module ne réimplémente jamais sa logique de hash, il
l'exécute telle quelle sur le code du worktree autofix, pour vérifier qu'un
patch généré automatiquement n'a pas modifié, supprimé ni renommé une
fonction listée comme protégée — au même titre qu'une erreur de compilation
ou de lint (Phase 8), un motif de rejet explicite, jamais un passe-droit
silencieux.

── Portée explicitement limitée à cette Partie A ─────────────────────────────
Le rejeu de DOM historiques/génériques représentatifs (regression_cases/,
décrit dans Utils/SURVEYBOT_AUTOFIX_PLAN.md pour la Phase 11) reste un
sous-chantier différé — aucune bibliothèque de cas n'est encore curée pour
l'alimenter. Ce module ne construit que la vérification de hash (partie A),
à la manière des sous-chantiers déjà différés en Phase 9 (rejeu de cas
historiques voisins) et Phase 10 (garde-fou de fermeture de page CDP en
stage="action").

── Préconditions, toutes ensemble, jamais la première seule ─────────────────
- worktree.json (Phase 7, Survey/autofix/autofix_worktree.py::write_worktree_manifest) :
  case_id/branch/worktree_path/base_sha déjà produits, jamais recalculés.
  worktree_path doit exister et ressembler à un worktree Git (.git présent) —
  même vérification que Phase 9/10 (Survey/autofix/patch_replay.py,
  Survey/autofix/live_validator.py).
- validation_static.json (Phase 8, Survey/autofix/static_validator.py) :
  verdict="ACCEPTED" requis, jamais recalculé — un patch qui ne compile même
  pas n'a pas besoin d'être vérifié pour intégrité.
- case_id cohérent entre les deux sources et les noms de dossiers fournis.
- Survey/extractor_integrity.py ET Survey/extractor_integrity.json doivent
  exister réellement dans le worktree, à la racine de paquet résolue
  (Survey.autofix.static_validator._resolve_package_root, Phase 8, réutilisée telle
  quelle — le worktree est un clone complet du dépôt, Survey/ et tools/ y
  vivent au même endroit relatif que dans le dépôt principal) — sinon refus
  explicite plutôt qu'une racine devinée.

── Chargement du code gelé DU WORKTREE, jamais du dépôt principal ────────────
Survey/extractor_integrity.py est chargé dynamiquement depuis le fichier du
worktree (importlib.util.spec_from_file_location, sous un nom de module
dédié — jamais "Survey.extractor_integrity" — pour ne jamais lire un module
déjà en cache dans sys.modules provenant du dépôt principal ; même
précaution que Phase 9/10 avec Survey.autofix.replay_browser/Survey.autofix.failure_replay,
mais résolue ici SANS sous-processus : extractor_integrity.py n'a aucune
dépendance de paquet (stdlib seulement — argparse/ast/hashlib/json/sys/
pathlib), un chargement direct suffit et est exigé par cette phase (jamais
un sous-processus avec parsing de texte). Ses fonctions _load_registry/
_hash_function sont appelées TELLES QUELLES ensuite, jamais réimplémentées.

Le registre est chargé depuis Survey/extractor_integrity.json DU WORKTREE
(le patch a pu légitimement y ajouter des entrées) — jamais une copie du
dépôt principal.

── Budget explicite, une seule stratégie ─────────────────────────────────────
Le registre est aujourd'hui restreint (une cinquantaine d'entrées) mais son
parcours (une lecture disque + un parsing AST par entrée) reste borné par un
budget de temps explicite (DEFAULT_TIME_BUDGET_S) : au-delà, les entrées
restantes ne sont pas vérifiées et comptent comme des erreurs explicites
("budget_exceeded"), jamais un passe-droit silencieux sur ce qui n'a pas pu
être vérifié.

── Verdict ────────────────────────────────────────────────────────────────
Toute anomalie compte comme un rejet : un hash différent (mismatch, fonction
protégée modifiée) et une fonction/fichier introuvable (error, fonction
protégée supprimée/renommée) sont toutes deux des motifs de rejet, avec le
détail exact de chaque cas. Verdict tracé sous
out_root/<case_id>/extractor_integrity_check.json, même convention JSON que
les phases précédentes (schema_version/case_id/created_at). Ne modifie
jamais le worktree, la branche autofix, ni aucun artefact d'une phase
précédente.
"""

import importlib.util
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info
from Survey.autofix.static_validator import StaticValidationError, _resolve_package_root

_TAG = "[EXTRACTOR_INTEGRITY_GATE]"
SCHEMA_VERSION = "1.0"

# Budget de temps unique pour le parcours du registre — au-delà, abandon
# contrôlé (entrées restantes comptées en erreur), jamais un blocage illimité.
DEFAULT_TIME_BUDGET_S = 30.0


class IntegrityGateError(Exception):
    """Refus contrôlé — précondition manquante (worktree, validation statique,
    registre absent/illisible). Jamais un effet de bord partiel."""


class IntegrityGateExistsError(IntegrityGateError):
    """La sortie cible existe déjà et la régénération n'a pas été demandée."""


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


def _load_worktree_extractor_integrity(integrity_py_path: Path):
    """Charge Survey/extractor_integrity.py DEPUIS LE WORKTREE dynamiquement,
    sous un nom de module dédié — jamais "Survey.extractor_integrity", pour
    ne jamais entrer en collision avec un éventuel import déjà en cache dans
    sys.modules provenant du dépôt principal. _load_registry/_hash_function
    sont ensuite appelées telles quelles : aucune réimplémentation, aucun
    sous-processus avec parsing de texte."""
    module_name = f"_extractor_integrity_worktree_{uuid.uuid4().hex}"
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


@dataclass
class IntegrityCheckResult:
    case_id: str
    branch: str
    base_sha: str
    registry_path: str
    total_entries: int
    checked_entries: int
    mismatches: "list[dict]" = field(default_factory=list)
    errors: "list[dict]" = field(default_factory=list)
    budget_exceeded: bool = False
    warnings: "list[str]" = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return "ACCEPTED" if not self.mismatches and not self.errors else "REJECTED"

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
            "mismatches": self.mismatches,
            "errors": self.errors,
            "budget_exceeded": self.budget_exceeded,
            "verdict": self.verdict,
            "warnings": self.warnings,
        }


def check_extractor_integrity(
    worktree_manifest_path: "str | Path",
    validation_static_path: "str | Path",
    *,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
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

    worktree = precondition.worktree or {}
    package_root = precondition.package_root
    if package_root is None:
        # Ne peut pas arriver si satisfied=True, mais évite toute hypothèse fragile.
        raise IntegrityGateError("racine de paquet du worktree non résolue malgré des préconditions satisfaites")

    survey_dir = package_root / "Survey"
    integrity_py = survey_dir / "extractor_integrity.py"
    integrity_json = survey_dir / "extractor_integrity.json"

    module = _load_worktree_extractor_integrity(integrity_py)

    try:
        registry = module._load_registry(survey_dir)
    except Exception as exc:
        raise IntegrityGateError(
            f"échec de lecture du registre {integrity_json} via _load_registry (worktree) : "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(registry, dict):
        raise IntegrityGateError(f"{integrity_json} ne contient pas un objet JSON exploitable comme registre")

    warnings: "list[str]" = []
    total_entries = len(registry)
    if total_entries == 0:
        warnings.append("registre d'intégrité vide — rien à vérifier (jamais un motif de rejet en soi)")

    mismatches: "list[dict]" = []
    errors: "list[dict]" = []
    checked = 0
    budget_exceeded = False

    start = time.monotonic()
    keys = sorted(registry)
    for index, key in enumerate(keys):
        if time.monotonic() - start > time_budget_s:
            remaining = keys[index:]
            budget_exceeded = True
            log_debug(
                _TAG,
                f"budget de temps ({time_budget_s}s) dépassé, {len(remaining)} entrée(s) non vérifiée(s)",
            )
            for remaining_key in remaining:
                errors.append({
                    "key": remaining_key,
                    "reason": f"budget_exceeded : non vérifié, budget de {time_budget_s}s dépassé",
                })
            break

        entry = registry.get(key)
        if not isinstance(entry, dict) or "hash" not in entry:
            errors.append({"key": key, "reason": "entrée de registre malformée (attendu {'hash': ...})"})
            checked += 1
            continue

        if "::" not in key:
            errors.append({"key": key, "reason": "clé de registre invalide (attendu 'fichier.py::fonction')"})
            checked += 1
            continue

        file_name, function_name = key.split("::", 1)
        expected_hash = entry["hash"]

        log_debug(_TAG, f"vérification {key}")
        try:
            current_hash = module._hash_function(survey_dir, file_name, function_name)
        except FileNotFoundError:
            errors.append({
                "key": key, "file": file_name, "function": function_name,
                "reason": "fichier introuvable — fonction protégée potentiellement supprimée/déplacée",
            })
        except LookupError:
            errors.append({
                "key": key, "file": file_name, "function": function_name,
                "reason": "fonction introuvable dans le fichier — renommée ou supprimée",
            })
        except Exception as exc:  # jamais un passe-droit silencieux sur une erreur inattendue
            errors.append({
                "key": key, "file": file_name, "function": function_name,
                "reason": f"erreur inattendue lors du calcul du hash : {type(exc).__name__}: {exc}",
            })
        else:
            if current_hash != expected_hash:
                mismatches.append({
                    "key": key, "file": file_name, "function": function_name,
                    "expected_hash": expected_hash, "actual_hash": current_hash,
                })
        checked += 1

    return IntegrityCheckResult(
        case_id=precondition.case_id or "",
        branch=str(worktree.get("branch") or ""),
        base_sha=str(worktree.get("base_sha") or ""),
        registry_path=str(integrity_json),
        total_entries=total_entries,
        checked_entries=checked,
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
