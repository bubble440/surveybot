"""Point d'extension unique des correctifs d'action, avant tout effet.

Le registre est vide par défaut. Un handler qui a pu agir ne peut jamais
déclencher ensuite le chemin historique de la même action.
"""

from __future__ import annotations

import math
import re
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
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
_ACTION_FIX_SUCCESSES = ContextVar("action_fix_successes", default=None)


class ActionFixOutcome(Enum):
    """DECLINED garantit aucun effet ; les autres états interdisent le fallback."""

    DECLINED = "declined"
    HANDLED_SUCCESS = "handled_success"
    HANDLED_FAILURE = "handled_failure"


@contextmanager
def observe_action_fix_successes(*, driver: Any = None, recorded_actions: Any = ()):
    """Isole les succès du plan ; en rejeu passif, reçoit leur provenance enregistrée."""
    # Le rejeu réel peut appeler un dispatcher déjà enveloppé par page_snapshot.
    # Les deux observateurs du même plan doivent voir les mêmes succès.
    if driver is None and _ACTION_FIX_SUCCESSES.get() is not None:
        yield
        return
    records = []
    if driver is not None:
        for action in recorded_actions:
            if isinstance(action, Mapping):
                records.append((driver, *(action.get(key) for key in (
                    "qid", "target_id", "itype", "value"
                ))))
    token = _ACTION_FIX_SUCCESSES.set(records)
    try:
        yield
    finally:
        _ACTION_FIX_SUCCESSES.reset(token)


def successful_action_fixes(driver: Any) -> tuple[tuple[Any, ...], ...]:
    """Retourne les (qid, target_id, itype, value) traités sur ce pilote."""
    records = _ACTION_FIX_SUCCESSES.get()
    return tuple(record[1:] for record in records or () if record[0] is driver)


def _log_selected_decision(fix: Any, outcome: ActionFixOutcome, failure_reason: str | None = None) -> None:
    """Journal best-effort : un échec d'écriture ne change jamais le verdict."""
    try:
        fix_id = fix.fix_id
        if not isinstance(fix_id, str) or re.fullmatch(r"[a-z][a-z0-9_]{2,63}", fix_id) is None:
            fix_id = "unknown"
        log_debug(_TAG, f"selected fix_id={fix_id}")
        suffix = f" reason={failure_reason}" if outcome is ActionFixOutcome.HANDLED_FAILURE and failure_reason else ""
        log_debug(_TAG, f"verdict={outcome.name}{suffix}")
    except BaseException:
        pass


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
    selected_fix = None
    returned_outcome = None
    failure_reason = None
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
            selected_fix = matched
            if time.monotonic() >= deadline:
                _debug("budget dépassé avant handler")
                raise TimeoutError

            handler_started = True
            outcome = matched.handler(driver, dom, action)
            if not isinstance(outcome, ActionFixOutcome):
                _debug("résultat de handler invalide")
                failure_reason = "invalid_result"
                returned_outcome = ActionFixOutcome.HANDLED_FAILURE
                return returned_outcome
            if time.monotonic() >= deadline:
                _debug("budget dépassé après handler")
                if outcome is ActionFixOutcome.HANDLED_FAILURE:
                    failure_reason = "post_handler_timeout"
                if outcome is ActionFixOutcome.DECLINED:
                    failure_reason = "post_handler_timeout"
                    returned_outcome = ActionFixOutcome.HANDLED_FAILURE
                    return returned_outcome
            if outcome is ActionFixOutcome.HANDLED_FAILURE and failure_reason is None:
                failure_reason = "handler_returned_failure"
            returned_outcome = outcome
            return returned_outcome
    except Exception as exc:
        _debug(f"hook indisponible : {type(exc).__name__}")
        if handler_started:
            failure_reason = "handler_exception"
        returned_outcome = ActionFixOutcome.HANDLED_FAILURE if handler_started else ActionFixOutcome.DECLINED
        return returned_outcome
    finally:
        if selected_fix is not None and returned_outcome is not None:
            if returned_outcome is ActionFixOutcome.HANDLED_SUCCESS:
                try:
                    records = _ACTION_FIX_SUCCESSES.get()
                    if records is not None:
                        records.append((driver, *(parsed_action.get(key) for key in (
                            "qid", "target_id", "itype", "value"
                        ))))
                except Exception:
                    pass
            _log_selected_decision(selected_fix, returned_outcome, failure_reason)
