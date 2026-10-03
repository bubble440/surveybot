"""Point d'extension unique des correctifs d'action, avant tout effet.

Le registre est vide par défaut. Un handler qui a pu agir ne peut jamais
déclencher ensuite le chemin historique de la même action.
"""

from __future__ import annotations

import math
import time
from contextlib import nullcontext
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from config import is_cta_intercept_only
from Survey.external_fix_loader import load_external_fixes_once
from Survey.external_fix_registry import EXTERNAL_FIXES, ExternalFixRegistry
from Survey.frame_utils import switch_to_frame_chain
from Survey.log_utils import log_debug

_TAG = "[ACTION_FIX]"
ANCHOR_FUNCTION_ID = "action_dispatcher.py::execute_action"
MAX_CANDIDATES = 8
MAX_SELECTORS_PER_KIND = 8
MAX_FRAME_DEPTH = 4
DEFAULT_BUDGET_S = 0.25


class ActionFixOutcome(Enum):
    """DECLINED garantit aucun effet ; les autres états interdisent le fallback."""

    DECLINED = "declined"
    HANDLED_SUCCESS = "handled_success"
    HANDLED_FAILURE = "handled_failure"


def _debug(message: str) -> None:
    try:
        log_debug(_TAG, message)
    except Exception:
        pass


def _selector_present(dom: Any, selector: str, cache: dict[str, bool], deadline: float) -> bool:
    if time.monotonic() >= deadline:
        _debug("budget dépassé avant handler")
        raise TimeoutError
    if selector not in cache:
        cache[selector] = dom.query_selector(selector) is not None
    if time.monotonic() >= deadline:
        _debug("budget dépassé avant handler")
        raise TimeoutError
    return cache[selector]


def run_action_fix_hook(
    driver: Any,
    parsed_action: Mapping[str, object],
    *,
    frame_chain: object = None,
    registry: ExternalFixRegistry | None = None,
    budget_s: float = DEFAULT_BUDGET_S,
) -> ActionFixOutcome:
    """Évalue un fix action avant la stratégie historique.

    ``handler(driver, dom, action)`` reçoit le contexte DOM courant et une
    copie en lecture seule des champs d'action. Il retourne ActionFixOutcome.
    DECLINED n'est permis que si le handler n'a produit aucun effet. Une
    exception ou un résultat invalide après son lancement arrête l'action.
    """
    active_registry = EXTERNAL_FIXES if registry is None else registry
    if active_registry is EXTERNAL_FIXES:
        try:
            load_external_fixes_once()
        except Exception as exc:
            _debug(f"chargement indisponible : {type(exc).__name__}")
    try:
        fixes = active_registry.candidates(
            stage="action", anchor_function_id=ANCHOR_FUNCTION_ID, position="before"
        )
    except Exception as exc:
        _debug(f"registre indisponible : {type(exc).__name__}")
        return ActionFixOutcome.DECLINED
    if not fixes:
        return ActionFixOutcome.DECLINED
    if len(fixes) > MAX_CANDIDATES:
        _debug("trop de candidats")
        return ActionFixOutcome.DECLINED

    try:
        if is_cta_intercept_only():
            return ActionFixOutcome.DECLINED
        chain = () if frame_chain is None else frame_chain
        if (
            not isinstance(chain, (list, tuple)) or len(chain) > MAX_FRAME_DEPTH
            or any(not isinstance(i, int) or isinstance(i, bool) or i < 0 for i in chain)
        ):
            _debug("chaîne de frames invalide")
            return ActionFixOutcome.DECLINED
        budget = float(budget_s)
        budget = min(DEFAULT_BUDGET_S, max(0.0, budget)) if math.isfinite(budget) else 0.0
        deadline = time.monotonic() + budget
        action = MappingProxyType({
            key: parsed_action.get(key)
            for key in ("qid", "target_id", "value", "itype", "context")
        })
    except Exception as exc:
        _debug(f"préparation indisponible : {type(exc).__name__}")
        return ActionFixOutcome.DECLINED

    handler_started = False
    try:
        context = switch_to_frame_chain(driver, list(chain)) if chain else nullcontext(True)
        with context as frame_ok:
            if not frame_ok:
                _debug("frame introuvable")
                return ActionFixOutcome.DECLINED
            dom = getattr(driver, "_current_frame", driver) if chain else driver
            cache: dict[str, bool] = {}
            matched = None
            for fix in fixes:
                if time.monotonic() >= deadline:
                    _debug("budget dépassé avant handler")
                    raise TimeoutError
                condition = fix.condition
                if (
                    len(condition.required_selectors) > MAX_SELECTORS_PER_KIND
                    or len(condition.excluded_selectors) > MAX_SELECTORS_PER_KIND
                ):
                    _debug("trop de sélecteurs")
                    return ActionFixOutcome.DECLINED
                if not all(_selector_present(dom, s, cache, deadline) for s in condition.required_selectors):
                    continue
                if any(_selector_present(dom, s, cache, deadline) for s in condition.excluded_selectors):
                    continue
                if matched is not None:
                    _debug("correctifs ambigus")
                    return ActionFixOutcome.DECLINED
                matched = fix
            if matched is None:
                return ActionFixOutcome.DECLINED
            if time.monotonic() >= deadline:
                _debug("budget dépassé avant handler")
                raise TimeoutError

            handler_started = True
            outcome = matched.handler(driver, dom, action)
            if not isinstance(outcome, ActionFixOutcome):
                _debug("résultat de handler invalide")
                return ActionFixOutcome.HANDLED_FAILURE
            if time.monotonic() >= deadline:
                _debug("budget dépassé après handler")
                if outcome is ActionFixOutcome.DECLINED:
                    return ActionFixOutcome.HANDLED_FAILURE
            return outcome
    except Exception as exc:
        _debug(f"hook indisponible : {type(exc).__name__}")
        return ActionFixOutcome.HANDLED_FAILURE if handler_started else ActionFixOutcome.DECLINED
