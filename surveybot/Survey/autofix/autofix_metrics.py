"""
autofix_metrics.py — outil de statistiques du pipeline autofix. Prérequis à
une future Phase 17 (jamais son déclencheur, jamais un mécanisme de merge
automatique).

Purement en lecture seule sur les artefacts déjà produits par toutes les
phases précédentes (failure_cases/, diagnoses/, prompts/, autofix_worktrees/,
autofix_static_validations/, patch_replays/, live_validations/,
extractor_integrity_checks/, confidence_scores/, human_reviews/,
bem_proposals/, commit_results/, merge_reviews/) : ne modifie, ne recalcule
ni ne relance JAMAIS rien de ces phases, et ne touche à aucun extracteur ni
stratégie de dispatch.

Portée strictement limitée à la mesure : ne construit aucun mécanisme de
merge automatique, aucune liste de catégories de confiance, ni aucune
logique de décision — seulement un outil qui compte et rapporte ce qui s'est
réellement passé. La Phase 17 telle que décrite par le plan (merge
automatique pour certaines catégories) reste explicitement hors périmètre,
en attente de plusieurs semaines de données réelles.

failure_cases/ est la seule liste exhaustive de cases (tout case y a un
dossier dès la Phase 2, cf. Survey/autofix/failure_case_builder.py::build_failure_case
— case_dir = out_root / case_id) : l'énumération part de là, jamais d'un
autre dossier de phase qui pourrait être incomplet. Pour chaque case_id,
l'artefact de chaque phase suivante est lu s'il existe, jamais supposé
présent — son absence est comptée comme "phase non atteinte pour ce case",
jamais une erreur.

Extension additive (transport fleet, cf. Survey/autofix/fleet_case_upload.py /
Survey/autofix/fleet_case_import.py) : failure_cases/<case_id>/fleet_origin.json,
quand il existe (écrit par l'importeur, jamais par la Phase 2), distingue un
case d'origine fleet (transporté depuis une machine de production) d'un case
d'origine locale. Lu tel quel, jamais recalculé ni deviné pour un case qui
n'en porte pas — son absence signifie simplement "case d'origine locale",
jamais une erreur.

Vocabulaire d'artefact repris tel quel, jamais deviné ni réinventé (vérifié
dans le code de chaque phase avant d'écrire ce module) :
  - validation_static.json (Phase 8) / extractor_integrity_check.json
    (Phase 11-A) : verdict="ACCEPTED"/"REJECTED".
  - patch_replay.json (Phase 9) / live_validation.json (Phase 10) :
    refused=bool, outcome=CORRECTIF_CONFIRME/BUG_PERSISTANT/NON_CONCLUANT
    (Survey.autofix.replay_browser.OUTCOME_*).
  - confidence_score.json (Phase 12) : confidence=HIGH/MEDIUM/REJECT
    (Survey.autofix.confidence_score.CONFIDENCE_*).
  - decision.json (Phase 13 human_reviews/, Phase 16 merge_reviews/) :
    decision="APPROVED"/"REJECTED".
  - commit_result.json (Phase 15) : commit_sha (non vide si commit réussi ou
    déjà committé).
  - diagnosis.json (Phase 4) : stage, symptom.failure_types,
    modules_likely_involved (liste de {module, matched_signals,
    memory_entries}).
  - fleet_origin.json (transport fleet, additif) : machine_id, uploaded_at,
    imported_at, live_validation_possible=false (Survey.autofix.fleet_case_import).

Deux limites disclosées explicitement plutôt que masquées ou devinées :
  - "régressions" n'est PAS mesurable avec les artefacts actuels — aucune
    boucle de rétroaction depuis un incident détecté en production vers un
    patch déjà mergé n'existe dans ce pipeline aujourd'hui.
  - la répartition par module (modules_likely_involved) est un proxy
    grossier, jamais une catégorisation sémantique du bug — aucun libellé de
    catégorie n'est inventé par cet outil.

Les décisions humaines (Phase 13/16) sont rapportées telles quelles
(APPROVED/REJECTED), jamais reformulées en "vrai positif"/"faux positif" —
cet outil ne peut pas garantir cette équivalence.

── Sortie ──────────────────────────────────────────────────────────────────
Instantané horodaté, jamais un fichier unique écrasé — cet outil est fait
pour tourner à répétition sur plusieurs semaines et préserver l'historique
des tendances : out_root/<horodatage>/metrics_report.json, même convention
JSON que les phases précédentes (schema_version, horodatage).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.autofix.confidence_score import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_REJECT,
)
from Survey.log_utils import log_debug, log_info
from Survey.autofix.replay_browser import (
    OUTCOME_BUG_PERSISTS,
    OUTCOME_FIX_CONFIRMED,
    OUTCOME_INCONCLUSIVE,
)

_TAG = "[AUTOFIX_METRICS]"
SCHEMA_VERSION = "1.0"

VERDICT_ACCEPTED = "ACCEPTED"
VERDICT_REJECTED = "REJECTED"
DECISION_APPROVED = "APPROVED"
DECISION_REJECTED = "REJECTED"

FIRST_TRY_DEFINITION = (
    "\"Validés du premier coup\" = patch_replay.json (Phase 9) avec "
    f"outcome={OUTCOME_FIX_CONFIRMED!r} ET aucun live_validation.json (Phase 10) "
    "présent pour ce case (donc la Phase 10 n'a pas été nécessaire pour ce case)."
)

PHASE10_DEFINITION = (
    "Parmi les cases D'ORIGINE LOCALE (fleet_origin.json absent) dont patch_replay.json "
    f"(Phase 9) porte outcome={OUTCOME_INCONCLUSIVE!r}, nombre dont live_validation.json "
    f"(Phase 10) porte ensuite refused=false ET outcome={OUTCOME_FIX_CONFIRMED!r}. Les cases "
    f"D'ORIGINE FLEET à outcome={OUTCOME_INCONCLUSIVE!r} sont exclues de ce compte et rapportées "
    "séparément (phase20_fleet_transport.phase9_non_concluant_fleet_not_applicable) : la Phase 10 "
    "ne leur est jamais applicable, faute d'accès physique/réseau à leur machine d'origine pour y "
    "attacher un --cdp-endpoint réel."
)

FLEET_TRANSPORT_NOTE = (
    "fleet_origin.json (transport fleet, Survey.autofix.fleet_case_import) distingue les cases "
    "transportés depuis une machine de production (fleet) des cases produits localement. Pour un "
    f"case fleet dont patch_replay.json (Phase 9) porte outcome={OUTCOME_INCONCLUSIVE!r}, la Phase "
    "10 n'est jamais applicable (pas d'accès physique/réseau à la machine d'origine) : ce case est "
    "explicitement signalé ici plutôt que compté comme un cas ambigu en attente de validation live "
    "dans phase10_live_validation."
)

MODULE_BREAKDOWN_NOTE = (
    "Proxy grossier basé sur diagnosis.json.modules_likely_involved (Phase 4, "
    "lui-même dérivé d'une recherche de signaux structurés dans "
    "BOT_EVOLUTION_MEMORY.md) — PAS une catégorisation sémantique du bug : "
    "aucun libellé de catégorie n'est inventé par cet outil, seuls les noms de "
    "fichiers/modules déjà produits par la Phase 4 sont comptés."
)

REGRESSIONS_NOTE = (
    "NON MESURABLE avec les artefacts actuels : mesurer des régressions "
    "nécessiterait une boucle de rétroaction depuis un incident détecté en "
    "production vers un patch déjà mergé, boucle qui n'existe pas dans ce "
    "pipeline aujourd'hui. Jamais un chiffre deviné, jamais un zéro silencieux."
)

HUMAN_DECISION_NOTE = (
    "Décisions humaines (Phase 13) rapportées telles quelles "
    "(APPROVED/REJECTED) — jamais reformulées en \"vrai positif\"/\"faux "
    "positif\", cet outil ne peut pas garantir cette équivalence."
)

_DEFAULT_ROOTS = {
    "failure_cases": "failure_cases",
    "diagnoses": "diagnoses",
    "prompts": "prompts",
    "autofix_worktrees": "autofix_worktrees",
    "autofix_static_validations": "autofix_static_validations",
    "patch_replays": "patch_replays",
    "live_validations": "live_validations",
    "extractor_integrity_checks": "extractor_integrity_checks",
    "confidence_scores": "confidence_scores",
    "human_reviews": "human_reviews",
    "bem_proposals": "bem_proposals",
    "commit_results": "commit_results",
    "merge_reviews": "merge_reviews",
}

_MAX_WARNINGS = 500


class AutofixMetricsError(Exception):
    """Véritable erreur d'usage (racine failure_cases/ introuvable ou n'est
    pas un dossier) — jamais levée pour un artefact de phase manquant ou
    illisible sur un case précis, qui est simplement compté comme "phase non
    atteinte pour ce case"."""


def _load_json_object(path: Path, *, warnings: list) -> "Optional[dict]":
    """Lecture tolérante d'un artefact optionnel. Absence = None silencieux
    (phase non atteinte pour ce case, jamais une erreur). Présence mais
    illisible/invalide = None + avertissement explicite, jamais un contenu
    deviné."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        if len(warnings) < _MAX_WARNINGS:
            warnings.append(f"{path} illisible ({exc})")
        log_debug(_TAG, f"artefact illisible : {path} ({exc})")
        return None
    except ValueError as exc:  # json.JSONDecodeError est une sous-classe de ValueError
        if len(warnings) < _MAX_WARNINGS:
            warnings.append(f"{path} JSON invalide ({exc})")
        log_debug(_TAG, f"artefact JSON invalide : {path} ({exc})")
        return None
    if not isinstance(data, dict):
        if len(warnings) < _MAX_WARNINGS:
            warnings.append(f"{path} ne contient pas un objet JSON")
        log_debug(_TAG, f"artefact non-objet : {path}")
        return None
    return data


def _resolve_roots(overrides: "Optional[dict[str, str | Path]]") -> "dict[str, Path]":
    roots = dict(_DEFAULT_ROOTS)
    if overrides:
        for key, value in overrides.items():
            if key not in _DEFAULT_ROOTS:
                raise AutofixMetricsError(f"racine inconnue : {key!r}")
            roots[key] = str(value)
    return {key: Path(value) for key, value in roots.items()}


def list_case_ids(failure_cases_root: Path, *, warnings: list) -> "list[str]":
    """Énumère tous les case_id connus — un dossier sous failure_cases_root
    par case, dès la Phase 2. Seule liste exhaustive : jamais dérivée d'un
    autre dossier de phase, qui pourrait être partiel."""
    if not failure_cases_root.is_dir():
        raise AutofixMetricsError(
            f"racine failure_cases introuvable ou n'est pas un dossier : {failure_cases_root}"
        )
    case_ids = []
    for entry in sorted(failure_cases_root.iterdir()):
        if not entry.is_dir():
            warnings.append(f"{entry} : entrée non-dossier sous failure_cases/, ignorée")
            continue
        case_ids.append(entry.name)
    return case_ids


@dataclass
class CaseArtifacts:
    case_id: str
    manifest: "Optional[dict]" = None
    fleet_origin: "Optional[dict]" = None
    diagnosis: "Optional[dict]" = None
    has_prompt: bool = False
    has_manual_review_required: bool = False
    worktree: "Optional[dict]" = None
    validation_static: "Optional[dict]" = None
    patch_replay: "Optional[dict]" = None
    live_validation: "Optional[dict]" = None
    extractor_integrity_check: "Optional[dict]" = None
    confidence_score: "Optional[dict]" = None
    human_review_decision: "Optional[dict]" = None
    bem_proposal: "Optional[dict]" = None
    commit_result: "Optional[dict]" = None
    merge_review_decision: "Optional[dict]" = None


def _collect_case_artifacts(case_id: str, roots: "dict[str, Path]", *, warnings: list) -> CaseArtifacts:
    ca = CaseArtifacts(case_id=case_id)

    ca.manifest = _load_json_object(roots["failure_cases"] / case_id / "manifest.json", warnings=warnings)
    ca.fleet_origin = _load_json_object(
        roots["failure_cases"] / case_id / "fleet_origin.json", warnings=warnings
    )
    ca.diagnosis = _load_json_object(roots["diagnoses"] / case_id / "diagnosis.json", warnings=warnings)

    prompt_dir = roots["prompts"] / case_id
    ca.has_prompt = (prompt_dir / "prompt.txt").is_file()
    ca.has_manual_review_required = (prompt_dir / "MANUAL_REVIEW_REQUIRED.txt").is_file()

    ca.worktree = _load_json_object(roots["autofix_worktrees"] / case_id / "worktree.json", warnings=warnings)
    ca.validation_static = _load_json_object(
        roots["autofix_static_validations"] / case_id / "validation_static.json", warnings=warnings
    )
    ca.patch_replay = _load_json_object(
        roots["patch_replays"] / case_id / "patch_replay.json", warnings=warnings
    )
    ca.live_validation = _load_json_object(
        roots["live_validations"] / case_id / "live_validation.json", warnings=warnings
    )
    ca.extractor_integrity_check = _load_json_object(
        roots["extractor_integrity_checks"] / case_id / "extractor_integrity_check.json", warnings=warnings
    )
    ca.confidence_score = _load_json_object(
        roots["confidence_scores"] / case_id / "confidence_score.json", warnings=warnings
    )
    ca.human_review_decision = _load_json_object(
        roots["human_reviews"] / case_id / "decision.json", warnings=warnings
    )
    ca.bem_proposal = _load_json_object(
        roots["bem_proposals"] / case_id / "bem_proposal.json", warnings=warnings
    )
    ca.commit_result = _load_json_object(
        roots["commit_results"] / case_id / "commit_result.json", warnings=warnings
    )
    ca.merge_review_decision = _load_json_object(
        roots["merge_reviews"] / case_id / "decision.json", warnings=warnings
    )
    return ca


def _bump(counter: dict, key: Any) -> None:
    counter[key] = counter.get(key, 0) + 1


@dataclass
class MetricsReport:
    generated_at: str
    case_count: int
    sections: dict
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "case_count": self.case_count,
            **self.sections,
            "warnings": self.warnings,
        }


def compute_autofix_metrics(
    *,
    roots: "Optional[dict[str, str | Path]]" = None,
) -> MetricsReport:
    """Calcule l'instantané de statistiques sur l'ensemble des cases connus
    sous failure_cases/. Purement en lecture : n'écrit rien, ne modifie
    aucun artefact d'aucune phase."""
    resolved_roots = _resolve_roots(roots)
    warnings: list = []

    case_ids = list_case_ids(resolved_roots["failure_cases"], warnings=warnings)
    log_debug(_TAG, f"{len(case_ids)} case(s) sous {resolved_roots['failure_cases']}")

    stage_counts: dict = {}
    module_counts: dict = {}

    fleet_origin_cases = 0
    local_origin_cases = 0
    phase9_inconclusive_fleet_not_applicable = 0

    diagnosed = 0
    with_prompt = 0
    with_manual_review_required = 0
    with_worktree = 0

    static_run = 0
    static_verdicts: dict = {}

    integrity_run = 0
    integrity_verdicts: dict = {}

    patch_replay_run = 0
    patch_replay_refused = 0
    patch_replay_outcomes: dict = {}

    first_try_confirmed = 0

    live_validation_run = 0
    live_validation_refused = 0
    live_validation_outcomes: dict = {}
    phase9_inconclusive = 0
    phase9_inconclusive_then_live_confirmed = 0

    confidence_run = 0
    confidence_values: dict = {}

    human_decisions: dict = {}

    bem_proposals_generated = 0

    commits = 0

    merge_decisions: dict = {}

    for case_id in case_ids:
        ca = _collect_case_artifacts(case_id, resolved_roots, warnings=warnings)

        stage = "unknown"
        if isinstance(ca.manifest, dict):
            stage = str(ca.manifest.get("stage") or "unknown")
        _bump(stage_counts, stage)

        is_fleet_origin = isinstance(ca.fleet_origin, dict)
        if is_fleet_origin:
            fleet_origin_cases += 1
        else:
            local_origin_cases += 1

        if isinstance(ca.diagnosis, dict):
            diagnosed += 1
            modules = ca.diagnosis.get("modules_likely_involved")
            if isinstance(modules, list):
                for row in modules:
                    if isinstance(row, dict) and row.get("module"):
                        _bump(module_counts, str(row["module"]))

        if ca.has_prompt:
            with_prompt += 1
        if ca.has_manual_review_required:
            with_manual_review_required += 1

        if isinstance(ca.worktree, dict):
            with_worktree += 1

        if isinstance(ca.validation_static, dict):
            static_run += 1
            _bump(static_verdicts, str(ca.validation_static.get("verdict")))

        if isinstance(ca.extractor_integrity_check, dict):
            integrity_run += 1
            _bump(integrity_verdicts, str(ca.extractor_integrity_check.get("verdict")))

        patch_replay_outcome = None
        if isinstance(ca.patch_replay, dict):
            patch_replay_run += 1
            if ca.patch_replay.get("refused") is True:
                patch_replay_refused += 1
            patch_replay_outcome = ca.patch_replay.get("outcome")
            _bump(patch_replay_outcomes, str(patch_replay_outcome))
            if patch_replay_outcome == OUTCOME_FIX_CONFIRMED and ca.live_validation is None:
                first_try_confirmed += 1
            if patch_replay_outcome == OUTCOME_INCONCLUSIVE:
                if is_fleet_origin:
                    # Phase 10 n'est jamais applicable à un case fleet (pas d'accès
                    # physique/réseau à sa machine d'origine) — signalé séparément,
                    # jamais compté comme un cas ambigu en attente de validation live.
                    phase9_inconclusive_fleet_not_applicable += 1
                else:
                    phase9_inconclusive += 1

        live_outcome = None
        if isinstance(ca.live_validation, dict):
            live_validation_run += 1
            if ca.live_validation.get("refused") is True:
                live_validation_refused += 1
            live_outcome = ca.live_validation.get("outcome")
            _bump(live_validation_outcomes, str(live_outcome))
            if (
                patch_replay_outcome == OUTCOME_INCONCLUSIVE
                and ca.live_validation.get("refused") is False
                and live_outcome == OUTCOME_FIX_CONFIRMED
            ):
                phase9_inconclusive_then_live_confirmed += 1

        if isinstance(ca.confidence_score, dict):
            confidence_run += 1
            _bump(confidence_values, str(ca.confidence_score.get("confidence")))

        if isinstance(ca.human_review_decision, dict):
            _bump(human_decisions, str(ca.human_review_decision.get("decision")))

        if isinstance(ca.bem_proposal, dict):
            bem_proposals_generated += 1

        if isinstance(ca.commit_result, dict) and str(ca.commit_result.get("commit_sha") or ""):
            commits += 1

        if isinstance(ca.merge_review_decision, dict):
            _bump(merge_decisions, str(ca.merge_review_decision.get("decision")))

    sections = {
        "phase2_detection": {
            "cases_detected": len(case_ids),
        },
        "phase4_diagnosis": {
            "diagnosed": diagnosed,
        },
        "phase6_prompt": {
            "with_prompt": with_prompt,
            "with_manual_review_required": with_manual_review_required,
        },
        "phase7_worktree": {
            "with_worktree": with_worktree,
        },
        "phase8_static_validation": {
            "run": static_run,
            "accepted": static_verdicts.get(VERDICT_ACCEPTED, 0),
            "rejected": static_verdicts.get(VERDICT_REJECTED, 0),
        },
        "phase9_patch_replay": {
            "run": patch_replay_run,
            "refused": patch_replay_refused,
            "outcome_correctif_confirme": patch_replay_outcomes.get(OUTCOME_FIX_CONFIRMED, 0),
            "outcome_bug_persistant": patch_replay_outcomes.get(OUTCOME_BUG_PERSISTS, 0),
            "outcome_non_concluant": patch_replay_outcomes.get(OUTCOME_INCONCLUSIVE, 0),
        },
        "first_try_validated": {
            "definition": FIRST_TRY_DEFINITION,
            "count": first_try_confirmed,
        },
        "phase10_live_validation": {
            "definition": PHASE10_DEFINITION,
            "run": live_validation_run,
            "refused": live_validation_refused,
            "outcome_correctif_confirme": live_validation_outcomes.get(OUTCOME_FIX_CONFIRMED, 0),
            "phase9_non_concluant_count": phase9_inconclusive,
            "phase9_non_concluant_then_live_confirme": phase9_inconclusive_then_live_confirmed,
        },
        "phase11a_extractor_integrity": {
            "run": integrity_run,
            "accepted": integrity_verdicts.get(VERDICT_ACCEPTED, 0),
            "rejected": integrity_verdicts.get(VERDICT_REJECTED, 0),
        },
        "phase12_confidence_score": {
            "run": confidence_run,
            "high": confidence_values.get(CONFIDENCE_HIGH, 0),
            "medium": confidence_values.get(CONFIDENCE_MEDIUM, 0),
            "reject": confidence_values.get(CONFIDENCE_REJECT, 0),
        },
        "phase13_human_review": {
            "note": HUMAN_DECISION_NOTE,
            "approved": human_decisions.get(DECISION_APPROVED, 0),
            "rejected": human_decisions.get(DECISION_REJECTED, 0),
        },
        "phase14_bem_proposal": {
            "generated": bem_proposals_generated,
        },
        "phase15_commit": {
            "commits": commits,
        },
        "phase16_merge_review": {
            "approved": merge_decisions.get(DECISION_APPROVED, 0),
            "rejected": merge_decisions.get(DECISION_REJECTED, 0),
        },
        "breakdown_by_stage": stage_counts,
        "breakdown_by_module": {
            "note": MODULE_BREAKDOWN_NOTE,
            "counts": module_counts,
        },
        "regressions": {
            "note": REGRESSIONS_NOTE,
        },
        "phase20_fleet_transport": {
            "note": FLEET_TRANSPORT_NOTE,
            "fleet_origin_cases": fleet_origin_cases,
            "local_origin_cases": local_origin_cases,
            "phase9_non_concluant_fleet_not_applicable": phase9_inconclusive_fleet_not_applicable,
        },
    }

    return MetricsReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        case_count=len(case_ids),
        sections=sections,
        warnings=warnings,
    )


def _timestamped_output_dir(out_root: Path) -> Path:
    """Un instantané par run, jamais un fichier unique écrasé — suffixe
    numérique incrémental si deux runs tombent dans la même seconde
    (jamais un écrasement silencieux)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidate = out_root / stamp
    suffix = 1
    while candidate.exists():
        candidate = out_root / f"{stamp}_{suffix}"
        suffix += 1
    return candidate


def write_autofix_metrics(
    *,
    roots: "Optional[dict[str, str | Path]]" = None,
    out_root: "str | Path" = "autofix_metrics",
) -> Path:
    """Calcule l'instantané et l'écrit sous out_root/<horodatage>/
    metrics_report.json. Toujours un nouveau dossier horodaté — jamais un
    rapport précédent écrasé, l'historique des tendances est préservé."""
    report = compute_autofix_metrics(roots=roots)

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    out_dir = _timestamped_output_dir(out_root)
    out_dir.mkdir(parents=True, exist_ok=False)
    out_file = out_dir / "metrics_report.json"
    out_file.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log_info(_TAG, f"instantané écrit ({report.case_count} case(s)) -> {out_file}")
    return out_file
