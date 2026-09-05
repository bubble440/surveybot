from __future__ import annotations

"""Validation passive des actions demandées au dispatcher.

Phase 1A reste volontairement conservative : on vérifie seulement ce que le
DOM_REGISTRY permet d'affirmer sans réimplémenter les sélecteurs spécialisés.
Les vérifications d'état DOM fin (checked/value/widget) seront élargies en 1B
après collecte de cas réels afin d'éviter les faux positifs.
"""

from typing import Any

from Survey.dom_registry import get_target


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _norm_lc(value: Any) -> str:
    return _norm(value).lower()


def validate_actions(actions: list[dict] | None, *, dispatcher_success: bool | None = None) -> dict:
    """Retourne un rapport JSON-sérialisable sans modifier les actions."""
    requested = actions or []
    issues: list[dict] = []

    for idx, action in enumerate(requested):
        if not isinstance(action, dict):
            issues.append({
                "failure_type": "invalid_action_shape",
                "action_index": idx,
            })
            continue

        target_id = _norm(action.get("target_id"))
        itype = _norm_lc(action.get("itype"))
        value = _norm(action.get("value"))
        qid = _norm(action.get("qid"))

        if not target_id:
            issues.append({
                "failure_type": "action_missing_target_id",
                "action_index": idx,
                "qid": qid,
                "itype": itype,
                "value": value,
            })
            continue

        payload = get_target(target_id)
        if payload is None:
            issues.append({
                "failure_type": "action_target_missing",
                "action_index": idx,
                "qid": qid,
                "target_id": target_id,
                "itype": itype,
                "value": value,
            })
            continue

        # Vérification sûre uniquement lorsque le registry expose explicitement
        # une option_xpath_map plate. Les matrices/nested maps et widgets custom
        # sont volontairement ignorés en 1A.
        option_map = payload.get("option_xpath_map") if isinstance(payload, dict) else None
        if itype in {"radio", "checkbox"} and value and isinstance(option_map, dict) and option_map:
            if all(not isinstance(v, dict) for v in option_map.values()):
                known = {_norm_lc(k) for k in option_map.keys() if _norm(k)}
                if known and _norm_lc(value) not in known:
                    issues.append({
                        "failure_type": "action_value_not_in_registry_options",
                        "action_index": idx,
                        "qid": qid,
                        "target_id": target_id,
                        "itype": itype,
                        "value": value,
                        "known_options_count": len(known),
                    })

    if requested and dispatcher_success is False:
        issues.append({
            "failure_type": "dispatcher_reported_failure",
            "actions_count": len(requested),
        })

    return {
        "stage": "action",
        "ok": not issues,
        "actions_count": len(requested),
        "dispatcher_success": dispatcher_success,
        "issues": issues,
    }
