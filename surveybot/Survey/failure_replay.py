from __future__ import annotations

"""Rejeu local et déterministe d'un failure_case (Survey/failure_case_builder.py)
contre dom_analyzer.analyze_dom() et le validator concerné, sur le HTML figé du
case — sans navigateur réel, sans dispatch/interaction, sans réseau.

Lecture seule sur le pipeline bot : ce module appelle dom_analyzer.analyze_dom(),
question_block_validator.validate_question_blocks() et
action_validator.validate_actions() strictement tels quels (aucune modification),
sur un driver statique fourni par Survey/dom_replay_shim.py. Il ne lit et ne copie
jamais rien dans failure_cases/, et ne modifie jamais le case source.

Stratégie de chargement — une seule par case, jamais de cascade essai/erreur sur
plusieurs DOM en espérant qu'un match :
  - stage=action : post_action_dom.html exclusivement (c'est l'état final observé
    que dom_analyzer aurait dû analyser après action ; pre_action_dom.html, quand
    présent, sert seulement de point de comparaison informatif — jamais analysé).
    Absent des artifacts du case -> NON_REJOUABLE, sans tenter d'alternative.
  - stage=extraction : dom_outer.html, sinon page_source.html, sinon dom_body.html
    — ordre fixe de préférence par fidélité décroissante (dom_outer.html est
    l'exact équivalent de ce que analyze_dom() voit en vrai, documentElement.outerHTML ;
    dom_body.html n'a pas de <head>). Le premier présent dans artifacts (manifest,
    jamais une présence supposée) est utilisé, sans essayer les suivants ensuite.
  - Aucun des deux fichiers ci-dessus présent -> NON_REJOUABLE.

Comparaison : les failure_types (mêmes listes qu'exposées par
failure_case_builder.py, dérivées strictement de validation_report.json) du
replay sont comparés à ceux du case d'origine :
  - REPRODUIT       : ensembles de failure_types strictement identiques.
  - NON_REPRODUIT    : le replay ne signale plus aucun problème (rapport ok=True).
  - DIFFERENT        : le replay signale des problèmes, mais un ensemble différent.
  - NON_REJOUABLE     : DOM requis absent, artefact illisible, ou erreur non
    recouvrable pendant l'extraction/la validation — jamais une conclusion forcée.

Honnêteté sur la fidélité (cf. Survey/dom_replay_shim.py) : un DOM statique n'a
ni JavaScript exécuté, ni layout, ni état runtime — certains signaux que
dom_analyzer.py ou les validators calculent via evaluate() (getComputedStyle,
getBoundingClientRect, lecture d'état live d'un widget) ne peuvent pas être
honorés et sont donc absents du replay, dégradant vers les chemins de repli déjà
existants dans ce code (comportement non modifié). evaluate_handled/
evaluate_declined (compteurs du shim) sont reportés pour rendre cette limite
visible plutôt que de la masquer.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from Survey.dom_replay_shim import ReplayLoadError, load_static_driver
from Survey.failure_case_builder import _failure_types as _report_failure_types
from Survey.log_utils import log_debug

_TAG = "[FAILURE_REPLAY]"

# stage=extraction : ordre de préférence fixe, jamais de cascade essai/erreur sur
# le RÉSULTAT (le choix du fichier est décidé une seule fois, avant toute analyse).
_EXTRACTION_DOM_PRIORITY = ("dom_outer.html", "page_source.html", "dom_body.html")

VERDICT_REPRODUIT = "REPRODUIT"
VERDICT_NON_REPRODUIT = "NON_REPRODUIT"
VERDICT_DIFFERENT = "DIFFERENT"
VERDICT_NON_REJOUABLE = "NON_REJOUABLE"


@dataclass
class ReplayResult:
    verdict: str
    case_id: str
    stage: str
    reason: Optional[str] = None
    dom_file_used: Optional[str] = None
    original_failure_types: list = field(default_factory=list)
    replayed_failure_types: list = field(default_factory=list)
    replayed_blocks_count: Optional[int] = None
    evaluate_handled: Optional[int] = None
    evaluate_declined: Optional[int] = None
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "case_id": self.case_id,
            "stage": self.stage,
            "reason": self.reason,
            "dom_file_used": self.dom_file_used,
            "original_failure_types": self.original_failure_types,
            "replayed_failure_types": self.replayed_failure_types,
            "replayed_blocks_count": self.replayed_blocks_count,
            "evaluate_handled": self.evaluate_handled,
            "evaluate_declined": self.evaluate_declined,
            "warnings": self.warnings,
        }


def _not_replayable(case_id: str, stage: str, reason: str, **extra: Any) -> ReplayResult:
    log_debug(_TAG, f"non rejouable case={case_id} stage={stage} reason={reason}")
    return ReplayResult(verdict=VERDICT_NON_REJOUABLE, case_id=case_id, stage=stage, reason=reason, **extra)


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path.name} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.name} illisible/invalide ({exc})"


def _pick_dom_file(stage: str, artifacts_dir: Path, artifacts_flags: dict) -> "tuple[Optional[str], Optional[str]]":
    """Retourne (nom_fichier, raison_echec). Un seul choix, jamais de cascade."""
    if stage == "action":
        name = "post_action_dom.html"
        if artifacts_flags.get(name) is True and (artifacts_dir / name).is_file():
            return name, None
        return None, (
            "post_action_dom.html absent des artifacts de ce case (manifest.artifacts) "
            "— c'est le seul DOM utilisé pour un replay stage=action, jamais "
            "pre_action_dom.html ou un autre fichier en repli"
        )

    if stage == "extraction":
        for name in _EXTRACTION_DOM_PRIORITY:
            if artifacts_flags.get(name) is True and (artifacts_dir / name).is_file():
                return name, None
        return None, (
            f"aucun de {_EXTRACTION_DOM_PRIORITY} présent dans les artifacts de ce "
            "case (manifest.artifacts)"
        )

    return None, f"stage={stage!r} inconnu — impossible de déterminer quel DOM/validator utiliser"


def replay_failure_case(case_dir: "str | Path") -> ReplayResult:
    case_dir = Path(case_dir)
    case_id = case_dir.name

    manifest, manifest_err = _load_json(case_dir / "manifest.json")
    if manifest_err:
        return _not_replayable(case_id, "unknown", f"manifest.json {manifest_err}")
    if not isinstance(manifest, dict):
        return _not_replayable(case_id, "unknown", "manifest.json ne contient pas un objet JSON")

    stage = str(manifest.get("stage") or "unknown")
    artifacts_flags = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    artifacts_dir = case_dir / "artifacts"

    original_report, report_err = _load_json(artifacts_dir / "validation_report.json")
    if report_err:
        return _not_replayable(case_id, stage, f"validation_report.json {report_err}")
    original_types = _report_failure_types(original_report)
    if not original_types:
        return _not_replayable(
            case_id, stage,
            "validation_report.json du case n'a pas de failure_types exploitable "
            "(issues vide/absent) — rien à comparer",
        )

    dom_name, dom_err = _pick_dom_file(stage, artifacts_dir, artifacts_flags)
    if dom_err:
        return _not_replayable(case_id, stage, dom_err, original_failure_types=original_types)

    try:
        html_text = (artifacts_dir / dom_name).read_text(encoding="utf-8")
    except OSError as exc:
        return _not_replayable(
            case_id, stage, f"{dom_name} illisible ({exc})",
            dom_file_used=dom_name, original_failure_types=original_types,
        )

    try:
        driver = load_static_driver(html_text)
    except ReplayLoadError as exc:
        return _not_replayable(
            case_id, stage, str(exc),
            dom_file_used=dom_name, original_failure_types=original_types,
        )

    try:
        import Survey.dom_analyzer as dom_analyzer
        blocks = dom_analyzer.analyze_dom(driver)
    except Exception as exc:
        log_debug(_TAG, f"analyze_dom a levé pendant le replay case={case_id}: {type(exc).__name__}: {exc}")
        return _not_replayable(
            case_id, stage,
            f"analyze_dom() a levé une exception non recouvrable pendant le replay : "
            f"{type(exc).__name__}: {exc}",
            dom_file_used=dom_name, original_failure_types=original_types,
        )

    warnings: list = []

    if stage == "extraction":
        try:
            from Survey.question_block_validator import validate_question_blocks
            replayed_report = validate_question_blocks(blocks, driver=driver)
        except Exception as exc:
            return _not_replayable(
                case_id, stage,
                f"validate_question_blocks() a levé une exception non recouvrable : "
                f"{type(exc).__name__}: {exc}",
                dom_file_used=dom_name, original_failure_types=original_types,
                replayed_blocks_count=len(blocks or []),
            )
    else:  # stage == "action"
        actions, actions_err = _load_json(artifacts_dir / "actions_requested.json")
        if actions_err:
            return _not_replayable(
                case_id, stage, f"actions_requested.json {actions_err}",
                dom_file_used=dom_name, original_failure_types=original_types,
                replayed_blocks_count=len(blocks or []),
            )
        # dispatcher_success du case d'origine réutilisé tel quel (donnée déjà
        # connue/enregistrée, pas une hypothèse) — le replay ne fait jamais
        # tourner de dispatcher réel, cf. docstring de ce module et la consigne
        # "dans la limite de ce que le validator peut évaluer sans dispatcher réel".
        dispatcher_success = original_report.get("dispatcher_success") if isinstance(original_report, dict) else None
        try:
            from Survey.action_validator import validate_actions
            replayed_report = validate_actions(
                actions,
                dispatcher_success=dispatcher_success,
                driver=driver,
                question_blocks=blocks,
            )
        except Exception as exc:
            return _not_replayable(
                case_id, stage,
                f"validate_actions() a levé une exception non recouvrable : "
                f"{type(exc).__name__}: {exc}",
                dom_file_used=dom_name, original_failure_types=original_types,
                replayed_blocks_count=len(blocks or []),
            )

    replayed_types = _report_failure_types(replayed_report)

    if driver.stats.declined:
        warnings.append(
            f"{driver.stats.declined} appel(s) evaluate() n'ont pas pu être honorés "
            "statiquement (JS/layout requis, cf. Survey/dom_replay_shim.py) — le "
            "replay peut sous-détecter des signaux qu'une page live aurait révélés"
        )

    if not replayed_types:
        verdict = VERDICT_NON_REPRODUIT
    elif set(replayed_types) == set(original_types):
        verdict = VERDICT_REPRODUIT
    else:
        verdict = VERDICT_DIFFERENT

    return ReplayResult(
        verdict=verdict,
        case_id=case_id,
        stage=stage,
        dom_file_used=dom_name,
        original_failure_types=original_types,
        replayed_failure_types=replayed_types,
        replayed_blocks_count=len(blocks or []),
        evaluate_handled=driver.stats.handled,
        evaluate_declined=driver.stats.declined,
        warnings=warnings,
    )
