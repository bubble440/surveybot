from __future__ import annotations

"""Phase 9 — replay automatique du bug après patch, dans le worktree autofix
préparé par la Phase 7 (Survey/autofix/autofix_worktree.py), une fois la Phase 8
(validation statique) déjà passée pour ce case.

Lecture seule sur les Phases 4/7/8 : ce module ne recalcule et ne réexécute
jamais rien de ces phases — il lit uniquement les artefacts qu'elles ont déjà
produits (diagnosis.json, worktree.json, validation_static.json) et refuse
avant tout effet de bord si une condition manque. Il ne rouvre jamais
manifest.json directement : le seul chemin par lequel ce fichier est lu est
indirect, à l'intérieur des mécanismes de rejeu réutilisés tels quels
(Survey/autofix/failure_replay.py::replay_failure_case, Survey/autofix/replay_browser.py::
execute_case_action), qui en ont besoin pour leur propre fonctionnement déjà
existant — jamais une lecture indépendante par la logique propre à ce module
(case_id/stage viennent de diagnosis.json).

── Conventions Phase 7/Phase 8 vérifiées avant d'écrire ce module ────────────
Au moment de commencer ce patch, ni worktree.json (Phase 7) ni
Survey/autofix/static_validator.py (Phase 8) n'existaient encore dans ce dépôt (vérifié :
aucun fichier, aucun commit, aucune autre branche) — les deux ont été implémentés
en parallèle par un autre chantier et fusionnés dans cette branche pendant
l'écriture de ce module. Les schémas/CLI ci-dessous sont donc ceux réellement
vérifiés dans le code fusionné, jamais devinés :

  - worktree.json (Survey/autofix/autofix_worktree.py::write_worktree_manifest, Phase 7) :
    out_root/<case_id>/worktree.json — {schema_version, case_id, created_at,
    branch, worktree_path, base_sha, source_branch, prompt_path, warnings}.
    Ce module lit case_id/branch/worktree_path/base_sha, exactement les champs
    déjà présents.
  - validation_static.json (Survey/autofix/static_validator.py::write_static_validation,
    Phase 8) : out_root/<case_id>/validation_static.json — {schema_version,
    case_id, created_at, branch, base_sha, changed_files, checks, verdict
    ("ACCEPTED"|"REJECTED"), reasons, warnings}. Ce module exige verdict=
    "ACCEPTED", exactement la valeur positive déjà produite.
  - Convention CLI reprise de Survey/autofix/static_validator.py::validate_patch_static
    (façade tools/validate_patch_static.py) : le CHEMIN COMPLET vers
    worktree.json est pris tel quel en argument (pas un dossier qui le
    contiendrait) — Phase 8 lit ainsi l'artefact de Phase 7. Ce module reprend
    exactement la même convention pour ses propres arguments worktree.json ET
    validation_static.json (deux artefacts à fichier unique, comme celui que
    lit déjà Phase 8), tout en gardant diagnosis_dir comme un DOSSIER (comme
    Survey/autofix/autofix_worktree.py::check_eligibility le fait déjà pour ce même
    artefact Phase 4).

── Résolution de racine de paquet pour exécuter le code du worktree ──────────
Vérifié avant le point 2 de la demande : Survey/autofix/static_validator.py::
_resolve_package_root(repo_root) EXISTE et résout déjà ce même besoin pour la
Phase 8 (Survey/ et tools/ comme frères directs de repo_root ou d'un de ses
sous-dossiers directs, jamais une structure supposée nommée en dur) — réutilisée
ici TELLE QUELLE (import direct, aucune réimplémentation parallèle), appliquée
à worktree_path (le worktree Git complet, structure identique à celle que Phase
8 résout déjà sur ce même chemin). Aucune raison de ne pas la reprendre : elle
ne dépend que du worktree lui-même, jamais de Path(__file__) ni d'un appel Git
sur le dépôt principal — plus robuste que ce que ce module envisageait avant de
vérifier que Phase 8 existait déjà.

Racine résolue exécutée dans un SOUS-PROCESSUS Python dédié, jamais dans ce
process-ci. Raison, non une préférence : ce process a déjà importé
Survey.autofix.replay_browser/Survey.autofix.failure_replay/Survey.autofix.static_validator (pour leurs
constantes/fonctions) depuis le dépôt principal ; sys.modules les garde en
cache, donc un simple sys.path.insert() ici referait sortir du cache les
modules du dépôt principal, jamais ceux du worktree patché — un sous-processus
neuf est la seule façon fiable de garantir que le code réellement exécuté est
celui du worktree, sans deviner un mécanisme d'invalidation de cache fragile.

── Mécanisme de rejeu réutilisé tel quel, jamais réimplémenté ────────────────
  stage="extraction" : Survey.autofix.failure_replay.replay_failure_case(case_dir) —
    même fonction, même vocabulaire REPRODUIT/NON_REPRODUIT/DIFFERENT/
    NON_REJOUABLE que le rejeu passif déjà utilisé pour le signal avant-patch.
  stage="action"      : exactement la même séquence que Survey/autofix/
    failure_diagnosis.py::_attempt_real_dispatch_replay (IsolatedReplayBrowser
    -> load_case_document(pre_action=True) -> extract_case_blocks ->
    execute_case_action), toutes fonctions publiques de Survey/autofix/
    replay_browser.py, non modifiées, appelées avec les mêmes arguments.

── Traduction du verdict après patch en validation/rejet du patch ───────────
Vocabulaire réutilisé tel quel (Survey.autofix.replay_browser.OUTCOME_*), jamais une
seconde échelle de confiance : CORRECTIF_CONFIRME / BUG_PERSISTANT /
NON_CONCLUANT. Pour stage="action", ce vocabulaire est déjà calculé par
execute_case_action() lui-même (_action_outcome, non modifié) — repris tel
quel. Pour stage="extraction", il n'existe pas encore côté rejeu passif (qui
n'expose que le vocabulaire de fidélité) : ce module ajoute la seule
traduction manquante, symétrique de _action_outcome, sans réimplémenter le
pari dispatcher_success/DOM que ce vocabulaire sert ailleurs à traduire (il
n'existe pas de pari équivalent pour l'extraction, seulement un comptage
d'issues déjà tranché par validate_question_blocks()). Seul CORRECTIF_CONFIRME
valide le patch : un TIMEOUT (stage=action) reste NON_CONCLUANT même si
trace_replay contient un signal favorable — un replay partiel n'est jamais une
preuve certaine (cf. Phase 3D, Utils/SURVEYBOT_AUTOFIX_PLAN.md).

── Portée explicitement exclue de ce patch ───────────────────────────────────
Rejouer en plus des cas historiques voisins déjà validés (suite de
non-régression DOM) : sous-chantier différé, pas traité ici. Cette phase ne
rejoue que le case ciblé par le worktree.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from Survey.autofix.failure_replay import VERDICT_NON_REPRODUIT, VERDICT_REPRODUIT
from Survey.log_utils import log_debug, log_info
from Survey.autofix.replay_browser import (
    OUTCOME_BUG_PERSISTS,
    OUTCOME_FIX_CONFIRMED,
    OUTCOME_INCONCLUSIVE,
    _DEFAULT_DISPATCH_BUDGET_S,
)
from Survey.autofix.static_validator import StaticValidationError, _resolve_package_root

_TAG = "[PATCH_REPLAY]"
SCHEMA_VERSION = "1.0"

# Budget explicite du sous-processus de rejeu — stage="extraction" (rejeu
# statique, sans navigateur : rapide, mais borné quand même, jamais illimité).
DEFAULT_EXTRACTION_TIMEOUT_S = 60.0
# stage="action" : marge au-dessus du budget interne du dispatcher lui-même
# (démarrage Chromium, extraction, fermeture) — le sous-processus doit pouvoir
# se terminer proprement même si le dispatcher va jusqu'au bout de son budget.
_ACTION_SUBPROCESS_MARGIN_S = 60.0

# Un seul script par stage, jamais de cascade — les chemins (racine du worktree,
# dossier du case) sont passés en argv, jamais interpolés dans le texte du script
# (évite tout risque d'injection/quoting sur un chemin contenant des caractères
# spéciaux).
_EXTRACTION_RUNNER_SCRIPT = """
import json
import sys

worktree_root, case_dir = sys.argv[1], sys.argv[2]
sys.path.insert(0, worktree_root)

try:
    from Survey.autofix.failure_replay import replay_failure_case
    result = replay_failure_case(case_dir).as_dict()
except Exception as exc:
    result = {"verdict": None, "error": f"{type(exc).__name__}: {exc}"}

print(json.dumps(result))
"""

_ACTION_RUNNER_SCRIPT = """
import json
import sys

worktree_root, case_dir, budget_s = sys.argv[1], sys.argv[2], float(sys.argv[3])
sys.path.insert(0, worktree_root)

try:
    from Survey.autofix.replay_browser import (
        _ACTION_REPLAY_EXECUTE_SCRIPTS,
        IsolatedReplayBrowser,
        ReplayBrowserError,
        execute_case_action,
        extract_case_blocks,
    )
    with IsolatedReplayBrowser() as browser:
        page = browser.load_case_document(
            case_dir, pre_action=True, execute_scripts=_ACTION_REPLAY_EXECUTE_SCRIPTS,
        )
        extraction = extract_case_blocks(page, case_dir)
        execution = execute_case_action(
            page, case_dir, budget_s=budget_s, question_blocks=extraction.blocks,
        )
    result = {
        "status": execution.status,
        "reason": execution.reason,
        "dispatcher_success": execution.dispatcher_success,
        "duration_s": execution.duration_s,
        "budget_s": execution.budget_s,
        "extraction_error": extraction.error,
        "validation_comparison": execution.validation_comparison,
        "validation_error": execution.validation_error,
        "trace_replay": execution.trace_replay,
        "dispatcher_steps": execution.dispatcher_steps,
        "execute_scripts": _ACTION_REPLAY_EXECUTE_SCRIPTS,
    }
except Exception as exc:
    result = {"status": None, "error": f"{type(exc).__name__}: {exc}"}

print(json.dumps(result))
"""


class PatchReplayError(Exception):
    """Erreur d'utilisation (chemins invalides, sortie déjà existante). Distincte
    d'un refus contrôlé (PatchReplayResult.refused=True), qui n'est jamais une
    exception."""


class PatchReplayExistsError(PatchReplayError):
    """La sortie cible existe déjà et la régénération n'a pas été demandée."""


def _load_json(path: Path) -> Tuple[Any, Optional[str]]:
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
    stage: Optional[str]
    reasons: List[str] = field(default_factory=list)
    worktree: Optional[dict] = None
    diagnosis: Optional[dict] = None


def check_preconditions(
    *,
    failure_case_dir: "str | Path",
    diagnosis_dir: "str | Path",
    worktree_manifest_path: "str | Path",
    validation_static_path: "str | Path",
) -> PreconditionResult:
    """Vérifie toutes les conditions ensemble ; ne s'arrête jamais à la première
    raison rencontrée — la liste complète est retournée, comme Survey/autofix/
    autofix_worktree.py::check_eligibility (Phase 7), jamais un résultat partiel.
    Lecture seule : ne recalcule ni la Phase 4, ni la Phase 7, ni la Phase 8.

    worktree_manifest_path/validation_static_path sont les CHEMINS COMPLETS
    vers worktree.json/validation_static.json (pas un dossier qui les
    contiendrait) — même convention que Survey/autofix/static_validator.py::
    validate_patch_static (Phase 8) pour son propre argument worktree.json."""
    failure_case_dir = Path(failure_case_dir)
    diagnosis_dir = Path(diagnosis_dir)
    worktree_manifest_path = Path(worktree_manifest_path)
    validation_static_path = Path(validation_static_path)
    reasons: List[str] = []

    validation, validation_err = _load_json(validation_static_path)
    if validation_err or not isinstance(validation, dict):
        reasons.append(f"validation_static.json (Phase 8) {validation_err or 'ne contient pas un objet JSON'}")
    elif validation.get("verdict") != "ACCEPTED":
        reasons.append(
            f"validation_static.json.verdict={validation.get('verdict')!r} — \"ACCEPTED\" requis "
            "(la Phase 8 doit avoir accepté le patch avant tout replay ; jamais recalculé ici)"
        )

    worktree, worktree_err = _load_json(worktree_manifest_path)
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

    diagnosis, diagnosis_err = _load_json(diagnosis_dir / "diagnosis.json")
    stage: Optional[str] = None
    if diagnosis_err or not isinstance(diagnosis, dict):
        reasons.append(f"diagnosis.json (Phase 4) {diagnosis_err or 'ne contient pas un objet JSON'}")
    else:
        stage = str(diagnosis.get("stage") or "")
        if stage not in ("extraction", "action"):
            reasons.append(f"diagnosis.json.stage={stage!r} — seuls \"extraction\" et \"action\" sont dans le périmètre")
        elif stage == "extraction":
            verdict = str((diagnosis.get("replay") or {}).get("verdict") or "")
            if verdict != "REPRODUIT":
                reasons.append(
                    f"stage=\"extraction\" : diagnosis.replay.verdict={verdict!r} — \"REPRODUIT\" requis "
                    "comme signal avant-patch déjà établi par la Phase 4 (jamais revérifié ici)"
                )
        elif stage == "action":
            real_dispatch = diagnosis.get("real_dispatch_replay")
            outcome = (
                (real_dispatch.get("validation_comparison") or {}).get("outcome")
                if isinstance(real_dispatch, dict) else None
            )
            if outcome != OUTCOME_BUG_PERSISTS:
                reasons.append(
                    f"stage=\"action\" : diagnosis.real_dispatch_replay.validation_comparison.outcome="
                    f"{outcome!r} — {OUTCOME_BUG_PERSISTS!r} requis comme signal avant-patch déjà établi "
                    "par la Phase 4 (jamais revérifié ici)"
                )

    # case_id cohérent entre toutes les sources. manifest.json n'est jamais ouvert
    # ici : failure_case_dir n'est vérifié que par son nom de dossier. worktree.json/
    # validation_static.json étant des CHEMINS DE FICHIER (out_root/<case_id>/...),
    # c'est le nom de leur dossier parent qui porte le case_id, pas leur propre nom.
    case_ids = {
        "diagnosis.json": str(diagnosis.get("case_id") or "") if isinstance(diagnosis, dict) else "",
        "worktree.json": str(worktree.get("case_id") or "") if isinstance(worktree, dict) else "",
        "validation_static.json": str(validation.get("case_id") or "") if isinstance(validation, dict) else "",
        "failure_case_dir": failure_case_dir.name,
        "diagnosis_dir": diagnosis_dir.name,
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

    if not failure_case_dir.is_dir():
        reasons.append(f"failure_case_dir introuvable : {failure_case_dir}")

    return PreconditionResult(
        satisfied=not reasons,
        case_id=resolved_case_id,
        stage=stage,
        reasons=reasons,
        worktree=worktree if isinstance(worktree, dict) else None,
        diagnosis=diagnosis if isinstance(diagnosis, dict) else None,
    )


def _resolve_worktree_package_root(worktree_path: Path) -> Tuple[Optional[Path], Optional[str]]:
    """Réutilise TELLE QUELLE Survey.autofix.static_validator._resolve_package_root
    (Phase 8, non modifiée) — même besoin exact (localiser Survey/ et tools/
    comme frères directs de worktree_path ou d'un sous-dossier direct), déjà
    résolu par cette phase sur ce même worktree_path. Jamais une seconde
    implémentation : seule la conversion exception -> (None, raison), pour
    rester dans la même convention de retour que _run_replay_subprocess
    ci-dessous, est propre à ce module."""
    try:
        return _resolve_package_root(worktree_path), None
    except StaticValidationError as exc:
        return None, str(exc)


def _run_replay_subprocess(
    script: str, args: List[str], timeout_s: float
) -> Tuple[Optional[dict], Optional[str]]:
    """Stratégie d'exécution UNIQUE et partagée par les deux stages : un seul
    sous-processus Python (sys.executable — jamais un "python"/"python3" supposé
    présent sur le PATH), un seul essai, un budget de temps explicite. Retourne
    (résultat JSON, None) en cas de succès, ou (None, raison) sinon — jamais
    d'exception propagée, jamais de processus laissé pendre."""
    fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="patch_replay_runner_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(script)
        try:
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", tmp_path, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log_debug(_TAG, f"sous-processus de rejeu expiré après {timeout_s}s — abandon contrôlé")
            return None, f"sous-processus de rejeu expiré après {timeout_s:.1f}s (budget dépassé)"
        except OSError as exc:
            return None, f"sous-processus de rejeu indisponible ({exc})"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    stdout, stderr = completed.stdout or "", completed.stderr or ""
    if "\ufffd" in stdout or "\ufffd" in stderr:
        return None, "sortie UTF-8 invalide du sous-processus de rejeu"
    if completed.returncode != 0:
        return None, (
            f"sous-processus de rejeu terminé avec le code {completed.returncode} : "
            f"{stderr.strip()[-2000:]}"
        )
    try:
        return json.loads(stdout.strip().splitlines()[-1]), None
    except (ValueError, IndexError) as exc:
        return None, f"sortie du sous-processus non JSON ({exc}) : {stdout[-500:]!r}"


@dataclass
class PatchReplayResult:
    case_id: str
    stage: str
    refused: bool
    refusal_reasons: List[str] = field(default_factory=list)
    before_signal: Optional[dict] = None
    after_replay: Optional[dict] = None
    after_replay_error: Optional[str] = None
    outcome: Optional[str] = None
    outcome_reason: Optional[str] = None
    patch_validated: bool = False
    worktree_path: Optional[str] = None
    branch: Optional[str] = None
    base_sha: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        result = {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stage": self.stage,
            "refused": self.refused,
            "refusal_reasons": self.refusal_reasons,
            "before_signal": self.before_signal,
            "after_replay": self.after_replay,
            "after_replay_error": self.after_replay_error,
            "outcome": self.outcome,
            "patch_validated": self.patch_validated,
            "worktree": {
                "path": self.worktree_path,
                "branch": self.branch,
                "base_sha": self.base_sha,
            },
            "warnings": self.warnings,
        }
        if self.outcome_reason is not None:
            result["outcome_reason"] = self.outcome_reason
        return result


def replay_patch(
    *,
    failure_case_dir: "str | Path",
    diagnosis_dir: "str | Path",
    worktree_manifest_path: "str | Path",
    validation_static_path: "str | Path",
    dispatch_budget_s: Optional[float] = None,
    extraction_timeout_s: Optional[float] = None,
) -> PatchReplayResult:
    """Rejoue le case ciblé par le worktree autofix (code patché), et compare au
    signal avant-patch déjà connu (Phase 4). Ne valide le patch (patch_validated=
    True) que si le verdict après patch confirme ACTIVEMENT la correction
    (outcome=CORRECTIF_CONFIRME) — jamais une absence de reproduction ambiguë, un
    verdict non concluant, ou un dépassement de budget."""
    pre = check_preconditions(
        failure_case_dir=failure_case_dir,
        diagnosis_dir=diagnosis_dir,
        worktree_manifest_path=worktree_manifest_path,
        validation_static_path=validation_static_path,
    )
    if not pre.satisfied:
        log_debug(_TAG, f"refus contrôlé, aucun effet de bord : {pre.reasons}")
        return PatchReplayResult(
            case_id=pre.case_id or Path(failure_case_dir).name,
            stage=pre.stage or "unknown",
            refused=True,
            refusal_reasons=pre.reasons,
        )

    worktree = pre.worktree or {}
    diagnosis = pre.diagnosis or {}
    worktree_path = str(worktree["worktree_path"])
    stage = pre.stage or "unknown"
    case_id = pre.case_id or Path(failure_case_dir).name
    case_dir_str = str(Path(failure_case_dir).resolve())

    package_root, resolve_err = _resolve_worktree_package_root(Path(worktree_path))

    if stage == "extraction":
        before_signal = {"replay_verdict": (diagnosis.get("replay") or {}).get("verdict")}
        timeout_s = float(extraction_timeout_s) if extraction_timeout_s else DEFAULT_EXTRACTION_TIMEOUT_S
        if resolve_err:
            after, err = None, resolve_err
        else:
            after, err = _run_replay_subprocess(
                _EXTRACTION_RUNNER_SCRIPT,
                [str(package_root), case_dir_str],
                timeout_s,
            )
    else:  # stage == "action" (seules deux valeurs possibles après check_preconditions)
        before_signal = {
            "real_dispatch_outcome": (
                (diagnosis.get("real_dispatch_replay") or {}).get("validation_comparison") or {}
            ).get("outcome"),
        }
        budget_s = float(dispatch_budget_s) if dispatch_budget_s else _DEFAULT_DISPATCH_BUDGET_S
        if resolve_err:
            after, err = None, resolve_err
        else:
            after, err = _run_replay_subprocess(
                _ACTION_RUNNER_SCRIPT,
                [str(package_root), case_dir_str, str(budget_s)],
                budget_s + _ACTION_SUBPROCESS_MARGIN_S,
            )

    result = PatchReplayResult(
        case_id=case_id,
        stage=stage,
        refused=False,
        before_signal=before_signal,
        worktree_path=worktree_path,
        branch=worktree.get("branch"),
        base_sha=worktree.get("base_sha"),
    )

    if err:
        result.after_replay_error = err
        result.outcome = OUTCOME_INCONCLUSIVE
        result.patch_validated = False
        log_info(_TAG, f"case={case_id} rejeu après patch non concluant ({err[:150]})")
        return result

    result.after_replay = after
    if stage == "extraction":
        verdict = (after or {}).get("verdict")
        if verdict == VERDICT_NON_REPRODUIT:
            outcome = OUTCOME_FIX_CONFIRMED
        elif verdict == VERDICT_REPRODUIT:
            outcome = OUTCOME_BUG_PERSISTS
        else:  # DIFFERENT, NON_REJOUABLE, ou absent/erreur du sous-processus
            outcome = OUTCOME_INCONCLUSIVE
    else:
        status = (after or {}).get("status")
        comparison = (after or {}).get("validation_comparison")
        if status in ("SUCCESS", "FAILURE") and isinstance(comparison, dict) and comparison.get("outcome"):
            outcome = comparison["outcome"]
        else:
            outcome = OUTCOME_INCONCLUSIVE
            if status == "TIMEOUT":
                result.warnings.append(
                    "budget de temps dépassé pendant le rejeu après patch — traité comme un rejet, "
                    "même si trace_replay porte un signal favorable (jamais une confirmation active, "
                    "cf. Phase 3D)"
                )
        steps = (after or {}).get("dispatcher_steps")
        decision_steps = (
            steps if isinstance(steps, list) and len(steps) <= 24 and "capture=truncated" not in steps else []
        )
        last_decision = next(
            (index for index in range(len(decision_steps) - 1, -1, -1)
             if isinstance(decision_steps[index], str)
             and decision_steps[index].startswith("action_fix ")),
            None,
        )
        if (
            status == "FAILURE"
            and (after or {}).get("execute_scripts") is False
            and outcome == OUTCOME_BUG_PERSISTS
            and last_decision is not None and last_decision > 0
            and isinstance(decision_steps[last_decision - 1], str)
            and re.fullmatch(
                r"action_fix selected fix_id=[a-z][a-z0-9_]{2,63}", decision_steps[last_decision - 1]
            )
            and decision_steps[last_decision]
            == "action_fix verdict=HANDLED_FAILURE reason=handler_returned_failure"
        ):
            outcome = OUTCOME_INCONCLUSIVE
            result.outcome_reason = "correctif d'action non vérifiable sans les scripts de la page"

    result.outcome = outcome
    result.patch_validated = outcome == OUTCOME_FIX_CONFIRMED
    log_info(
        _TAG,
        f"case={case_id} stage={stage} outcome={outcome} patch_validated={result.patch_validated}",
    )
    return result


def write_patch_replay(
    *,
    failure_case_dir: "str | Path",
    diagnosis_dir: "str | Path",
    worktree_manifest_path: "str | Path",
    validation_static_path: "str | Path",
    out_root: "str | Path" = "patch_replays",
    force: bool = False,
    dispatch_budget_s: Optional[float] = None,
    extraction_timeout_s: Optional[float] = None,
) -> Path:
    """Rejoue le patch (replay_patch) et écrit out_root/<case_id>/patch_replay.json.
    Ne modifie jamais failure_case_dir/diagnosis_dir/worktree_manifest_path/
    validation_static_path. Lève PatchReplayExistsError si la sortie existe déjà
    et force=False — jamais d'écrasement silencieux (même convention que
    Survey/autofix/replayability_classifier.py)."""
    failure_case_dir = Path(failure_case_dir)
    out_root = Path(out_root)
    out_dir = out_root / failure_case_dir.name
    out_file = out_dir / "patch_replay.json"

    if out_dir.exists():
        if not force:
            raise PatchReplayExistsError(
                f"résultat déjà existant : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise PatchReplayError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de patch_replay.json) — suppression refusée, vérifier manuellement"
            )
        log_info(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    result = replay_patch(
        failure_case_dir=failure_case_dir,
        diagnosis_dir=diagnosis_dir,
        worktree_manifest_path=worktree_manifest_path,
        validation_static_path=validation_static_path,
        dispatch_budget_s=dispatch_budget_s,
        extraction_timeout_s=extraction_timeout_s,
    )

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    log_info(
        _TAG,
        f"résultat écrit case={result.case_id} refused={result.refused} outcome={result.outcome} "
        f"-> {out_file}",
    )
    return out_file
