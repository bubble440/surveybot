from __future__ import annotations

"""Phase 12 — score de confiance d'un patch autofix, agrégé en LECTURE SEULE à
partir des verdicts déjà produits par les Phases 7 à 11. Ne recalcule, ne
relance et ne réexécute jamais rien de ces phases (aucun extracteur, aucune
stratégie de dispatch, aucun module du pipeline existant n'est touché ni
importé pour être réexécuté) : ce module se contente de lire des artefacts
JSON déjà écrits sur disque et d'en dériver un verdict.

── Différence assumée avec les façades des Phases 7 à 11 ─────────────────────
Ces phases refusent tout AVANT le moindre effet de bord dès qu'une
précondition manque, parce qu'elles s'apprêtent à faire quelque chose de
risqué (Git, un vrai navigateur, une vraie page CDP). Cette phase-ci N'A
AUCUN EFFET DE BORD au sens de ces phases : elle ne fait que lire des
artefacts déjà produits et calculer un verdict, puis écrire CE SEUL verdict.
Sa valeur vient de là : produire TOUJOURS un résultat, même dégradé
(MEDIUM/REJECT), plutôt que de refuser dès qu'une pièce manque. Seule une
véritable erreur d'usage reste bloquante (levée comme ConfidenceScoreError,
jamais un résultat dégradé) :
  - case_id incohérent entre les artefacts/chemins effectivement fournis
    (signe qu'on a mélangé deux cases différents — comparaison à la fois sur
    le case_id porté par le contenu de chaque artefact lisible ET sur le nom
    du dossier parent de chaque chemin fourni, même principe que les Phases
    9/10/11) ;
  - branch incohérente entre les artefacts lisibles, même principe ;
  - aucun artefact fourni n'est lisible avec un case_id exploitable (rien à
    quoi rattacher un verdict).
Toute autre absence ou incohérence de contenu (une phase qui n'a pas tourné,
un champ attendu absent, un JSON illisible) DÉGRADE le critère concerné à
MISSING/NOT_RUN — jamais une exception, jamais un crash.

── Vocabulaire de verdict : relu dans le code, jamais redéfini en dur ────────
  - validation_static.json (Phase 8, Survey/autofix/static_validator.py) et
    extractor_integrity_check.json (Phase 11-A, Survey/autofix/
    extractor_integrity_gate.py) : verdict "ACCEPTED"/"REJECTED", identique
    dans les deux modules (StaticValidationResult.verdict /
    IntegrityCheckResult.verdict).
  - patch_replay.json (Phase 9, Survey/autofix/patch_replay.py) et
    live_validation.json (Phase 10, Survey/autofix/live_validator.py) : outcome
    Survey.autofix.replay_browser.OUTCOME_FIX_CONFIRMED/OUTCOME_BUG_PERSISTS/
    OUTCOME_INCONCLUSIVE ("CORRECTIF_CONFIRME"/"BUG_PERSISTANT"/
    "NON_CONCLUANT"), importés ici tels quels, jamais réécrits en chaîne.
    Les deux modules exposent aussi refused (bool) : refused=true signifie
    qu'aucun rejeu n'a été tenté (préconditions non satisfaites côté
    Phase 9/10), pas un échec du correctif lui-même.

── Quatre critères indépendants, jamais un booléen simple ────────────────────
Chacun vaut explicitement PASS/FAIL/INCONCLUSIVE/MISSING (et, pour la
validation live seulement, NOT_RUN à la place de MISSING — une absence
normale et attendue, cf. Phase 10, ne doit jamais être confondue avec un
échec) :
  1. validation statique (Phase 8) : PASS si verdict="ACCEPTED", FAIL sinon,
     MISSING si validation_static.json absent/illisible.
  2. intégrité des fonctions gelées (Phase 11-A) : PASS seulement pour un
     artefact v2 complet ACCEPTED ; un ancien artefact ou une absence vaut
     MISSING, un rejet ou une incohérence vaut FAIL.
  3. correctif confirmé sur le case ciblé : AVANT toute autre évaluation de ce
     critère, patch_replay.json.guard_check.state="echec" (Phase 9, contrôle de
     garde d'un correctif d'action — cf. Survey/autofix/patch_replay.py) vaut FAIL,
     même si l'outcome du rejeu est NON_CONCLUANT et même si une validation
     live confirmée existerait par ailleurs : la garde a pris la main alors que
     la cible du chemin historique était actionnable, jamais réexaminé. Les
     états "reussi"/"non_concluant"/"non_applicable" (ou l'absence de la clé)
     ne changent rien à ce qui suit. Sinon, dérivé par défaut de
     patch_replay.json (Phase 9) — PASS si outcome=CORRECTIF_CONFIRME, FAIL
     si outcome=BUG_PERSISTANT, INCONCLUSIVE sinon (y compris refused=true),
     MISSING si patch_replay.json absent/illisible. SI et seulement si cet
     outcome vaut NON_CONCLUANT, ET que live_validation.json existe, est
     lisible et porte refused=false, alors c'est SON outcome (même
     traduction) qui prévaut pour ce seul critère — un verdict Phase 9 déjà
     tranché (CORRECTIF_CONFIRME ou BUG_PERSISTANT) n'est jamais réexaminé
     par la Phase 10, et les deux verdicts ne sont jamais combinés autrement.
  4. validation live : PASS/FAIL depuis live_validation.json si celui-ci
     existe, est lisible et refused=false (même traduction) ; NOT_RUN dans
     tous les autres cas (non fourni, absent, illisible, refused=true, ou
     outcome non concluant) — jamais confondu avec un échec du patch.
Ce quatrième critère est rapporté pour transparence complète mais n'entre
dans la règle de décision ci-dessous que via le critère 3 (son éventuelle
préséance sur un outcome Phase 9 resté NON_CONCLUANT) — il n'existe pas de
cinquième branche de décision qui le testerait seul.

── Calcul de la confiance : une suite de règles ORDONNÉES, jamais une formule
pondérée ni un fallback empilé (conforme à la demande d'origine) ────────────
  a. validation statique != PASS -> REJECT.
  b. intégrité des fonctions gelées = FAIL -> REJECT (toujours dominant,
     même si le correctif ciblé est confirmé par ailleurs).
  c. correctif confirmé = FAIL -> REJECT.
  d. intégrité != PASS -> MEDIUM (aucun HIGH sur un contrôle absent).
  e. correctif confirmé = PASS -> HIGH.
  f. sinon -> MEDIUM, avec le détail exact du/des critère(s) qui empêche(nt)
     HIGH dans `reason`.
La première règle qui s'applique l'emporte. L'état du critère d'intégrité
reste visible dans `criteria` ; seul PASS peut contribuer à HIGH.

── Avertissement systématique, non conditionnel ──────────────────────────────
Le critère d'intégrité ne couvre à ce jour que le hash des fonctions gelées
(Phase 11, partie A) : le rejeu de DOM représentatifs (Phase 11, partie B)
n'existe pas encore. Un verdict HIGH ne garantit donc PAS l'absence de
régression comportementale. Cet avertissement est toujours présent dans `warnings`, quel que
soit le verdict.

── Sortie ──────────────────────────────────────────────────────────────────
Verdict tracé sur disque, même convention JSON que les phases précédentes
(schema_version/case_id/created_at) : out_root/<case_id>/confidence_score.json.
Ne modifie jamais aucun artefact d'une phase antérieure. Refus explicite
(jamais un écrasement silencieux) si la sortie existe déjà et force=False.
"""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info
from Survey.autofix.patch_replay import GUARD_CHECK_FAILED
from Survey.autofix.replay_browser import (
    OUTCOME_BUG_PERSISTS,
    OUTCOME_FIX_CONFIRMED,
    OUTCOME_INCONCLUSIVE,
)

_TAG = "[CONFIDENCE_SCORE]"
SCHEMA_VERSION = "1.0"

CRITERION_PASS = "PASS"
CRITERION_FAIL = "FAIL"
CRITERION_INCONCLUSIVE = "INCONCLUSIVE"
CRITERION_MISSING = "MISSING"
CRITERION_NOT_RUN = "NOT_RUN"

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_REJECT = "REJECT"

INTEGRITY_SCOPE_WARNING = (
    "le critère d'intégrité (Phase 11, partie A) ne couvre aujourd'hui que le hash des "
    "fonctions gelées — le rejeu de DOM représentatifs (Phase 11, partie B) n'existe pas "
    "encore : un verdict HIGH ne garantit pas l'absence de régression comportementale"
)


class ConfidenceScoreError(Exception):
    """Véritable erreur d'usage (case_id/branch incohérents entre les artefacts
    fournis, ou aucun case_id exploitable) — jamais levée pour une simple
    absence/illisibilité d'un artefact de phase, qui dégrade le critère
    concerné au lieu de planter l'outil."""


class ConfidenceScoreExistsError(ConfidenceScoreError):
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


# ─────────────────────────────── Critères ────────────────────────────────────

def _binary_verdict_criterion(
    data: "Optional[dict]", err: "Optional[str]", source: str
) -> "tuple[str, str]":
    """Traduction partagée par validation_static.json (Phase 8) et
    extractor_integrity_check.json (Phase 11-A) : même vocabulaire
    ACCEPTED/REJECTED dans les deux modules, vérifié dans le code."""
    if err or not isinstance(data, dict):
        return CRITERION_MISSING, err or f"{source} ne contient pas un objet JSON"
    verdict = data.get("verdict")
    if verdict == "ACCEPTED":
        return CRITERION_PASS, f"{source}.verdict={verdict!r}"
    return CRITERION_FAIL, f"{source}.verdict={verdict!r}"


def _integrity_criterion(data: "Optional[dict]", err: "Optional[str]") -> "tuple[str, str]":
    source = "extractor_integrity_check.json"
    if err or not isinstance(data, dict):
        return CRITERION_MISSING, err or f"{source} ne contient pas un objet JSON"
    if data.get("verdict") != "ACCEPTED":
        return CRITERION_FAIL, f"{source}.verdict={data.get('verdict')!r}"
    rows = data.get("functions")
    if data.get("schema_version") != "2.0" or not isinstance(rows, list) or not rows:
        return CRITERION_MISSING, f"{source} ne contient pas le contrôle à trois versions de la Phase 11-A"
    if data.get("errors") != [] or data.get("budget_exceeded") is not False:
        return CRITERION_FAIL, f"{source} contient des erreurs ou un budget dépassé"
    if len(rows) != data.get("total_entries") or any(
        not isinstance(row, dict) or not isinstance(row.get("key"), str)
        or row.get("state") not in ("UNCHANGED", "EXPECTED_CHANGE") for row in rows
    ):
        return CRITERION_FAIL, f"{source} contient un état non accepté"
    if data.get("checked_entries") != data.get("total_entries") or len(rows) != len({
        row["key"] for row in rows
    }):
        return CRITERION_FAIL, f"{source} incomplet ou incohérent"
    expected_count = sum(row["state"] == "EXPECTED_CHANGE" for row in rows)
    counts = data.get("state_counts")
    states = ("UNCHANGED", "EXPECTED_CHANGE", "UNEXPECTED_CHANGE", "BASELINE_MISMATCH")
    if not isinstance(counts, dict) or any(type(counts.get(state)) is not int for state in states) or (
        counts.get("UNCHANGED") != len(rows) - expected_count or
        counts.get("EXPECTED_CHANGE") != expected_count or counts.get("UNEXPECTED_CHANGE") != 0
        or counts.get("BASELINE_MISMATCH") != 0
    ):
        return CRITERION_FAIL, f"{source} contient des compteurs d'état incohérents"
    if data.get("review_required") is not (expected_count > 0):
        return CRITERION_FAIL, f"{source} omet ou contredit la revue requise du core"
    return CRITERION_PASS, f"{source}.verdict='ACCEPTED'"


def _fix_confirmed_criterion(
    patch_replay: "Optional[dict]",
    pr_err: "Optional[str]",
    live_validation: "Optional[dict]",
    lv_provided: bool,
    lv_err: "Optional[str]",
) -> "tuple[str, str, str]":
    """Critère "correctif confirmé sur le case ciblé". Source par défaut
    patch_replay.json (Phase 9) ; live_validation.json (Phase 10) ne prévaut
    QUE si la Phase 9 est restée NON_CONCLUANT ET que la Phase 10 est
    lisible/refused=false — un outcome Phase 9 déjà tranché
    (CORRECTIF_CONFIRME/BUG_PERSISTANT) n'est jamais réexaminé."""
    if pr_err or not isinstance(patch_replay, dict):
        return CRITERION_MISSING, pr_err or "patch_replay.json ne contient pas un objet JSON", "patch_replay.json"

    guard_check = patch_replay.get("guard_check")
    if isinstance(guard_check, dict) and guard_check.get("state") == GUARD_CHECK_FAILED:
        return (
            CRITERION_FAIL,
            f"patch_replay.json.guard_check.state={guard_check.get('state')!r} reason="
            f"{guard_check.get('reason')!r} — la garde du correctif a pris la main alors que la "
            "cible du chemin historique était actionnable (prioritaire sur toute autre évaluation "
            "de ce critère, y compris un rejeu non concluant ou une validation live confirmée)",
            "patch_replay.json",
        )

    if patch_replay.get("refused") is True:
        return (
            CRITERION_INCONCLUSIVE,
            "patch_replay.json.refused=true — Phase 9 n'a rien pu rejouer",
            "patch_replay.json",
        )

    outcome = patch_replay.get("outcome")
    if outcome == OUTCOME_FIX_CONFIRMED:
        return CRITERION_PASS, f"patch_replay.json.outcome={outcome!r}", "patch_replay.json"
    if outcome == OUTCOME_BUG_PERSISTS:
        return CRITERION_FAIL, f"patch_replay.json.outcome={outcome!r}", "patch_replay.json"

    # Phase 9 non tranchée (outcome == OUTCOME_INCONCLUSIVE, ou toute autre
    # valeur inattendue traitée de la même façon, jamais un cas à part) :
    # seule situation où la Phase 10 peut prévaloir pour ce critère.
    base_detail = f"patch_replay.json.outcome={outcome!r}"

    if not lv_provided:
        return CRITERION_INCONCLUSIVE, base_detail + " ; live_validation.json non fourni", "patch_replay.json"
    if lv_err or not isinstance(live_validation, dict):
        return (
            CRITERION_INCONCLUSIVE,
            base_detail + f" ; live_validation.json fourni mais {lv_err or 'illisible'}",
            "patch_replay.json",
        )
    if live_validation.get("refused") is not False:
        return (
            CRITERION_INCONCLUSIVE,
            base_detail + f" ; live_validation.json.refused={live_validation.get('refused')!r}",
            "patch_replay.json",
        )

    live_outcome = live_validation.get("outcome")
    if live_outcome == OUTCOME_FIX_CONFIRMED:
        return (
            CRITERION_PASS,
            f"live_validation.json.outcome={live_outcome!r} (Phase 9 restée NON_CONCLUANT, "
            "la Phase 10 prévaut pour ce seul critère)",
            "live_validation.json",
        )
    if live_outcome == OUTCOME_BUG_PERSISTS:
        return (
            CRITERION_FAIL,
            f"live_validation.json.outcome={live_outcome!r} (Phase 9 restée NON_CONCLUANT, "
            "la Phase 10 prévaut pour ce seul critère)",
            "live_validation.json",
        )
    return (
        CRITERION_INCONCLUSIVE,
        base_detail + f" ; live_validation.json.outcome={live_outcome!r} également non concluant",
        "patch_replay.json + live_validation.json",
    )


def _live_validation_criterion(
    live_validation: "Optional[dict]", lv_provided: bool, lv_err: "Optional[str]"
) -> "tuple[str, str]":
    """Critère "validation live" rapporté indépendamment (transparence) — voir
    docstring du module pour son rôle dans la règle de décision (via le
    critère "correctif confirmé" seulement, jamais testé seul)."""
    if not lv_provided:
        return CRITERION_NOT_RUN, "live_validation.json non fourni — Phase 10 non déclenchée pour ce case"
    if lv_err or not isinstance(live_validation, dict):
        return CRITERION_NOT_RUN, lv_err or "live_validation.json ne contient pas un objet JSON"
    if live_validation.get("refused") is not False:
        return CRITERION_NOT_RUN, f"live_validation.json.refused={live_validation.get('refused')!r}"
    outcome = live_validation.get("outcome")
    if outcome == OUTCOME_FIX_CONFIRMED:
        return CRITERION_PASS, f"outcome={outcome!r}"
    if outcome == OUTCOME_BUG_PERSISTS:
        return CRITERION_FAIL, f"outcome={outcome!r}"
    return CRITERION_NOT_RUN, f"outcome={outcome!r} non concluant"


# ────────────────────────────── Règle de décision ────────────────────────────

def _confidence_and_reason(static_v: str, integrity_v: str, fix_v: str) -> "tuple[str, Optional[str]]":
    """Suite de règles ORDONNÉES — la première qui s'applique l'emporte, jamais
    une formule pondérée. Voir docstring du module pour la justification de
    chaque étape."""
    if static_v != CRITERION_PASS:
        return CONFIDENCE_REJECT, f"validation statique (Phase 8) = {static_v} — PASS requis"
    if integrity_v == CRITERION_FAIL:
        return (
            CONFIDENCE_REJECT,
            "intégrité des fonctions gelées (Phase 11, partie A) = FAIL — toujours dominant, "
            "même si le correctif ciblé est confirmé par ailleurs",
        )
    if fix_v == CRITERION_FAIL:
        return CONFIDENCE_REJECT, f"correctif confirmé sur le case ciblé = {fix_v}"

    if integrity_v != CRITERION_PASS:
        return CONFIDENCE_MEDIUM, f"intégrité des fonctions gelées = {integrity_v} — PASS requis pour HIGH"

    if fix_v == CRITERION_PASS:
        return CONFIDENCE_HIGH, None

    unmet = [f"correctif confirmé sur le case ciblé = {fix_v} (PASS requis pour HIGH)"]
    return CONFIDENCE_MEDIUM, "confiance MEDIUM — " + " ; ".join(unmet)


# ────────────────────────────────── Orchestration ─────────────────────────────

@dataclass
class ConfidenceScoreResult:
    case_id: str
    branch: "Optional[str]"
    inputs: dict
    criteria: dict
    confidence: str
    reason: "Optional[str]"
    warnings: "list[str]" = field(default_factory=list)
    core_review_required: bool = False
    core_changed_functions: "list[str]" = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "branch": self.branch,
            "inputs": self.inputs,
            "criteria": self.criteria,
            "confidence": self.confidence,
            "reason": self.reason,
            "warnings": self.warnings,
            "core_review_required": self.core_review_required,
            "core_changed_functions": self.core_changed_functions,
        }


def score_patch_confidence(
    *,
    worktree_manifest_path: "str | Path",
    validation_static_path: "str | Path",
    patch_replay_path: "str | Path",
    extractor_integrity_check_path: "str | Path",
    live_validation_path: "str | Path | None" = None,
) -> ConfidenceScoreResult:
    """Calcule le score de confiance à partir des artefacts déjà produits par
    les Phases 7 à 11. Lève ConfidenceScoreError uniquement pour une
    véritable erreur d'usage (case_id/branch incohérents, ou aucun case_id
    exploitable) — toute autre absence/illisibilité dégrade le critère
    concerné, jamais une exception."""
    worktree_manifest_path = Path(worktree_manifest_path)
    validation_static_path = Path(validation_static_path)
    patch_replay_path = Path(patch_replay_path)
    extractor_integrity_check_path = Path(extractor_integrity_check_path)
    lv_provided = live_validation_path is not None
    live_validation_path_obj = Path(live_validation_path) if lv_provided else None

    wt_data, wt_err = _load_json(worktree_manifest_path)
    vs_data, vs_err = _load_json(validation_static_path)
    pr_data, pr_err = _load_json(patch_replay_path)
    ei_data, ei_err = _load_json(extractor_integrity_check_path)
    lv_data, lv_err = _load_json(live_validation_path_obj) if lv_provided else (None, None)

    # ── case_id cohérent entre TOUS les artefacts effectivement fournis (ceux
    # qui existent et sont lisibles) ET le nom du dossier parent de chaque
    # chemin fourni (même principe que les Phases 9/10/11 : un chemin qui ne
    # correspond pas au case_id qu'il contient est le signe qu'on a mélangé
    # deux cases différents) — seule véritable erreur d'usage bloquante.
    case_id_signals: "dict[str, str]" = {
        "worktree_manifest_path.parent": worktree_manifest_path.parent.name,
        "validation_static_path.parent": validation_static_path.parent.name,
        "patch_replay_path.parent": patch_replay_path.parent.name,
        "extractor_integrity_check_path.parent": extractor_integrity_check_path.parent.name,
    }
    if lv_provided:
        case_id_signals["live_validation_path.parent"] = live_validation_path_obj.parent.name
    if isinstance(wt_data, dict) and not wt_err:
        case_id_signals["worktree.json"] = str(wt_data.get("case_id") or "")
    if isinstance(vs_data, dict) and not vs_err:
        case_id_signals["validation_static.json"] = str(vs_data.get("case_id") or "")
    if isinstance(pr_data, dict) and not pr_err:
        case_id_signals["patch_replay.json"] = str(pr_data.get("case_id") or "")
    if isinstance(ei_data, dict) and not ei_err:
        case_id_signals["extractor_integrity_check.json"] = str(ei_data.get("case_id") or "")
    if lv_provided and isinstance(lv_data, dict) and not lv_err:
        case_id_signals["live_validation.json"] = str(lv_data.get("case_id") or "")

    case_id_signals = {k: v for k, v in case_id_signals.items() if v}
    distinct_case_ids = set(case_id_signals.values())
    if len(distinct_case_ids) > 1:
        raise ConfidenceScoreError(f"case_id incohérent entre les artefacts/chemins fournis : {case_id_signals}")
    if not distinct_case_ids:
        raise ConfidenceScoreError(
            "impossible de déterminer un case_id exploitable à partir des artefacts/chemins fournis"
        )
    case_id = next(iter(distinct_case_ids))
    if any(sep in case_id for sep in ("/", "\\")) or ".." in case_id:
        raise ConfidenceScoreError(f"case_id {case_id!r} n'est pas un composant de chemin sûr")

    # ── branch cohérente entre les artefacts lisibles qui la portent ────────
    branch_signals: "dict[str, str]" = {}
    if isinstance(wt_data, dict) and not wt_err and wt_data.get("branch"):
        branch_signals["worktree.json"] = str(wt_data["branch"])
    if isinstance(vs_data, dict) and not vs_err and vs_data.get("branch"):
        branch_signals["validation_static.json"] = str(vs_data["branch"])
    if isinstance(pr_data, dict) and not pr_err:
        pr_branch = (pr_data.get("worktree") or {}).get("branch")
        if pr_branch:
            branch_signals["patch_replay.json"] = str(pr_branch)
    if isinstance(ei_data, dict) and not ei_err and ei_data.get("branch"):
        branch_signals["extractor_integrity_check.json"] = str(ei_data["branch"])
    if lv_provided and isinstance(lv_data, dict) and not lv_err:
        lv_branch = (lv_data.get("worktree") or {}).get("branch")
        if lv_branch:
            branch_signals["live_validation.json"] = str(lv_branch)

    distinct_branches = set(branch_signals.values())
    if len(distinct_branches) > 1:
        raise ConfidenceScoreError(f"branch incohérente entre les artefacts fournis : {branch_signals}")
    branch = next(iter(distinct_branches)) if distinct_branches else None

    # ── Quatre critères indépendants ─────────────────────────────────────────
    static_v, static_detail = _binary_verdict_criterion(vs_data, vs_err, "validation_static.json")
    integrity_v, integrity_detail = _integrity_criterion(ei_data, ei_err)
    if integrity_v == CRITERION_PASS and (
        not isinstance(wt_data, dict) or not wt_data.get("base_sha")
        or ei_data.get("base_sha") != wt_data.get("base_sha")
    ):
        integrity_v, integrity_detail = CRITERION_FAIL, "base_sha incohérent entre Phase 7 et Phase 11-A"
    fix_v, fix_detail, fix_source = _fix_confirmed_criterion(pr_data, pr_err, lv_data, lv_provided, lv_err)
    live_v, live_detail = _live_validation_criterion(lv_data, lv_provided, lv_err)

    confidence, reason = _confidence_and_reason(static_v, integrity_v, fix_v)

    warnings = [INTEGRITY_SCOPE_WARNING]
    if wt_err:
        warnings.append(f"worktree.json (Phase 7) non exploitable pour le recoupement case_id/branch : {wt_err}")

    criteria = {
        "static_validation": {
            "value": static_v, "detail": static_detail, "source": "validation_static.json",
        },
        "extractor_integrity": {
            "value": integrity_v, "detail": integrity_detail, "source": "extractor_integrity_check.json",
        },
        "fix_confirmed": {"value": fix_v, "detail": fix_detail, "source": fix_source},
        "live_validation": {
            "value": live_v, "detail": live_detail,
            "source": "live_validation.json" if lv_provided else None,
        },
    }

    inputs = {
        "worktree_manifest_path": str(worktree_manifest_path),
        "worktree_manifest_readable": wt_err is None,
        "validation_static_path": str(validation_static_path),
        "validation_static_readable": vs_err is None,
        "patch_replay_path": str(patch_replay_path),
        "patch_replay_readable": pr_err is None,
        "extractor_integrity_check_path": str(extractor_integrity_check_path),
        "extractor_integrity_check_readable": ei_err is None,
        "live_validation_path": str(live_validation_path_obj) if lv_provided else None,
        "live_validation_provided": lv_provided,
        "live_validation_readable": (lv_err is None) if lv_provided else None,
    }

    log_debug(
        _TAG,
        f"case={case_id} static={static_v} integrity={integrity_v} fix={fix_v} live={live_v}",
    )

    return ConfidenceScoreResult(
        case_id=case_id, branch=branch, inputs=inputs, criteria=criteria,
        confidence=confidence, reason=reason, warnings=warnings,
        core_review_required=integrity_v == CRITERION_PASS and bool(ei_data.get("review_required")),
        core_changed_functions=[row["key"] for row in ei_data["functions"] if row["state"] == "EXPECTED_CHANGE"]
        if integrity_v == CRITERION_PASS else [],
    )


def _output_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "confidence_score.json"


def write_patch_confidence(
    *,
    worktree_manifest_path: "str | Path",
    validation_static_path: "str | Path",
    patch_replay_path: "str | Path",
    extractor_integrity_check_path: "str | Path",
    live_validation_path: "str | Path | None" = None,
    out_root: "str | Path" = "confidence_scores",
    force: bool = False,
) -> Path:
    """Exécute score_patch_confidence et écrit
    out_root/<case_id>/confidence_score.json. Lève ConfidenceScoreExistsError
    si la sortie existe déjà et force=False — jamais d'écrasement silencieux.
    Ne modifie jamais aucun artefact d'une phase antérieure."""
    result = score_patch_confidence(
        worktree_manifest_path=worktree_manifest_path,
        validation_static_path=validation_static_path,
        patch_replay_path=patch_replay_path,
        extractor_integrity_check_path=extractor_integrity_check_path,
        live_validation_path=live_validation_path,
    )

    out_root = Path(out_root)
    out_dir, out_file = _output_paths(out_root, result.case_id)

    if out_dir.exists():
        if not force:
            raise ConfidenceScoreExistsError(
                f"score de confiance déjà existant : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise ConfidenceScoreError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de confidence_score.json) — suppression refusée, vérifier manuellement"
            )
        log_debug(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(_TAG, f"case={result.case_id} confidence={result.confidence} -> {out_file}")
    return out_file
