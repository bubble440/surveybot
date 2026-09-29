from __future__ import annotations

"""Phase 3D — classification automatique de rejouabilité d'un failure case déjà
diagnostiqué (Phase 4, Survey/autofix/failure_diagnosis.py).

Lecture seule : ce module ne recalcule ni ne réexécute rien (aucun replay, aucun
dispatcher, aucun navigateur). Il lit exclusivement diagnosis.json, déjà produit par
Survey/autofix/failure_diagnosis.py, et classe le case dans l'un des quatre modes déjà nommés
par Utils/SURVEYBOT_AUTOFIX_PLAN.md (Phase 3D) :

  STATIC_DOM               structure DOM pure suffisante (Survey/autofix/failure_replay.py,
                            rejeu statique 3A, réellement réexécuté pour stage=
                            "extraction" — jamais un booléen réutilisé).
  BROWSER_CAPSULE           HTML statique insuffisant, mais le Chromium isolé
                            (Survey/autofix/replay_browser.py, Phase 3C.4, exposé dans
                            diagnosis.json::real_dispatch_replay) a réellement pu
                            rejouer l'interaction jusqu'à un verdict exploitable.
  TRACE_REPLAY              l'interaction elle-même n'a pas pu être réexécutée
                            fidèlement (budget dépassé), mais l'état avant/après déjà
                            capturé (runtime_state.json) a suffi, via le repli déjà
                            existant (Survey/autofix/replay_browser.py::_trace_replay_fallback,
                            exposé dans real_dispatch_replay.trace_replay).
  EXTERNAL_NON_REPLAYABLE   dépendance intrinsèque à une donnée externe non capturable
                            de façon fiable. Aucun signal de ce type n'existe encore
                            dans ce pipeline (donnée de session, captcha, shadow DOM
                            fermé...) — ce mode reste donc structurellement défini
                            (vocabulaire du plan) mais n'est actuellement jamais
                            produit par ce classificateur : l'inventer sur un signal
                            absent serait deviner, ce que ce chantier interdit
                            explicitement. À ouvrir quand un signal réel existera
                            (cf. Phase 3D d'Utils/SURVEYBOT_AUTOFIX_PLAN.md).
  UNDETERMINED              aucun signal déjà disponible ne permet de choisir une
                            catégorie avec une confiance suffisante — jamais un
                            classement par défaut dans l'une des quatre ci-dessus.

Signaux réutilisés tels quels (jamais recalculés, jamais réinterprétés) :
  - diagnosis["replay"]["verdict"] / ["evaluate_declined"] — Survey/autofix/failure_replay.py,
    rejeu statique passif (3A), non modifié.
  - diagnosis["real_dispatch_replay"]["status"] / ["trace_replay"] — Survey/autofix/
    failure_diagnosis.py (réexécution réelle du dispatcher, stage="action") /
    Survey/autofix/replay_browser.py (execute_case_action, _trace_replay_fallback), non
    modifiés.

Pourquoi stage="action" ne peut jamais atteindre STATIC_DOM ici : le rejeu passif
(Survey/autofix/failure_replay.py) réutilise dispatcher_success du case d'origine tel quel
pour ce stage (jamais de dispatcher réellement réexécuté) — un verdict REPRODUIT y
est donc attendu par construction et ne prouve rien sur la structure DOM seule (cf.
Survey/autofix/failure_diagnosis.py). Seul le signal real_dispatch_replay, qui fait tourner
un Chromium réel, peut établir BROWSER_CAPSULE/TRACE_REPLAY pour ce stage.
Symétriquement, stage="extraction" n'a aujourd'hui aucun signal de vérification par
navigateur réel exposé dans diagnosis.json (Survey/autofix/replay_browser.py sait déjà
réexécuter l'extraction sur une page réelle, Phase 3C.3, mais Survey/autofix/
failure_diagnosis.py ne l'invoque que pour stage="action" à ce jour) : un case
extraction qui ne relève pas de STATIC_DOM reste donc UNDETERMINED plutôt que
BROWSER_CAPSULE deviné.
"""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info

_TAG = "[REPLAYABILITY]"
SCHEMA_VERSION = "1.0"

MODE_STATIC_DOM = "STATIC_DOM"
MODE_BROWSER_CAPSULE = "BROWSER_CAPSULE"
MODE_TRACE_REPLAY = "TRACE_REPLAY"
MODE_EXTERNAL_NON_REPLAYABLE = "EXTERNAL_NON_REPLAYABLE"
MODE_UNDETERMINED = "UNDETERMINED"

# Statuts de real_dispatch_replay.status pour lesquels le dispatcher réel a rendu un
# verdict exploitable (booléen) dans le Chromium isolé — cf. Survey/autofix/replay_browser.py.
_REAL_DISPATCH_CONCLUSIVE_STATUSES = ("SUCCESS", "FAILURE")


class ReplayabilityError(Exception):
    """Échec contrôlé de classification."""


class ReplayabilityExistsError(ReplayabilityError):
    """La sortie cible existe déjà et la régénération n'a pas été demandée."""


@dataclass
class ReplayabilityClassification:
    case_id: str
    stage: str
    mode: str
    reason: str
    signals: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stage": self.stage,
            "mode": self.mode,
            "reason": self.reason,
            "signals": self.signals,
        }


def _classify_extraction(replay: dict) -> tuple[str, str]:
    verdict = replay.get("verdict")
    declined = replay.get("evaluate_declined")
    if verdict == "REPRODUIT" and declined in (0, None):
        return MODE_STATIC_DOM, (
            "stage=\"extraction\" : replay passif (Survey/autofix/failure_replay.py) verdict="
            f"REPRODUIT sans aucun evaluate() décliné (evaluate_declined={declined!r}) — "
            "la structure DOM seule a suffi à réexécuter l'extraction et le validator."
        )
    return MODE_UNDETERMINED, (
        f"stage=\"extraction\" : replay passif verdict={verdict!r}, "
        f"evaluate_declined={declined!r} — aucun signal déjà disponible ne confirme que "
        "la structure DOM seule suffit (verdict différent de REPRODUIT, ou impact des "
        "evaluate() déclinés sur ce verdict non établi), et aucune vérification par "
        "navigateur réel n'est aujourd'hui exposée dans diagnosis.json pour ce stage "
        "(Survey/autofix/replay_browser.py::extract_case_blocks existe mais n'est invoqué par "
        "Survey/autofix/failure_diagnosis.py que pour stage=\"action\")."
    )


def _classify_action(real_dispatch: Optional[dict]) -> tuple[str, str]:
    if not isinstance(real_dispatch, dict):
        return MODE_UNDETERMINED, (
            "stage=\"action\" : real_dispatch_replay absent (réexécution réelle du "
            "dispatcher non tentée ou pré-requis absents, cf. Survey/"
            "failure_diagnosis.py) — aucun signal de rejouabilité par navigateur réel "
            "disponible."
        )

    status = real_dispatch.get("status")
    if status in _REAL_DISPATCH_CONCLUSIVE_STATUSES:
        return MODE_BROWSER_CAPSULE, (
            f"stage=\"action\" : real_dispatch_replay.status={status!r} — le dispatcher "
            "réel a été réexécuté jusqu'à un verdict exploitable dans le Chromium isolé "
            "(Survey/autofix/replay_browser.py::execute_case_action) ; le HTML statique seul "
            "aurait été insuffisant pour ce stage (Survey/autofix/failure_replay.py réutilise "
            "dispatcher_success d'origine, jamais un dispatcher réellement réexécuté)."
        )

    if status == "TIMEOUT":
        trace_replay = real_dispatch.get("trace_replay")
        if isinstance(trace_replay, dict) and trace_replay.get("available") is True:
            return MODE_TRACE_REPLAY, (
                "stage=\"action\" : real_dispatch_replay.status=\"TIMEOUT\" (budget "
                "dépassé, interaction non réexécutée fidèlement), mais le repli sur les "
                "faits déjà capturés (real_dispatch_replay.trace_replay.available=true, "
                "Survey/autofix/replay_browser.py::_trace_replay_fallback) a produit une "
                "comparaison exploitable sans nouvelle exécution."
            )
        return MODE_UNDETERMINED, (
            "stage=\"action\" : real_dispatch_replay.status=\"TIMEOUT\" sans repli "
            f"trace_replay exploitable (trace_replay={trace_replay!r}) — ni une "
            "réexécution fidèle, ni un état avant/après suffisant ne sont disponibles."
        )

    return MODE_UNDETERMINED, (
        f"stage=\"action\" : real_dispatch_replay.status={status!r} — ni un verdict de "
        "dispatch exploitable (SUCCESS/FAILURE), ni un TIMEOUT avec repli trace_replay "
        "exploitable : aucun signal déjà disponible ne confirme une catégorie."
    )


def classify_replayability(diagnosis: dict) -> ReplayabilityClassification:
    """Classe un case déjà diagnostiqué (Phase 4) selon les quatre modes de
    rejouabilité de la Phase 3D, à partir des seuls signaux déjà présents dans
    `diagnosis` — jamais recalculés, jamais devinés. Ne lève jamais : un diagnostic
    incomplet/inattendu donne UNDETERMINED avec la raison exacte, pas une exception.
    """
    case_id = str(diagnosis.get("case_id") or "")
    stage = str(diagnosis.get("stage") or "unknown")
    replay = diagnosis.get("replay") if isinstance(diagnosis.get("replay"), dict) else {}
    real_dispatch = diagnosis.get("real_dispatch_replay")
    real_dispatch = real_dispatch if isinstance(real_dispatch, dict) else None

    if stage == "extraction":
        mode, reason = _classify_extraction(replay)
    elif stage == "action":
        mode, reason = _classify_action(real_dispatch)
    else:
        mode, reason = MODE_UNDETERMINED, (
            f"stage={stage!r} — aucune règle de classification connue pour ce stage."
        )

    trace_replay = real_dispatch.get("trace_replay") if real_dispatch else None
    signals = {
        "replay_verdict": replay.get("verdict"),
        "replay_evaluate_declined": replay.get("evaluate_declined"),
        "real_dispatch_status": real_dispatch.get("status") if real_dispatch else None,
        "real_dispatch_trace_replay_available": (
            trace_replay.get("available") if isinstance(trace_replay, dict) else None
        ),
    }

    log_debug(_TAG, f"case={case_id} stage={stage} mode={mode} signals={signals}")

    return ReplayabilityClassification(
        case_id=case_id, stage=stage, mode=mode, reason=reason, signals=signals,
    )


def _load_diagnosis(diagnosis_dir: Path) -> dict:
    path = diagnosis_dir / "diagnosis.json"
    if not path.is_file():
        raise ReplayabilityError(f"diagnosis.json absent : {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReplayabilityError(f"diagnosis.json illisible/invalide ({exc})") from exc
    if not isinstance(data, dict):
        raise ReplayabilityError("diagnosis.json ne contient pas un objet JSON")
    return data


def write_classification(
    diagnosis_dir: "str | Path",
    *,
    out_root: "str | Path" = "replayability",
    force: bool = False,
) -> Path:
    """Classe le diagnostic déjà produit sous diagnosis_dir/diagnosis.json et écrit
    out_root/<case_id>/classification.json. Ne modifie jamais diagnosis_dir. Lève
    ReplayabilityExistsError si la sortie existe déjà et force=False — jamais
    d'écrasement silencieux."""
    diagnosis_dir = Path(diagnosis_dir)
    diagnosis = _load_diagnosis(diagnosis_dir)

    out_root = Path(out_root)
    out_dir = out_root / diagnosis_dir.name
    out_file = out_dir / "classification.json"

    if out_dir.exists():
        if not force:
            raise ReplayabilityExistsError(
                f"classification déjà existante : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise ReplayabilityError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet "
                "outil (pas de classification.json) — suppression refusée, vérifier "
                "manuellement"
            )
        log_info(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    result = classify_replayability(diagnosis)

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_info(
        _TAG,
        f"classification créée case={result.case_id} stage={result.stage} "
        f"mode={result.mode} -> {out_file}",
    )
    return out_file
