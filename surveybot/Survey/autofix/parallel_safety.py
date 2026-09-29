from __future__ import annotations

"""Deux contrôles complémentaires, en LECTURE SEULE, de sécurité du parallélisme
entre plusieurs cases traités en même temps par le pipeline autofix. N'invoque,
ne planifie ni ne décide jamais rien à la place de l'opérateur : chaque fonction
ne fait que produire un rapport ; l'ordre de traitement ou la résolution d'un
conflit reste une décision humaine (manuelle, ou via un prompt Codex de
réconciliation ciblé, hors périmètre de ce module). Ne touche à aucun fichier
existant (Survey/extractor_integrity.py, Survey/autofix/bem_proposal.py,
Survey/autofix/context_selector.py, etc.) — réutilise leurs techniques telles quelles.

── Partie A : AVANT le lancement de Codex (approximatif par nature, assumé) ───
check_pre_launch_safety(context_selection_paths) — au moins 2 chemins vers des
context_selection.json (Phase 5, Survey/autofix/context_selector.py), chacun portant
déjà le case_id qu'il représente (champ "case_id" du fichier lui-même, jamais
redevinée). Extrait l'ensemble des fichiers de code_files, EN EXCLUANT
always_included (notamment Survey/BOT_EVOLUTION_MEMORY.md — présent dans
presque toutes les sélections ; un conflit dessus n'est jamais un vrai risque
de code, seulement du texte que Git fusionne normalement). Compare chaque paire
de cases : intersection non vide -> paire signalée non sûre, avec le détail des
fichiers partagés et une recommandation explicite de traiter l'un puis l'autre
(jamais un ordre choisi automatiquement par cet outil). Vérification au niveau
du FICHIER seulement : à ce stade, Codex n'a pas encore écrit de code — on ne
peut comparer que les fichiers candidats déjà connus, jamais deviner les
fonctions qui seront réellement touchées (cf. Partie B pour ça).

── Partie B : APRÈS que Codex a produit ses patchs, AVANT le merge (fonction) ─
check_pre_merge_function_overlap(worktree_paths) — au moins 2 chemins vers des
worktree.json (Phase 7, Survey/autofix/autofix_worktree.py). Pour chaque worktree,
détermine les fichiers réellement modifiés depuis base_sha et les fonctions
top-level ajoutées/modifiées/supprimées de chacun, par réutilisation STRICTE,
SANS MODIFICATION, de Survey/autofix/bem_proposal.py::_detect_changed_files — qui
elle-même réutilise déjà telles quelles Survey/autofix/static_validator.py::
_git_changed_paths/_resolve_package_root (Phase 8) et l'énumération AST/hash de
Survey/autofix/bem_proposal.py (_enumerate_top_level_functions/_hash_all, la même
technique déjà utilisée pour Survey/extractor_integrity.py::
_find_function_source, généralisée). Aucune réimplémentation, aucun de ces
fichiers n'est importé ni modifié pour être changé.

Pour chaque fichier modifié présent dans PLUSIEURS des worktrees fournis, les
noms de fonctions réellement touchées (ajoutée, modifiée OU supprimée — les
trois comptent : une fonction supprimée d'un côté et modifiée de l'autre est
tout autant un vrai risque de fusion qu'une modification des deux côtés) sont
comparés entre ces worktrees. Toute fonction touchée par au moins deux
worktrees à la fois est un conflit réel, signalé explicitement (fichier, nom de
fonction, worktrees concernés, nature du changement de chacun) — distinct d'un
fichier partagé sans fonction en commun, qui n'est JAMAIS signalé comme un
conflit à ce niveau (Git le fusionnera normalement). Un fichier partagé dont
l'AST ne parse pas (worktree ou base_sha) est signalé explicitement comme
"non comparable" — jamais un fallback textuel approximatif qui devinerait un
verdict, jamais classé "sûr" par défaut non plus.

── Sortie (les deux parties) ──────────────────────────────────────────────────
Un rapport JSON horodaté (même convention que les phases précédentes —
schema_version, horodatage — mais un instantané par run sous
out_root/<horodatage>/, jamais un fichier unique écrasé, cf.
Survey/autofix/autofix_metrics.py::_timestamped_output_dir : ces contrôles portent sur
un ENSEMBLE de cases donné au moment de l'appel, pas sur un case_id unique).
Écrit uniquement son propre artefact de traçabilité ; ne modifie jamais
context_selections/, autofix_worktrees/, ni aucun worktree Git lui-même.
"""

import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.autofix.bem_proposal import BemProposalError, ChangedFile, _detect_changed_files
from Survey.log_utils import log_debug, log_info

_TAG = "[PARALLEL_SAFETY]"
SCHEMA_VERSION = "1.0"

DEFAULT_GIT_TIMEOUT_S = 15.0


class ParallelSafetyError(Exception):
    """Échec contrôlé — artefact illisible, entrée insuffisante, ou échec Git/AST
    remonté par les fonctions réutilisées."""


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"{path} illisible ({exc})"
    except ValueError as exc:  # json.JSONDecodeError est une sous-classe de ValueError
        return None, f"{path} JSON invalide ({exc})"


def _timestamped_output_dir(out_root: Path) -> Path:
    """Un instantané par run, jamais un fichier écrasé — même convention que
    Survey/autofix/autofix_metrics.py::_timestamped_output_dir. Dupliquée ici plutôt
    qu'importée : importer Survey/autofix/autofix_metrics.py entraînerait toute sa
    chaîne de dépendances (Survey/autofix/replay_browser.py, Playwright) pour une
    fonction utilitaire de huit lignes, sans rapport avec ce module en lecture
    seule sur du JSON."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidate = out_root / stamp
    suffix = 1
    while candidate.exists():
        candidate = out_root / f"{stamp}_{suffix}"
        suffix += 1
    return candidate


# ═══════════════════════ Partie A — pré-lancement (fichier) ══════════════════


@dataclass
class CaseFileSummary:
    case_id: str
    path: str
    code_files: "list[str]" = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"case_id": self.case_id, "path": self.path, "code_files": self.code_files}


@dataclass
class FileOverlapPair:
    case_id_a: str
    case_id_b: str
    shared_files: "list[str]" = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.shared_files

    def as_dict(self) -> dict:
        return {
            "case_id_a": self.case_id_a,
            "case_id_b": self.case_id_b,
            "shared_files": self.shared_files,
            "safe": self.safe,
            "recommendation": (
                None
                if self.safe
                else (
                    f"fichier(s) partagé(s) entre {self.case_id_a} et {self.case_id_b} — traiter "
                    "l'un puis l'autre (jamais un ordre choisi automatiquement par cet outil)"
                )
            ),
        }


@dataclass
class PreLaunchSafetyResult:
    cases: "list[CaseFileSummary]" = field(default_factory=list)
    pairs: "list[FileOverlapPair]" = field(default_factory=list)
    warnings: "list[str]" = field(default_factory=list)

    @property
    def unsafe_pairs(self) -> "list[FileOverlapPair]":
        return [p for p in self.pairs if not p.safe]

    @property
    def safe(self) -> bool:
        return not self.unsafe_pairs

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "check": "pre_launch_file_level",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "granularity": "file",
            "note": (
                "Vérification au niveau du FICHIER, approximative par nature : Codex n'a pas "
                "encore écrit de code à ce stade, seuls les fichiers candidats (code_files de "
                "context_selection.json, Phase 5) sont connus — jamais les fonctions réellement "
                "touchées. Pour une vérification précise après coup, voir la Partie B "
                "(check_pre_merge_function_overlap, sur worktree.json une fois les patchs produits)."
            ),
            "case_ids": [c.case_id for c in self.cases],
            "cases": [c.as_dict() for c in self.cases],
            "pairs": [p.as_dict() for p in self.pairs],
            "unsafe_pairs_count": len(self.unsafe_pairs),
            "safe": self.safe,
            "warnings": self.warnings,
        }


def check_pre_launch_safety(context_selection_paths: "list[str | Path]") -> PreLaunchSafetyResult:
    """Compare au niveau fichier les code_files de >= 2 context_selection.json
    (Phase 5). Ne modifie rien, ne décide jamais d'un ordre de traitement —
    seulement un rapport pour l'opérateur."""
    paths = [Path(p) for p in context_selection_paths]
    if len(paths) < 2:
        raise ParallelSafetyError(
            "au moins 2 context_selection.json requis pour comparer des paires de cases "
            f"({len(paths)} fourni(s))"
        )

    cases: "list[CaseFileSummary]" = []
    seen_case_ids: "dict[str, Path]" = {}
    warnings: "list[str]" = []

    for path in paths:
        data, err = _load_json(path)
        if err or not isinstance(data, dict):
            raise ParallelSafetyError(f"context_selection.json (Phase 5) {err or 'invalide'} : {path}")

        case_id = str(data.get("case_id") or "").strip()
        if not case_id:
            raise ParallelSafetyError(f"context_selection.json sans case_id exploitable : {path}")
        if case_id in seen_case_ids:
            raise ParallelSafetyError(
                f"case_id {case_id!r} fourni deux fois ({seen_case_ids[case_id]} et {path}) — "
                "comparaison ambiguë, jamais devinée"
            )
        seen_case_ids[case_id] = path
        log_debug(_TAG, f"pré-lancement : lecture case={case_id} <- {path}")

        always_included_files = {
            str(entry.get("file"))
            for entry in (data.get("always_included") or [])
            if isinstance(entry, dict) and entry.get("file")
        }
        code_files: "set[str]" = set()
        for entry in data.get("code_files") or []:
            if not isinstance(entry, dict):
                continue
            file_path = str(entry.get("file") or "").strip()
            if not file_path:
                continue
            if file_path in always_included_files:
                # Défensif seulement : always_included et code_files ne devraient jamais se
                # recouper (cf. Survey/autofix/context_selector.py) — exclusion explicite si jamais.
                continue
            code_files.add(file_path)

        cases.append(CaseFileSummary(case_id=case_id, path=str(path), code_files=sorted(code_files)))

    pairs = [
        FileOverlapPair(
            case_id_a=a.case_id,
            case_id_b=b.case_id,
            shared_files=sorted(set(a.code_files) & set(b.code_files)),
        )
        for a, b in itertools.combinations(cases, 2)
    ]
    for pair in pairs:
        if not pair.safe:
            log_debug(
                _TAG,
                f"paire non sûre : {pair.case_id_a} / {pair.case_id_b} "
                f"fichier(s) partagé(s)={pair.shared_files}",
            )

    if not any(c.code_files for c in cases):
        warnings.append(
            "aucun des context_selection.json fournis ne porte de code_files — comparaison "
            "vacuously sûre, ne prouve pas l'absence réelle de risque"
        )

    return PreLaunchSafetyResult(cases=cases, pairs=pairs, warnings=warnings)


def write_pre_launch_safety_check(
    context_selection_paths: "list[str | Path]",
    *,
    out_root: "str | Path" = "parallel_safety_checks",
) -> Path:
    """Exécute check_pre_launch_safety et écrit le résultat sous
    out_root/<horodatage>/file_overlap_report.json — toujours un nouveau
    dossier horodaté, jamais un rapport précédent écrasé (ce contrôle porte sur
    un ensemble de cases donné à cet instant, pas sur un case_id unique)."""
    result = check_pre_launch_safety(context_selection_paths)

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    out_dir = _timestamped_output_dir(out_root)
    out_dir.mkdir(parents=True, exist_ok=False)
    out_file = out_dir / "file_overlap_report.json"
    out_file.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(
        _TAG,
        f"pré-lancement (fichier) : {len(result.cases)} case(s), "
        f"{len(result.unsafe_pairs)}/{len(result.pairs)} paire(s) non sûre(s) -> {out_file}",
    )
    return out_file


# ═══════════════════════ Partie B — pré-merge (fonction) ═════════════════════


@dataclass
class WorktreeSummary:
    case_id: str
    path: str
    branch: str
    base_sha: str
    changed_files_count: int

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "path": self.path,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "changed_files_count": self.changed_files_count,
        }


@dataclass
class FunctionConflict:
    file: str
    function: str
    cases: "list[dict]" = field(default_factory=list)  # [{"case_id":..., "change": "added"|"modified"|"removed"}]

    def as_dict(self) -> dict:
        return {"file": self.file, "function": self.function, "cases": self.cases}


@dataclass
class SharedFileEntry:
    file: str
    case_ids: "list[str]"
    comparable: bool
    reason: "Optional[str]"
    excluded_cases: "list[dict]" = field(default_factory=list)  # [{"case_id":..., "reason":...}]
    conflicting_functions: "list[str]" = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "cases": self.case_ids,
            "comparable": self.comparable,
            "reason": self.reason,
            "excluded_cases": self.excluded_cases,
            "conflicting_functions": self.conflicting_functions,
        }


@dataclass
class PreMergeOverlapResult:
    worktrees: "list[WorktreeSummary]" = field(default_factory=list)
    shared_files: "list[SharedFileEntry]" = field(default_factory=list)
    real_conflicts: "list[FunctionConflict]" = field(default_factory=list)
    warnings: "list[str]" = field(default_factory=list)

    @property
    def unresolved_files(self) -> "list[SharedFileEntry]":
        # Non comparable du tout (< 2 versions exploitables), OU comparaison
        # partielle (au moins un des worktrees partageant ce fichier a été
        # exclu pour échec de parsing AST) : l'un et l'autre laissent une
        # information incomplète, jamais traitée comme sûre par défaut.
        return [f for f in self.shared_files if not f.comparable or f.excluded_cases]

    @property
    def safe(self) -> bool:
        # Un fichier partagé non (totalement) comparable n'est jamais traité
        # comme sûr par défaut, au même titre qu'un vrai conflit de fonction —
        # l'un et l'autre exigent une revue manuelle avant merge.
        return not self.real_conflicts and not self.unresolved_files

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "check": "pre_merge_function_level",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "granularity": "function",
            "note": (
                "Vérification précise, au niveau de la FONCTION, sur les patchs réellement "
                "produits par Codex dans chaque worktree (Survey/autofix/static_validator.py::"
                "_git_changed_paths/_resolve_package_root + énumération AST/hash de "
                "Survey/autofix/bem_proposal.py, réutilisés tels quels via _detect_changed_files). Un "
                "fichier partagé sans fonction en commun n'est jamais signalé comme un conflit — "
                "Git le fusionnera normalement. Si un worktree partageant ce fichier échoue au "
                "parsing AST, il est exclu (excluded_cases) et jamais deviné : les conflits sont "
                "comparés parmi le reste si au moins deux versions restent exploitables, mais le "
                "fichier reste alors signalé non entièrement résolu (unresolved_files_count)."
            ),
            "case_ids": [w.case_id for w in self.worktrees],
            "worktrees": [w.as_dict() for w in self.worktrees],
            "shared_files": [f.as_dict() for f in self.shared_files],
            "real_conflicts": [c.as_dict() for c in self.real_conflicts],
            "unresolved_files_count": len(self.unresolved_files),
            "safe": self.safe,
            "warnings": self.warnings,
        }


def check_pre_merge_function_overlap(
    worktree_paths: "list[str | Path]",
    *,
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> PreMergeOverlapResult:
    """Compare au niveau fonction les patchs de >= 2 worktree.json (Phase 7),
    par réutilisation stricte de Survey/autofix/bem_proposal.py::_detect_changed_files
    (elle-même réutilisant Survey/autofix/static_validator.py Phase 8 et l'énumération
    AST/hash déjà écrite pour la Phase 14). Ne modifie rien, ne décide jamais
    d'une réconciliation — seulement un rapport pour l'opérateur."""
    paths = [Path(p) for p in worktree_paths]
    if len(paths) < 2:
        raise ParallelSafetyError(
            f"au moins 2 worktree.json requis pour comparer les fonctions modifiées "
            f"({len(paths)} fourni(s))"
        )

    worktrees: "list[WorktreeSummary]" = []
    files_by_case: "dict[str, dict[str, ChangedFile]]" = {}
    seen_case_ids: "dict[str, Path]" = {}
    warnings: "list[str]" = []

    for path in paths:
        data, err = _load_json(path)
        if err or not isinstance(data, dict):
            raise ParallelSafetyError(f"worktree.json (Phase 7) {err or 'invalide'} : {path}")

        case_id = str(data.get("case_id") or "").strip()
        if not case_id:
            raise ParallelSafetyError(f"worktree.json sans case_id exploitable : {path}")
        if case_id in seen_case_ids:
            raise ParallelSafetyError(
                f"case_id {case_id!r} fourni deux fois ({seen_case_ids[case_id]} et {path}) — "
                "comparaison ambiguë, jamais devinée"
            )
        seen_case_ids[case_id] = path
        log_debug(_TAG, f"pré-merge : lecture case={case_id} <- {path}")

        try:
            changed_files, det_warnings = _detect_changed_files(data, git_timeout_s=git_timeout_s)
        except BemProposalError as exc:
            # Seule exception que _detect_changed_files remonte (git/AST déjà gérés en interne,
            # convertis en BemProposalError) — cf. Survey/autofix/bem_proposal.py.
            raise ParallelSafetyError(
                f"détection des fichiers/fonctions réellement modifiés (case={case_id}, {path}) "
                f"échouée — Survey/autofix/bem_proposal.py::_detect_changed_files : {exc}"
            ) from exc

        for w in det_warnings:
            warnings.append(f"case={case_id} : {w}")

        files_by_case[case_id] = {cf.git_relative_path: cf for cf in changed_files}
        worktrees.append(
            WorktreeSummary(
                case_id=case_id,
                path=str(path),
                branch=str(data.get("branch") or ""),
                base_sha=str(data.get("base_sha") or ""),
                changed_files_count=len(changed_files),
            )
        )

    file_to_cases: "dict[str, list[str]]" = {}
    for case_id, files in files_by_case.items():
        for git_rel in files:
            file_to_cases.setdefault(git_rel, []).append(case_id)

    shared_files: "list[SharedFileEntry]" = []
    real_conflicts: "list[FunctionConflict]" = []

    for git_rel in sorted(file_to_cases):
        case_ids = sorted(file_to_cases[git_rel])
        if len(case_ids) < 2:
            continue  # fichier modifié par un seul des worktrees fournis — rien à comparer ici

        entries = {cid: files_by_case[cid][git_rel] for cid in case_ids}
        parse_failed = sorted(cid for cid, cf in entries.items() if cf.parse_error)
        excluded_cases = [
            {"case_id": cid, "reason": entries[cid].parse_error} for cid in parse_failed
        ]
        comparable_entries = {cid: cf for cid, cf in entries.items() if cf.parse_error is None}

        if len(comparable_entries) < 2:
            # Moins de deux versions exploitables de ce fichier (échec de parsing d'un côté ou
            # de plusieurs) : aucune comparaison au niveau fonction n'est possible ici — jamais
            # devinée, jamais classée sûre par défaut.
            shared_files.append(
                SharedFileEntry(
                    file=git_rel,
                    case_ids=case_ids,
                    comparable=False,
                    reason=(
                        f"échec du parsing AST pour : {parse_failed} — moins de deux versions "
                        "exploitables de ce fichier, comparaison au niveau fonction impossible"
                    ),
                    excluded_cases=excluded_cases,
                )
            )
            continue

        # Une fonction touchée par un worktree — ajoutée, modifiée OU supprimée — compte : les
        # trois représentent un vrai changement de son code source, donc un vrai risque de
        # fusion si un autre worktree la touche aussi. Uniquement parmi les versions exploitables
        # (comparable_entries) : un worktree exclu pour échec de parsing reste signalé
        # (excluded_cases) mais ne participe jamais à la détection, jamais deviné.
        func_to_occurrences: "dict[str, list[tuple[str, str]]]" = {}
        for cid, cf in comparable_entries.items():
            for fn in cf.added:
                func_to_occurrences.setdefault(fn, []).append((cid, "added"))
            for fn in cf.modified:
                func_to_occurrences.setdefault(fn, []).append((cid, "modified"))
            for fn in cf.removed:
                func_to_occurrences.setdefault(fn, []).append((cid, "removed"))

        conflicting_names: "list[str]" = []
        for fn in sorted(func_to_occurrences):
            occurrences = func_to_occurrences[fn]
            if len({cid for cid, _change in occurrences}) >= 2:
                conflicting_names.append(fn)
                log_debug(_TAG, f"conflit réel : {git_rel}::{fn} <- {occurrences}")
                real_conflicts.append(
                    FunctionConflict(
                        file=git_rel,
                        function=fn,
                        cases=[{"case_id": cid, "change": change} for cid, change in occurrences],
                    )
                )

        shared_files.append(
            SharedFileEntry(
                file=git_rel,
                case_ids=case_ids,
                comparable=True,
                reason=(
                    f"comparaison partielle : {parse_failed} exclu(s) pour échec de parsing AST, "
                    f"conflits comparés uniquement entre {sorted(comparable_entries)}"
                    if parse_failed
                    else None
                ),
                excluded_cases=excluded_cases,
                conflicting_functions=conflicting_names,
            )
        )

    return PreMergeOverlapResult(
        worktrees=worktrees, shared_files=shared_files, real_conflicts=real_conflicts, warnings=warnings,
    )


def write_pre_merge_function_overlap_check(
    worktree_paths: "list[str | Path]",
    *,
    out_root: "str | Path" = "function_overlap_checks",
    git_timeout_s: float = DEFAULT_GIT_TIMEOUT_S,
) -> Path:
    """Exécute check_pre_merge_function_overlap et écrit le résultat sous
    out_root/<horodatage>/function_overlap_report.json — toujours un nouveau
    dossier horodaté, jamais un rapport précédent écrasé."""
    result = check_pre_merge_function_overlap(worktree_paths, git_timeout_s=git_timeout_s)

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    out_dir = _timestamped_output_dir(out_root)
    out_dir.mkdir(parents=True, exist_ok=False)
    out_file = out_dir / "function_overlap_report.json"
    out_file.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(
        _TAG,
        f"pré-merge (fonction) : {len(result.worktrees)} worktree(s), "
        f"{len(result.real_conflicts)} conflit(s) réel(s), "
        f"{len(result.unresolved_files)} fichier(s) non comparable(s) -> {out_file}",
    )
    return out_file
