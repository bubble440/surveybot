from __future__ import annotations

"""Enregistrement passif des anomalies détectées par les validators Phase 1A."""

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info


def record_validation_failure(
    driver,
    *,
    stage: str,
    report: dict,
    question_blocks: Any = None,
    actions: Any = None,
    pre_action_dom: Optional[str] = None,
    runtime_state: Any = None,
    action_trace: Any = None,
) -> Optional[str]:
    """Capture le contexte d'un validator en échec, sans jamais casser le survey.

    Réutilise page_snapshot comme source unique de capture DOM/frame/screenshot.
    Les artefacts propres à l'observabilité sont ajoutés dans le même dossier.

    runtime_state/action_trace (Browser Capsule, Phase 3B, additifs) : fournis
    par le hook d'action de page_snapshot.py via Survey/browser_capsule.py.
    Optionnels (None par défaut) — absents, ils ne changent rien au comportement
    existant ; fournis, ils sont écrits en plus des artefacts déjà produits,
    jamais à leur place.
    """
    if not isinstance(report, dict) or report.get("ok", True):
        return None

    try:
        from Survey.page_snapshot import dump_page_snapshot

        folder = dump_page_snapshot(
            driver,
            reason=f"{stage}_validation_failure",
            question_blocks=question_blocks,
            artifact_profile="action_validation" if stage == "action" else None,
            pre_action_dom=pre_action_dom if stage == "action" else None,
        )
        out = Path(folder)
        (out / "validation_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if actions is not None:
            (out / "actions_requested.json").write_text(
                json.dumps(actions, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if runtime_state is not None:
            (out / "runtime_state.json").write_text(
                json.dumps(runtime_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if action_trace is not None:
            (out / "action_trace.json").write_text(
                json.dumps(action_trace, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        issue_types = [
            str(i.get("failure_type") or "unknown")
            for i in (report.get("issues") or [])
            if isinstance(i, dict)
        ]
        log_info(
            "[OBSERVABILITY]",
            f"validation_failure stage={stage} issues={issue_types} snapshot={folder}",
        )
        return folder
    except Exception as exc:
        # L'observabilité ne doit jamais devenir une nouvelle cause d'échec survey.
        log_debug("[OBSERVABILITY]", f"failure recorder error stage={stage}: {type(exc).__name__}: {exc}")
        return None


# =============================================================================
# BDL-2b — MODE COLLECTE (plans d'actions jugés PASS)
# =============================================================================
# Pourquoi : record_validation_failure() n'écrit que si le validator a déjà
# conclu à un échec. Les plans PASS ne laissent donc aucune trace, ce qui interdit
# de mesurer les faux négatifs du validator (BDL-6). Ce mode persiste, pour les
# plans PASS uniquement, les FAITS RUNTIME avant/après (pas de DOM complet, pas de
# screenshot : réservés aux échecs).
#
# Isolation : racine DISTINCTE de snapshots/ (jamais lue par l'Autofix, qui ne
# traite que des dossiers de failure cases) et noms de dossier sans le motif
# "*_validation_failure" ; meta.json porte kind="bdl_collect_pass".
#
# Garanties : jamais d'exception vers l'appelant (le survey n'est jamais impacté),
# volume borné par un plafond de dossiers avec abandon contrôlé et log unique.

BDL_COLLECT_KIND = "bdl_collect_pass"
_BDL_COLLECT_DEFAULT_MAX = 200

# État de processus : compteur de dossiers déjà présents/écrits (initialisé une
# seule fois par racine, pour éviter de re-lister le disque à chaque plan) et
# mémoire du log "plafond atteint" (1 seule ligne info par racine).
_collect_count_by_root: dict = {}
_collect_cap_logged: set = set()


def _bdl_collect_root() -> Path:
    """Racine de collecte, distincte de la racine des snapshots d'échec."""
    # Local sous ./bdl_collect, hors-local sous /tmp/bdl_collect (même logique
    # que page_snapshot pour ./snapshots et /tmp/snapshots).
    from config import RUN_ENV

    default_root = "./bdl_collect" if RUN_ENV != "prod" else "/tmp/bdl_collect"
    return Path(os.getenv("SURVEY_BDL_COLLECT_DIR", default_root))


def _bdl_collect_max() -> int:
    """Plafond de cas collectés par racine (entier > 0, sinon valeur par défaut)."""
    raw = (os.getenv("SURVEY_BDL_COLLECT_MAX", "") or "").strip()
    try:
        value = int(raw)
        return value if value > 0 else _BDL_COLLECT_DEFAULT_MAX
    except ValueError:
        return _BDL_COLLECT_DEFAULT_MAX


def record_collected_action(
    driver,
    *,
    report: Any,
    actions: Any,
    action_trace: Any,
    dispatcher_success: Optional[bool],
) -> Optional[str]:
    """Persiste les faits runtime avant/après d'un plan d'actions jugé PASS.

    Contrat : retourne le dossier créé, ou None (désactivé par l'appelant,
    plafond atteint, faits indisponibles, erreur). Ne lève JAMAIS : un échec de
    collecte ne doit ni casser le survey ni modifier le résultat du plan.
    """
    try:
        # Sans faits "après" exploitables (aucune cible résolue, capture
        # impossible), le cas n'apporterait rien à l'oracle et consommerait le
        # plafond : on ne l'écrit pas.
        after = action_trace.get("after") if isinstance(action_trace, dict) else None
        if not isinstance(after, dict):
            log_debug("[BDL_COLLECT]", "plan PASS ignoré : faits runtime 'après' indisponibles")
            return None

        root = _bdl_collect_root()
        key = str(root)
        if key not in _collect_count_by_root:
            # Initialisation unique : reprend le compte des dossiers déjà présents
            # (redémarrage du bot) pour que le plafond reste global à la racine.
            _collect_count_by_root[key] = (
                sum(1 for e in root.iterdir() if e.is_dir()) if root.is_dir() else 0
            )

        cap = _bdl_collect_max()
        count = _collect_count_by_root[key]
        if count >= cap:
            if key not in _collect_cap_logged:
                _collect_cap_logged.add(key)
                log_info("[BDL_COLLECT]", f"plafond atteint ({cap} cas) — collecte suspendue dans {root}")
            return None

        folder = root / f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{count:05d}_action_pass"
        folder.mkdir(parents=True, exist_ok=True)

        url = None
        try:
            url = getattr(driver, "url", None)
            url = url if isinstance(url, str) else None
        except Exception:
            url = None

        meta = {
            "kind": BDL_COLLECT_KIND,
            "stage": "action",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "url": url,
            "dispatcher_success": dispatcher_success,
            "validator_ok": True,
        }
        (folder / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Nom volontairement distinct de validation_report.json : un dossier collecté
        # présenté par erreur au failure_case_builder serait ainsi vu comme case
        # incomplet (pas de validation_report.json) plutôt que comme un échec réel.
        (folder / "pass_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (folder / "action_trace.json").write_text(
            json.dumps(action_trace, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        _collect_count_by_root[key] = count + 1
        log_debug("[BDL_COLLECT]", f"plan PASS collecté ({count + 1}/{cap}) -> {folder}")
        return str(folder)
    except Exception as exc:
        # La collecte ne doit jamais devenir une cause d'échec survey.
        log_debug("[BDL_COLLECT]", f"collecte erreur: {type(exc).__name__}: {exc}")
        return None