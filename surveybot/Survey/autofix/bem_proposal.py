from __future__ import annotations

"""Phase 14 — proposition automatique (brouillon, jamais collée automatiquement)
d'une entrée BOT_EVOLUTION_MEMORY.md pour un patch autofix déjà approuvé par un
humain (Phase 13). Nouveau module, en LECTURE SEULE sur les artefacts déjà
produits par les phases précédentes (worktree.json Phase 7, diagnosis.json
Phase 4, context_selection.json Phase 5, confidence_score.json Phase 12,
decision.json Phase 13) et sur le worktree Git isolé lui-même (jamais modifié).
N'écrit JAMAIS dans BOT_EVOLUTION_MEMORY.md — seulement un brouillon markdown
séparé (bem_proposals/<case_id>/bem_entry_proposal.md), destiné à une relecture
humaine puis un collage manuel. Ne touche à aucun extracteur, aucune stratégie
de dispatch, ni à Survey/extractor_integrity.py (jamais importé, jamais
modifié) ; ne recalcule/ne relance rien des Phases 2 à 13.

── Précondition : toutes les conditions ensemble, jamais la première seule ───
decision.json (Phase 13, human_reviews/<case_id>/) doit exister et porter
decision="APPROVED" exactement — REJECTED, ou Phase 13 jamais déclenchée
(decision.json absent), refusent explicitement, jamais une proposition.
worktree.json (Phase 7), diagnosis.json (Phase 4), context_selection.json
(Phase 5) doivent exister, être lisibles et cohérents en case_id — tous requis.
confidence_score.json (Phase 12) est en pratique déjà garanti exister à ce
stade : Phase 13 (Survey/autofix/human_review.py::check_review_eligibility) exige déjà
confidence="HIGH" avant de pouvoir seulement notifier, donc avant que
decision.json puisse exister — vérifié ici avec la même rigueur que les trois
autres artefacts (même garantie structurelle), pas une précondition
supplémentaire inventée : son champ "confidence" est un des faits explicitement
attendus dans le brouillon.

── Étape 1 : fichiers réellement modifiés (réutilisation stricte) ────────────
Survey/autofix/static_validator.py::_git_changed_paths / _filter_existing_python_files /
_resolve_package_root (Phase 8) sont importées et réutilisées TELLES QUELLES
(jamais réimplémentées) pour déterminer, entre base_sha (worktree.json) et
l'état courant du worktree, l'ensemble des fichiers .py réellement modifiés
existant encore sur disque. Limite héritée assumée : un fichier .py SUPPRIMÉ
par le patch est bien détecté par git mais filtré par
_filter_existing_python_files (réutilisée sans modification) — ses fonctions
ne sont donc jamais énumérées comme "supprimées", signalé explicitement en
avertissement, jamais masqué.

── Étape 2 : fonctions top-level ajoutées/modifiées/supprimées ───────────────
Même technique que Survey/extractor_integrity.py::_find_function_source (AST,
segment source exact décorateurs inclus, hash SHA256) — mais
Survey/extractor_integrity.py n'est ni importé ni modifié : une nouvelle
fonction d'énumération (_enumerate_top_level_functions), qui liste TOUTES les
fonctions top-level (module) et méthodes de classes top-level d'un fichier
(jamais les fonctions/classes imbriquées), est écrite dans ce module car rien
d'équivalent n'existe déjà. Comparaison entre le contenu à base_sha (git show,
budget de temps explicite) et le contenu courant du worktree. Stratégie
unique, sans repli textuel : un fichier qui ne parse pas (AST) d'un côté ou de
l'autre est signalé explicitement en avertissement ("échec du parsing AST"),
ses fonctions ne sont jamais devinées.

── Étape 3 : croisement avec context_selection.json (Phase 5) ────────────────
Pour chaque fichier réellement modifié : si anticipé par la Phase 5
(code_files/always_included), sa "reason" est reprise telle quelle comme
contexte. Sinon, signalé explicitement ("non anticipé par la sélection de
contexte"), jamais omis silencieusement.

── Étape 4 : brouillon markdown ──────────────────────────────────────────────
Format repris EXACTEMENT de l'en-tête de BOT_EVOLUTION_MEMORY.md (### nom,
Fichier, Bug corrigé, Correction, Patterns couverts, Patterns exclus,
Diagnostic associé, Statut). Rempli uniquement avec des faits déjà établis par
les phases précédentes (fichiers/fonctions réellement modifiés,
symptom.failure_types et cause.justification de diagnosis.json, confidence de
confidence_score.json, case_id et chemins des artefacts pour traçabilité).
"Patterns couverts"/"Patterns exclus" exigent un jugement humain : jamais une
prose inventée, toujours un marqueur explicite "[À COMPLÉTER — ...]".

── Sortie ──────────────────────────────────────────────────────────────────
bem_proposals/<case_id>/bem_entry_proposal.md (brouillon, jamais collé
automatiquement) + bem_proposals/<case_id>/bem_proposal.json (traçabilité,
même convention schema_version/case_id/created_at que les phases précédentes).
Refus explicite (jamais un écrasement silencieux) si une sortie existe déjà
pour ce case sans force=True.
"""

import ast
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info
from Survey.autofix.static_validator import (
    StaticValidationError,
    _filter_existing_python_files,
    _git_changed_paths,
    _resolve_package_root,
)

_TAG = "[BEM_PROPOSAL]"
SCHEMA_VERSION = "1.0"

DEFAULT_GIT_TIMEOUT_S = 15.0

_MISSING_JUDGEMENT_MARKER = (
    "[À COMPLÉTER — nécessite un jugement humain sur les DOM réellement couverts]"
)

# Composant de chemin unique, allowlist conservatrice — même garde-fou dupliqué
# volontairement dans plusieurs modules indépendants de ce pipeline (cf.
# Survey/autofix/autofix_worktree.py, Survey/autofix/human_review.py).
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class BemProposalError(Exception):
    """Refus contrôlé — inéligibilité, artefact illisible, ou échec Git/AST."""


class BemProposalExistsError(BemProposalError):
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


def _is_safe_case_id(case_id: str) -> bool:
    if not case_id or not _CASE_ID_RE.match(case_id):
        return False
    if ".." in case_id:
        return False
    if case_id.lower() in _WINDOWS_RESERVED_NAMES:
        return False
    return True


# ─────────────────────────────── Éligibilité ─────────────────────────────────

@dataclass
class BemEligibilityResult:
    eligible: bool
    case_id: Optional[str]
    reasons: "list[str]" = field(default_factory=list)


def check_bem_proposal_eligibility(
    *,
    human_review_dir: "str | Path",
    worktree_dir: "str | Path",
    diagnosis_dir: "str | Path",
    context_selection_dir: "str | Path",
    confidence_score_dir: "str | Path",
) -> BemEligibilityResult:
    """Vérifie toutes les conditions ensemble ; ne s'arrête jamais à la première
    raison de refus rencontrée — soit toutes les conditions tiennent, soit la
    liste complète des raisons de refus est renvoyée."""
    human_review_dir = Path(human_review_dir)
    worktree_dir = Path(worktree_dir)
    diagnosis_dir = Path(diagnosis_dir)
    context_selection_dir = Path(context_selection_dir)
    confidence_score_dir = Path(confidence_score_dir)
    reasons: "list[str]" = []

    decision, dec_err = _load_json(human_review_dir / "decision.json")
    if dec_err or not isinstance(decision, dict):
        reasons.append(f"decision.json {dec_err or 'ne contient pas un objet JSON'}")

    worktree, wt_err = _load_json(worktree_dir / "worktree.json")
    if wt_err or not isinstance(worktree, dict):
        reasons.append(f"worktree.json {wt_err or 'ne contient pas un objet JSON'}")

    diagnosis, diag_err = _load_json(diagnosis_dir / "diagnosis.json")
    if diag_err or not isinstance(diagnosis, dict):
        reasons.append(f"diagnosis.json {diag_err or 'ne contient pas un objet JSON'}")

    context_selection, cs_err = _load_json(context_selection_dir / "context_selection.json")
    if cs_err or not isinstance(context_selection, dict):
        reasons.append(f"context_selection.json {cs_err or 'ne contient pas un objet JSON'}")

    confidence_score, cf_err = _load_json(confidence_score_dir / "confidence_score.json")
    if cf_err or not isinstance(confidence_score, dict):
        reasons.append(f"confidence_score.json {cf_err or 'ne contient pas un objet JSON'}")

    # Les vérifications ci-dessous nécessitent les cinq artefacts valides ;
    # sans eux, on s'arrête ici plutôt que de spéculer sur des données absentes.
    if reasons:
        return BemEligibilityResult(eligible=False, case_id=None, reasons=reasons)

    decision_value = str(decision.get("decision") or "")
    if decision_value != "APPROVED":
        reasons.append(
            f"decision.json : decision={decision_value!r} — seul \"APPROVED\" exact déclenche "
            "une proposition (REJECTED, ou Phase 13 jamais déclenchée, n'en génèrent jamais)"
        )

    case_ids = {
        "decision.json": str(decision.get("case_id") or ""),
        "worktree.json": str(worktree.get("case_id") or ""),
        "diagnosis.json": str(diagnosis.get("case_id") or ""),
        "context_selection.json": str(context_selection.get("case_id") or ""),
        "confidence_score.json": str(confidence_score.get("case_id") or ""),
        "human_review_dir": human_review_dir.name,
        "worktree_dir": worktree_dir.name,
        "diagnosis_dir": diagnosis_dir.name,
        "context_selection_dir": context_selection_dir.name,
        "confidence_score_dir": confidence_score_dir.name,
    }
    distinct = set(case_ids.values())
    if len(distinct) != 1 or "" in distinct:
        reasons.append(f"case_id incohérent entre les sources : {case_ids}")
        resolved_case_id = None
    else:
        resolved_case_id = next(iter(distinct))
        if not _is_safe_case_id(resolved_case_id):
            reasons.append(
                f"case_id {resolved_case_id!r} n'est pas utilisable sans transformation comme "
                "composant de chemin"
            )

    return BemEligibilityResult(eligible=not reasons, case_id=resolved_case_id, reasons=reasons)


# ───────────────────────── Fichiers/fonctions modifiés ───────────────────────

def _run_git(args: "list[str]", *, cwd: Path, timeout: float) -> "tuple[bool, str, str, bool]":
    """Une seule stratégie d'exécution de sous-processus, jamais de retry.
    Retourne (ok, stdout, stderr, timed_out)."""
    log_debug(_TAG, f"git {' '.join(args)} (cwd={cwd}, timeout={timeout}s)")
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired:
        return False, "", f"dépassement du budget ({timeout}s)", True
    except OSError as exc:
        return False, "", f"exécution impossible : {exc}", False


def _git_show_at(worktree_path: Path, base_sha: str, git_rel_posix: str, *, timeout: float) -> "Optional[str]":
    """Contenu du fichier à base_sha, ou None si absent à cette révision
    (interprété comme "fichier nouveau créé par le patch" — pas nécessairement
    une erreur ; stratégie unique, sans distinction plus fine)."""
    ok, out, _err, timed_out = _run_git(
        ["show", f"{base_sha}:{git_rel_posix}"], cwd=worktree_path, timeout=timeout,
    )
    if timed_out:
        raise BemProposalError(f"git show {base_sha}:{git_rel_posix} a dépassé son budget ({timeout}s)")
    if not ok:
        return None
    return out


def _enumerate_top_level_functions(source: str) -> "dict[str, str]":
    """Liste TOUTES les fonctions top-level (module) et méthodes de classes
    top-level d'un fichier — même technique que Survey/extractor_integrity.py::
    _find_function_source (segment source exact, décorateurs inclus), généralisée
    à une énumération complète plutôt qu'une recherche par nom. Ne descend jamais
    dans les fonctions/classes imbriquées (pas une clôture, pas une méthode de
    classe elle-même imbriquée)."""
    tree = ast.parse(source)
    lines = source.splitlines()

    def _segment(node: "ast.FunctionDef | ast.AsyncFunctionDef") -> str:
        start_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
        return "\n".join(lines[start_line - 1:node.end_lineno])

    result: "dict[str, str]" = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = _segment(node)
        elif isinstance(node, ast.ClassDef):
            for member in ast.iter_child_nodes(node):
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result[f"{node.name}.{member.name}"] = _segment(member)
    return result


def _hash_all(functions: "dict[str, str]") -> "dict[str, str]":
    return {name: hashlib.sha256(segment.encode("utf-8")).hexdigest() for name, segment in functions.items()}


@dataclass
class ChangedFile:
    git_relative_path: str
    package_relative_path: "Optional[str]"
    added: "list[str]" = field(default_factory=list)
    removed: "list[str]" = field(default_factory=list)
    modified: "list[str]" = field(default_factory=list)
    parse_error: "Optional[str]" = None
    context_selection_reason: "Optional[str]" = None
    anticipated_by_context_selection: bool = False

    def as_dict(self) -> dict:
        return {
            "git_relative_path": self.git_relative_path,
            "package_relative_path": self.package_relative_path,
            "functions": {"added": self.added, "removed": self.removed, "modified": self.modified},
            "parse_error": self.parse_error,
            "context_selection_reason": self.context_selection_reason,
            "anticipated_by_context_selection": self.anticipated_by_context_selection,
        }


def _detect_changed_files(worktree: dict, *, git_timeout_s: float) -> "tuple[list[ChangedFile], list[str]]":
    """Étapes 1+2 : fichiers réellement modifiés (réutilisation stricte de
    Survey/autofix/static_validator.py) puis fonctions top-level ajoutées/modifiées/
    supprimées, par comparaison base_sha <-> worktree (git show + AST)."""
    warnings: "list[str]" = []
    worktree_path = Path(str(worktree.get("worktree_path") or ""))
    base_sha = str(worktree.get("base_sha") or "")
    if not worktree_path.is_dir():
        raise BemProposalError(f"worktree_path (worktree.json, Phase 7) introuvable sur disque : {worktree_path}")
    if not base_sha:
        raise BemProposalError("worktree.json (Phase 7) sans base_sha exploitable")

    try:
        # Même contrat Phase 8 que patch_commit : seule la liste complète des
        # chemins modifiés alimente l'analyse AST des fonctions du patch.
        changed_rel, _added_rel = _git_changed_paths(worktree_path, base_sha, timeout=git_timeout_s)
    except StaticValidationError as exc:
        raise BemProposalError(
            "détection des fichiers modifiés (Survey/autofix/static_validator.py::_git_changed_paths, "
            f"réutilisée telle quelle) échouée : {exc}"
        ) from exc

    changed_abs = _filter_existing_python_files(worktree_path, changed_rel)

    # Limite héritée assumée (cf. docstring du module) : un fichier .py supprimé
    # par le patch est détecté par git mais filtré par _filter_existing_python_files
    # (réutilisée sans modification) — ses fonctions supprimées ne sont jamais
    # énumérées ici, signalé explicitement plutôt que masqué.
    kept_git_rel = {p.relative_to(worktree_path).as_posix() for p in changed_abs}
    for rel in changed_rel:
        if rel.endswith(".py") and rel not in kept_git_rel:
            warnings.append(
                f"fichier {rel} modifié/supprimé par le patch mais absent du worktree — fonctions "
                "supprimées non énumérées (limite de _filter_existing_python_files, Phase 8, "
                "réutilisée telle quelle)"
            )

    if not changed_abs:
        warnings.append(
            "aucun fichier .py modifié détecté entre base_sha et l'état courant du worktree autofix"
        )
        return [], warnings

    try:
        package_root = _resolve_package_root(worktree_path)
    except StaticValidationError as exc:
        raise BemProposalError(
            "résolution de la racine de paquet (Survey/autofix/static_validator.py::_resolve_package_root, "
            f"réutilisée telle quelle) échouée : {exc}"
        ) from exc

    results: "list[ChangedFile]" = []
    for abs_file in changed_abs:
        git_rel = abs_file.relative_to(worktree_path).as_posix()
        try:
            pkg_rel = abs_file.relative_to(package_root).as_posix()
        except ValueError:
            warnings.append(
                f"fichier {git_rel} hors de la racine de paquet détectée ({package_root}) — non "
                "rattachable à context_selection.json, fonctions non énumérées"
            )
            results.append(ChangedFile(git_relative_path=git_rel, package_relative_path=None))
            continue

        try:
            worktree_source = abs_file.read_text(encoding="utf-8-sig")
            worktree_funcs = _enumerate_top_level_functions(worktree_source)
        except (OSError, SyntaxError, ValueError) as exc:
            msg = f"échec du parsing AST du worktree pour {git_rel} : {exc}"
            warnings.append(msg)
            results.append(ChangedFile(git_relative_path=git_rel, package_relative_path=pkg_rel, parse_error=msg))
            continue

        base_content = _git_show_at(worktree_path, base_sha, git_rel, timeout=git_timeout_s)
        base_funcs: "Optional[dict[str, str]]" = None
        if base_content is not None:
            try:
                base_funcs = _enumerate_top_level_functions(base_content)
            except (SyntaxError, ValueError) as exc:
                msg = f"échec du parsing AST de la version base_sha de {git_rel} : {exc}"
                warnings.append(msg)
                results.append(ChangedFile(git_relative_path=git_rel, package_relative_path=pkg_rel, parse_error=msg))
                continue

        wt_hashes = _hash_all(worktree_funcs)
        base_hashes = _hash_all(base_funcs) if base_funcs is not None else {}

        added = sorted(k for k in wt_hashes if k not in base_hashes)
        removed = sorted(k for k in base_hashes if k not in wt_hashes) if base_funcs is not None else []
        modified = sorted(k for k in wt_hashes if k in base_hashes and wt_hashes[k] != base_hashes[k])

        results.append(ChangedFile(
            git_relative_path=git_rel, package_relative_path=pkg_rel,
            added=added, removed=removed, modified=modified,
        ))

    return results, warnings


def _cross_reference_context_selection(files: "list[ChangedFile]", context_selection: dict) -> "list[str]":
    """Étape 3 : reprend telle quelle la reason de la Phase 5 pour chaque
    fichier réellement modifié déjà anticipé ; signale explicitement les
    autres, jamais une omission silencieuse."""
    reasons_by_file: "dict[str, str]" = {}
    for entry in (context_selection.get("code_files") or []):
        if isinstance(entry, dict) and entry.get("file"):
            reasons_by_file[str(entry["file"])] = str(entry.get("reason") or "")
    for entry in (context_selection.get("always_included") or []):
        if isinstance(entry, dict) and entry.get("file"):
            reasons_by_file.setdefault(str(entry["file"]), str(entry.get("reason") or ""))

    warnings: "list[str]" = []
    for cf in files:
        if cf.package_relative_path is None:
            continue
        reason = reasons_by_file.get(cf.package_relative_path)
        if reason is not None:
            cf.context_selection_reason = reason
            cf.anticipated_by_context_selection = True
        else:
            cf.anticipated_by_context_selection = False
            warnings.append(
                f"fichier {cf.package_relative_path} modifié mais non anticipé par la sélection de "
                "contexte (Phase 5, context_selection.json)"
            )
    return warnings


# ────────────────────────────── Brouillon markdown ───────────────────────────

def _compose_markdown(
    *, case_id: str, files: "list[ChangedFile]", diagnosis: dict, confidence_score: dict,
    artifact_paths: "dict[str, str]",
) -> str:
    symptom = diagnosis.get("symptom") if isinstance(diagnosis.get("symptom"), dict) else {}
    failure_types = symptom.get("failure_types") or []
    failure_types_line = ", ".join(str(x) for x in failure_types) if failure_types else "non documenté"

    cause = diagnosis.get("cause") if isinstance(diagnosis.get("cause"), dict) else {}
    cause_line = str(cause.get("justification") or "non documentée")

    title_candidates = sorted({name for cf in files for name in (*cf.added, *cf.modified)})
    title = ", ".join(title_candidates) if title_candidates else (
        f"case {case_id} (fonctions non déterminées — voir avertissements)"
    )

    fichier_lines = "\n".join(
        f"- {cf.package_relative_path or cf.git_relative_path}"
        + (" (parsing AST en échec, cf. avertissements)" if cf.parse_error else "")
        for cf in files
    ) or "(aucun fichier détecté)"

    correction_blocks = []
    for cf in files:
        label = cf.package_relative_path or cf.git_relative_path
        lines = [f"- {label}"]
        if cf.parse_error:
            lines.append(f"  - {cf.parse_error}")
        else:
            if cf.added:
                lines.append(f"  - Fonctions ajoutées : {', '.join(cf.added)}")
            if cf.modified:
                lines.append(f"  - Fonctions modifiées : {', '.join(cf.modified)}")
            if cf.removed:
                lines.append(f"  - Fonctions supprimées : {', '.join(cf.removed)}")
            if not (cf.added or cf.modified or cf.removed):
                lines.append("  - Aucune fonction top-level ajoutée/modifiée/supprimée détectée")
        if cf.anticipated_by_context_selection:
            lines.append(f"  - Contexte (Phase 5) : {cf.context_selection_reason}")
        else:
            lines.append("  - [NON ANTICIPÉ PAR LA SÉLECTION DE CONTEXTE — Phase 5]")
        correction_blocks.append("\n".join(lines))
    correction_text = "\n".join(correction_blocks) or "(aucun fichier détecté)"

    confidence = str(confidence_score.get("confidence") or "non disponible")

    diagnostic_lines = "\n".join([
        f"case_id : {case_id}",
        f"confidence (Phase 12) : {confidence}",
        f"diagnosis.json : {artifact_paths['diagnosis']}",
        f"context_selection.json : {artifact_paths['context_selection']}",
        f"worktree.json : {artifact_paths['worktree']}",
        f"confidence_score.json : {artifact_paths['confidence_score']}",
        f"decision.json : {artifact_paths['decision']}",
    ])

    return (
        f"### {title}\n"
        f"Fichier :\n{fichier_lines}\n"
        f"Bug corrigé :\n"
        f"Failure_types : {failure_types_line}\n"
        f"Cause : {cause_line}\n"
        f"Correction :\n{correction_text}\n"
        f"Patterns couverts :\n{_MISSING_JUDGEMENT_MARKER}\n"
        f"Patterns exclus :\n{_MISSING_JUDGEMENT_MARKER}\n"
        f"Diagnostic associé :\n{diagnostic_lines}\n"
        f"Statut : patch validé (Phase 13, décision humaine)\n"
    )


# ────────────────────────────────── Orchestration ────────────────────────────

@dataclass
class BemProposalResult:
    case_id: str
    files: "list[ChangedFile]"
    warnings: "list[str]"
    draft_markdown: str

    def as_dict(self, *, draft_path: str) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "changed_files": [cf.as_dict() for cf in self.files],
            "warnings": self.warnings,
            "draft_path": draft_path,
        }


def generate_bem_proposal(
    *,
    human_review_dir: "str | Path",
    worktree_dir: "str | Path",
    diagnosis_dir: "str | Path",
    context_selection_dir: "str | Path",
    confidence_score_dir: "str | Path",
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> BemProposalResult:
    """Calcule la proposition BEM à partir des artefacts déjà produits par les
    Phases 4, 5, 7, 12 et 13. Lève BemProposalError si le case n'est pas
    éligible (cf. check_bem_proposal_eligibility) ou si la détection Git/AST
    échoue — jamais une proposition partielle."""
    human_review_dir = Path(human_review_dir)
    worktree_dir = Path(worktree_dir)
    diagnosis_dir = Path(diagnosis_dir)
    context_selection_dir = Path(context_selection_dir)
    confidence_score_dir = Path(confidence_score_dir)

    eligibility = check_bem_proposal_eligibility(
        human_review_dir=human_review_dir,
        worktree_dir=worktree_dir,
        diagnosis_dir=diagnosis_dir,
        context_selection_dir=context_selection_dir,
        confidence_score_dir=confidence_score_dir,
    )
    if not eligibility.eligible:
        raise BemProposalError(
            "case non éligible pour la Phase 14 : " + " ; ".join(eligibility.reasons)
        )
    case_id = eligibility.case_id
    assert case_id is not None  # garanti par eligible=True

    worktree, _ = _load_json(worktree_dir / "worktree.json")
    diagnosis, _ = _load_json(diagnosis_dir / "diagnosis.json")
    context_selection, _ = _load_json(context_selection_dir / "context_selection.json")
    confidence_score, _ = _load_json(confidence_score_dir / "confidence_score.json")

    files, detect_warnings = _detect_changed_files(worktree, git_timeout_s=git_timeout_s)
    cross_warnings = _cross_reference_context_selection(files, context_selection)
    warnings = detect_warnings + cross_warnings

    draft = _compose_markdown(
        case_id=case_id, files=files, diagnosis=diagnosis, confidence_score=confidence_score,
        artifact_paths={
            "diagnosis": str(diagnosis_dir / "diagnosis.json"),
            "context_selection": str(context_selection_dir / "context_selection.json"),
            "worktree": str(worktree_dir / "worktree.json"),
            "confidence_score": str(confidence_score_dir / "confidence_score.json"),
            "decision": str(human_review_dir / "decision.json"),
        },
    )

    return BemProposalResult(case_id=case_id, files=files, warnings=warnings, draft_markdown=draft)


def _output_paths(out_root: Path, case_id: str) -> "tuple[Path, Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "bem_entry_proposal.md", out_dir / "bem_proposal.json"


def write_bem_proposal(
    *,
    human_review_dir: "str | Path",
    worktree_dir: "str | Path",
    diagnosis_dir: "str | Path",
    context_selection_dir: "str | Path",
    confidence_score_dir: "str | Path",
    out_root: "str | Path" = "bem_proposals",
    force: bool = False,
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> Path:
    """Génère la proposition et écrit bem_entry_proposal.md + bem_proposal.json
    sous out_root/<case_id>/. Lève BemProposalExistsError si la sortie existe
    déjà et force=False — jamais un écrasement silencieux. N'écrit jamais dans
    BOT_EVOLUTION_MEMORY.md."""
    out_root = Path(out_root)

    result = generate_bem_proposal(
        human_review_dir=human_review_dir,
        worktree_dir=worktree_dir,
        diagnosis_dir=diagnosis_dir,
        context_selection_dir=context_selection_dir,
        confidence_score_dir=confidence_score_dir,
        git_timeout_s=git_timeout_s,
    )

    out_dir, draft_file, trace_file = _output_paths(out_root, result.case_id)
    if out_dir.exists():
        if not force:
            raise BemProposalExistsError(
                f"proposition BEM déjà existante : {out_dir} (utiliser --force pour régénérer)"
            )
        if not draft_file.is_file() or not trace_file.is_file():
            raise BemProposalError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(bem_entry_proposal.md/bem_proposal.json absents) — suppression refusée, vérifier "
                "manuellement"
            )
        log_debug(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=False)
    draft_file.write_text(result.draft_markdown, encoding="utf-8")
    trace_file.write_text(
        json.dumps(result.as_dict(draft_path=str(draft_file)), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_info(
        _TAG,
        f"proposition BEM créée case={result.case_id} fichiers={len(result.files)} "
        f"avertissements={len(result.warnings)} -> {draft_file}",
    )
    return draft_file
