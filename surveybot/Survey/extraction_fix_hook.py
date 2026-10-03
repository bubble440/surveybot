"""Premier point d'extension des correctifs d'extraction.

Le registre est vide en production à ce stade. Ce module n'importe aucun
correctif métier et ne modifie pas le chemin de validation des blocs.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from Survey.external_fix_loader import load_external_fixes_once
from Survey.external_fix_registry import EXTERNAL_FIXES, ExternalFixRegistry
from Survey.log_utils import log_debug

_TAG = "[EXTRACTION_FIX]"
MAX_CANDIDATES = 8
MAX_BLOCKS = 64
DEFAULT_BUDGET_S = 0.25


def _valid_blocks(value: object) -> bool:
    """Barrière de forme avant le post-traitement et validator habituels."""
    if not isinstance(value, list) or not 0 < len(value) <= MAX_BLOCKS:
        return False
    for block in value:
        if not isinstance(block, dict):
            return False
        if not isinstance(block.get("itype"), str) or not block["itype"]:
            return False
        if not isinstance(block.get("question"), str) or not block["question"]:
            return False
        if "options" in block and not isinstance(block["options"], list):
            return False
        if "context" in block and not isinstance(block["context"], dict):
            return False
        if "max_select" in block and (
            not isinstance(block["max_select"], int) or isinstance(block["max_select"], bool)
        ):
            return False
    return True


def _try_position(
    driver: Any,
    frame_chain: Any,
    *,
    anchor_function_id: str,
    position: str,
    registry: ExternalFixRegistry,
    deadline: float,
) -> tuple[list[dict] | None, bool]:
    """Retourne les blocs et indique si un hook after reste possible."""
    try:
        candidates = registry.candidates(
            stage="extraction", anchor_function_id=anchor_function_id, position=position
        )
    except Exception:
        log_debug(_TAG, "consultation du registre impossible")
        return None, False
    if not candidates:
        return None, True
    if len(candidates) > MAX_CANDIDATES:
        log_debug(_TAG, f"trop de candidats anchor={anchor_function_id} position={position}")
        return None, False

    matched = []
    selector_cache: dict[str, bool] = {}
    try:
        for fix in candidates:
            if time.monotonic() >= deadline:
                log_debug(_TAG, f"budget dépassé anchor={anchor_function_id} position={position}")
                return None, False
            condition = fix.condition
            applicable = True
            for selector in condition.required_selectors:
                if time.monotonic() >= deadline:
                    log_debug(_TAG, f"budget dépassé anchor={anchor_function_id} position={position}")
                    return None, False
                if selector not in selector_cache:
                    selector_cache[selector] = driver.query_selector(selector) is not None
                if time.monotonic() >= deadline:
                    log_debug(_TAG, f"budget dépassé anchor={anchor_function_id} position={position}")
                    return None, False
                if not selector_cache[selector]:
                    applicable = False
                    break
            if not applicable:
                continue
            for selector in condition.excluded_selectors:
                if time.monotonic() >= deadline:
                    log_debug(_TAG, f"budget dépassé anchor={anchor_function_id} position={position}")
                    return None, False
                if selector not in selector_cache:
                    selector_cache[selector] = driver.query_selector(selector) is not None
                if time.monotonic() >= deadline:
                    log_debug(_TAG, f"budget dépassé anchor={anchor_function_id} position={position}")
                    return None, False
                if selector_cache[selector]:
                    applicable = False
                    break
            if applicable:
                matched.append(fix)
                if len(matched) > 1:
                    log_debug(_TAG, f"correctifs ambigus anchor={anchor_function_id} position={position}")
                    return None, False
    except Exception as exc:
        log_debug(_TAG, f"garde DOM indisponible : {type(exc).__name__}")
        return None, False

    if not matched:
        return None, True
    if time.monotonic() >= deadline:
        log_debug(_TAG, f"budget dépassé anchor={anchor_function_id} position={position}")
        return None, False
    try:
        result = matched[0].handler(driver, frame_chain)
    except Exception as exc:
        log_debug(_TAG, f"handler indisponible : {type(exc).__name__}")
        return None, False
    # Le budget borne la sélection et les gardes avant le handler. Une fois
    # lancé, écarter un bloc valide selon la vitesse du navigateur rendrait le
    # résultat du même correctif imprévisible d'un rejeu à l'autre.
    if time.monotonic() >= deadline:
        log_debug(_TAG, f"budget dépassé après handler anchor={anchor_function_id} position={position}")
    if result is None:
        return None, False
    if not _valid_blocks(result):
        log_debug(_TAG, f"résultat rejeté anchor={anchor_function_id} position={position}")
        return None, False
    return result, False


def run_extraction_strategy_with_fixes(
    driver: Any,
    frame_chain: Any,
    *,
    anchor_function_id: str,
    strategy: Callable[[Any, Any], Any],
    registry: ExternalFixRegistry = EXTERNAL_FIXES,
    budget_s: float = DEFAULT_BUDGET_S,
) -> Any:
    """Évalue before, la stratégie, puis after si la stratégie est vide.

    Contrat handler extraction : ``handler(driver, frame_chain)`` retourne
    une liste non vide de blocs, ou None. Le driver est le Page/Frame courant.
    Une erreur du hook rend la main à la stratégie normale.
    """
    if registry is EXTERNAL_FIXES:
        try:
            load_external_fixes_once()
        except Exception as exc:
            try:
                log_debug(_TAG, f"chargement indisponible : {type(exc).__name__}")
            except Exception:
                pass
    try:
        budget = float(budget_s)
        budget = min(DEFAULT_BUDGET_S, max(0.0, budget)) if math.isfinite(budget) else 0.0
    except (TypeError, ValueError):
        budget = 0.0
    deadline = time.monotonic() + budget
    try:
        before, allow_after = _try_position(
            driver, frame_chain, anchor_function_id=anchor_function_id,
            position="before", registry=registry, deadline=deadline,
        )
    except Exception:
        before = None
        allow_after = False
    if before is not None:
        return before

    try:
        original = strategy(driver, frame_chain)
    except Exception:
        original = None  # Même issue qu'un extracteur en échec dans la cascade.
    if original:
        return original

    after = None
    if allow_after:
        try:
            after, _ = _try_position(
                driver, frame_chain, anchor_function_id=anchor_function_id,
                position="after", registry=registry, deadline=deadline,
            )
        except Exception:
            pass
    return after if after is not None else original
