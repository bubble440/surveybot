from __future__ import annotations

"""Sélection bornée et traçable des fichiers de code à transmettre à la Phase 6
(génération de prompt) pour un incident déjà diagnostiqué (Phase 4, diagnosis.json)
sur un failure_case (Phase 2, manifest.json).

Lecture seule sur les Phases 2/3/4 : ne modifie jamais failure_cases/ ni diagnoses/,
n'appelle et ne réinterprète aucune donnée de Survey/failure_case_builder.py,
Survey/failure_replay.py, Survey/failure_diagnosis.py, dom_analyzer.py, des
validators, du dispatcher — se contente de LIRE leurs sorties déjà produites
(manifest.json, diagnosis.json). Ne génère aucun prompt — hors périmètre.

── Deux sources de signal, et seulement deux ────────────────────────────────
1. modules_likely_involved de diagnosis.json (Phase 4) — repris tel quel, jamais
   recalculé différemment. Chaque fichier retenu porte les matched_signals/
   memory_entries déjà produits par la Phase 4 comme raison d'inclusion.
2. Une structure de mapping itype/stage -> extracteur/stratégie, SI ET SEULEMENT
   SI une telle structure existe réellement et de façon vérifiable dans le code
   du bot (dom_analyzer.py, action_dispatcher.py, ou équivalent).

   Investigation menée avant d'écrire ce module (grep exhaustif : noms usuels de
   structure — ITYPE_*, _ITYPE_MAP, EXTRACTOR_MAP, dispatch_table — puis tout
   dict littéral dont les clés sont des valeurs d'itype "radio"/"checkbox"/...).
   Résultat, documenté ici pour traçabilité : AUCUNE table statique itype/stage
   -> fichier/fonction n'existe dans ce codebase à ce jour.
     - Survey/action_dispatcher.py::_apply_by_target_id route par un long
       enchaînement de conditions "if resolved_itype == ... and payload.get(...)"
       (plus de 150 branches inline) — pas une structure énumérable.
     - Survey/dom_analyzer.py::_analyze_dom_current_context appelle une cascade
       séquentielle try/except de fonctions _extract_XXX(...) — pas une table non
       plus.
     - Le seul dict littéral trouvé indexé par itype
       (Survey/action_dispatcher.py::_TYPE_ALIASES, ~ligne 5692) associe chaque
       itype à des SYNONYMES texte pour parser un libellé LLM ("libellé ////
       type"), pas à un fichier ou une fonction — inutilisable comme source de
       module.
   Reconstruire une association à partir de ces enchaînements if/elif
   reviendrait à interpréter le code par ressemblance — exactement ce que la
   consigne interdit. Cette source de signal contribue donc actuellement zéro
   fichier, et context_selection.json le documente explicitement (champ
   mapping_table_signal) plutôt que de le passer sous silence. Si une vraie
   table de dispatch apparaît un jour dans le code, ce module pourra être étendu
   pour la consommer — non anticipé ici (patch minimal).

── Plafond ────────────────────────────────────────────────────────────────────
CODE_FILES_CAP fichiers de code au maximum (hors BOT_EVOLUTION_MEMORY.md, toujours
inclus séparément). Au-delà, la liste n'est jamais étendue silencieusement : les
fichiers en excès sont retirés (ordre déterministe : ordre de production par la
Phase 4) et consignés dans dropped_files, avec truncated=true.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info

_TAG = "[CONTEXT_SELECTOR]"
SCHEMA_VERSION = "1.0"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_FILE_REL = "Survey/BOT_EVOLUTION_MEMORY.md"

# Documenté et volontairement modeste : assez pour couvrir les quelques modules
# (typiquement 1 à 4, observé en pratique) que la Phase 4 remonte pour un
# incident, avec une marge — sans dériver vers une transmission quasi complète
# du dépôt, qui dégraderait le diagnostic en aval plutôt que de l'aider.
DEFAULT_CODE_FILES_CAP = 8


class ContextSelectionError(Exception):
    """Échec contrôlé de génération d'une sélection de contexte."""


class ContextSelectionExistsError(ContextSelectionError):
    """La sortie cible existe déjà et la régénération n'a pas été demandée."""


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path.name} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.name} illisible/invalide ({exc})"


def _reason_from_phase4(entry: dict) -> str:
    signals = entry.get("matched_signals") or []
    mem_entries = entry.get("memory_entries") or []
    parts = [f"Phase 4 (diagnosis.json, modules_likely_involved) : signaux {signals}"]
    if mem_entries:
        parts.append(f"entrées BOT_EVOLUTION_MEMORY.md correspondantes : {mem_entries}")
    return " ; ".join(parts)


@dataclass
class SelectedFile:
    file: str
    reason: str
    source: str

    def as_dict(self) -> dict:
        return {"file": self.file, "reason": self.reason, "source": self.source}


@dataclass
class ContextSelectionResult:
    case_id: str
    always_included: list = field(default_factory=list)
    code_files: list = field(default_factory=list)
    code_files_cap: int = DEFAULT_CODE_FILES_CAP
    code_files_found: int = 0
    truncated: bool = False
    dropped_files: list = field(default_factory=list)
    mapping_table_checked: bool = True
    mapping_table_found: bool = False
    mapping_table_note: str = ""
    stale_references: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "always_included": self.always_included,
            "code_files": [f.as_dict() if isinstance(f, SelectedFile) else f for f in self.code_files],
            "code_files_cap": self.code_files_cap,
            "code_files_found": self.code_files_found,
            "truncated": self.truncated,
            "dropped_files": self.dropped_files,
            "mapping_table_signal": {
                "checked": self.mapping_table_checked,
                "found": self.mapping_table_found,
                "note": self.mapping_table_note,
            },
            "stale_references": self.stale_references,
            "warnings": self.warnings,
        }


_MAPPING_TABLE_NOTE = (
    "Aucune structure de mapping itype/stage -> fichier/fonction vérifiable n'a été "
    "trouvée dans dom_analyzer.py / action_dispatcher.py (investigation documentée "
    "dans le docstring de Survey/context_selector.py : le routage réel est un "
    "enchaînement if/elif, pas une table énumérable ; le seul dict itype trouvé, "
    "_TYPE_ALIASES, mappe vers des synonymes texte, pas vers des fichiers). Cette "
    "source de signal contribue donc zéro fichier — la sélection repose "
    "entièrement sur modules_likely_involved de la Phase 4."
)


def select_context(
    diagnosis_dir: "str | Path",
    *,
    failure_cases_root: "str | Path" = "failure_cases",
    code_files_cap: int = DEFAULT_CODE_FILES_CAP,
) -> ContextSelectionResult:
    diagnosis_dir = Path(diagnosis_dir)
    case_id = diagnosis_dir.name

    diagnosis, diag_err = _load_json(diagnosis_dir / "diagnosis.json")
    if diag_err or not isinstance(diagnosis, dict):
        raise ContextSelectionError(f"diagnosis.json {diag_err or 'invalide'}")

    manifest, manifest_err = _load_json(Path(failure_cases_root) / case_id / "manifest.json")
    if manifest_err or not isinstance(manifest, dict):
        raise ContextSelectionError(
            f"manifest.json (Phase 2, {Path(failure_cases_root) / case_id}) {manifest_err or 'invalide'}"
        )

    warnings: list[str] = []
    stale_references: list[str] = []

    # ── Source 1 : modules_likely_involved (Phase 4), repris tel quel ──────────
    phase4_modules = diagnosis.get("modules_likely_involved")
    candidates: list[SelectedFile] = []
    if isinstance(phase4_modules, list):
        for entry in phase4_modules:
            if not isinstance(entry, dict):
                continue
            rel_path = str(entry.get("module") or "").strip()
            if not rel_path:
                continue
            if not (_REPO_ROOT / rel_path).is_file():
                stale_references.append(rel_path)
                log_debug(_TAG, f"référence obsolète (fichier absent) ignorée : {rel_path}")
                continue
            candidates.append(SelectedFile(
                file=rel_path,
                reason=_reason_from_phase4(entry),
                source="phase4_modules_likely_involved",
            ))
    if stale_references:
        warnings.append(
            f"{len(stale_references)} module(s) listé(s) par la Phase 4 n'existe(nt) plus sur "
            f"disque (référence obsolète) et ont été exclus : {stale_references}"
        )

    # ── Source 2 : table de mapping itype/stage vérifiée dans le code ──────────
    # Confirmée absente pour ce codebase (cf. docstring du module) — contribue 0.

    code_files_found = len(candidates)
    truncated = code_files_found > code_files_cap
    dropped_files: list[str] = []
    if truncated:
        kept, dropped = candidates[:code_files_cap], candidates[code_files_cap:]
        dropped_files = [d.file for d in dropped]
        candidates = kept
        warnings.append(
            f"sélection tronquée : {code_files_found} fichier(s) trouvé(s) par la Phase 4, "
            f"plafond={code_files_cap} — {len(dropped_files)} retiré(s) plutôt que la liste "
            f"n'étende silencieusement : {dropped_files}"
        )

    if not candidates:
        warnings.append(
            "aucun fichier de code retenu : la Phase 4 n'a trouvé aucun module probable pour ce "
            "case, et aucune structure de mapping itype/stage vérifiable n'existe dans le code "
            "(cf. mapping_table_signal) — sélection limitée à BOT_EVOLUTION_MEMORY.md, pas de "
            "complément par supposition."
        )

    always_included = []
    if (_REPO_ROOT / _MEMORY_FILE_REL).is_file():
        always_included.append({
            "file": _MEMORY_FILE_REL,
            "reason": "contexte systématique de ce projet (règle constante) — hors plafond de fichiers de code",
        })
    else:
        warnings.append(f"{_MEMORY_FILE_REL} introuvable sur disque — non inclus")

    return ContextSelectionResult(
        case_id=case_id,
        always_included=always_included,
        code_files=candidates,
        code_files_cap=code_files_cap,
        code_files_found=code_files_found,
        truncated=truncated,
        dropped_files=dropped_files,
        mapping_table_checked=True,
        mapping_table_found=False,
        mapping_table_note=_MAPPING_TABLE_NOTE,
        stale_references=stale_references,
        warnings=warnings,
    )


def write_context_selection(
    diagnosis_dir: "str | Path",
    *,
    failure_cases_root: "str | Path" = "failure_cases",
    out_root: "str | Path" = "context_selections",
    code_files_cap: int = DEFAULT_CODE_FILES_CAP,
    force: bool = False,
) -> Path:
    """Génère la sélection et l'écrit sous out_root/<case_id>/context_selection.json.

    Ne modifie jamais diagnosis_dir ni failure_cases_root. Lève
    ContextSelectionExistsError si la sortie existe déjà et force=False — jamais
    d'écrasement silencieux.
    """
    diagnosis_dir = Path(diagnosis_dir)
    # diagnosis_dir.name EST le case_id (même convention que la Phase 4, qui
    # mirroir déjà le nom du dossier du case sans supposer de préfixe) : pas de
    # reconstruction de nommage ici.
    out_root = Path(out_root)
    out_dir = out_root / diagnosis_dir.name
    out_file = out_dir / "context_selection.json"

    if out_dir.exists():
        if not force:
            raise ContextSelectionExistsError(
                f"sélection déjà existante : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise ContextSelectionError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de context_selection.json) — suppression refusée, vérifier manuellement"
            )
        import shutil
        log_info(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    result = select_context(
        diagnosis_dir,
        failure_cases_root=failure_cases_root,
        code_files_cap=code_files_cap,
    )

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_info(
        _TAG,
        f"sélection créée case={result.case_id} code_files={len(result.code_files)}"
        f"/{result.code_files_cap} truncated={result.truncated} -> {out_file}",
    )
    return out_file
