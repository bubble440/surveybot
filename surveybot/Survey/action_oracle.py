from __future__ import annotations

"""Oracle d'action BDL-3 — OBSERVATION SEULE.

Compare, pour chaque action d'un plan, ce que le dispatcher a annoncé
(`dispatcher_action_success`) avec l'état DOM réellement observé « après »
(faits runtime de browser_capsule). Fonction pure : aucune I/O, aucun navigateur,
aucune décision. Le verdict est écrit dans action_trace.json (clé "oracle") ;
il ne modifie ni le validator, ni le rapport, ni les failure_cases, ni AutoFix.

Principe de prédictibilité : on ne tranche que sur une preuve forte et on
s'ABSTIENT (INCONCLUSIVE) dès que le signal est ambigu. Aucune cascade, aucun
fallback : un seul signal par famille d'itype.

  - radio / checkbox : l'option dont le libellé normalisé égale la valeur
    préparée (ou demandée) doit être `checked` dans l'état « après ».
  - text / textarea / number : le champ ne doit pas être vide, et sa valeur
    normalisée égale la valeur préparée (ou demandée) -> appliqué ; valeur
    différente non vide -> abstention (masque/formatage possible).
  - tout autre itype (dropdown, matrix, ...) : INCONCLUSIVE.

Pages « slide » (SPA qui avance seule après l'action) : l'élément ciblé disparaît du
DOM, donc tous les faits « après » de la cible sont absents alors qu'ils existaient
« avant ». Le verdict reste INCONCLUSIVE, avec le motif dédié `target_gone_after_action`
(distinct d'un état manquant), pour que ces cas soient comptés à part.

Limite connue : la valeur préparée est celle transmise à execute_action, pas
nécessairement la valeur finale après transformation interne d'une stratégie ;
d'où le test sur les DEUX valeurs (préparée, demandée) et l'abstention si aucune
ne correspond.
"""

import unicodedata
from typing import Any, Optional

ORACLE_VERSION = 1

CONFIRMED = "CONFIRMED"                  # annoncé réussi, observé appliqué
CONFIRMED_FAILURE = "CONFIRMED_FAILURE"  # annoncé échoué, observé non appliqué
FALSE_POSITIVE = "FALSE_POSITIVE"        # annoncé réussi, observé NON appliqué
FALSE_NEGATIVE = "FALSE_NEGATIVE"        # annoncé échoué, observé appliqué
INCONCLUSIVE = "INCONCLUSIVE"            # preuve insuffisante : on s'abstient

_CHOICE_ITYPES = {"radio", "checkbox"}
_TEXT_ITYPES = {"text", "textarea", "number"}


def _norm_lc(value: Any) -> str:
    # Même forme canonique que action_validator._norm (NFKC + espaces) en minuscules.
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).lower()


def _candidates(entry: dict) -> list:
    """Valeurs attendues normalisées : préparée d'abord, puis demandée (dédupliquées)."""
    out: list = []
    for raw in (entry.get("value"), entry.get("requested_value")):
        n = _norm_lc(raw)
        if n and n not in out:
            out.append(n)
    return out


def _has_fact(values) -> bool:
    return any(isinstance(v, dict) for v in values)


def _observe_choice(facts: dict, before_facts: Optional[dict], target_id: str, candidates: list) -> tuple:
    """(observed, reason) pour radio/checkbox : l'option cible est-elle cochée ?"""
    prefix = f"target:{target_id}:option:"
    # Cible disparue : options mesurées « avant », toutes introuvables « après ».
    after_opts = [f for k, f in facts.items() if k.startswith(prefix)]
    before_opts = [f for k, f in (before_facts or {}).items() if k.startswith(prefix)]
    if after_opts and not _has_fact(after_opts) and _has_fact(before_opts):
        return "unknown", "target_gone_after_action"
    matches = [
        fact for key, fact in facts.items()
        if key.startswith(prefix) and _norm_lc(key[len(prefix):]) in candidates
    ]
    if not matches:
        return "unknown", "no_matching_option"
    if len(matches) > 1:
        return "unknown", "ambiguous_option"
    fact = matches[0]
    if not isinstance(fact, dict):
        return "unknown", "option_fact_unavailable"
    checked = fact.get("checked")
    if not isinstance(checked, bool):
        return "unknown", "no_checked_state"
    return ("applied" if checked else "not_applied"), None


def _observe_text(facts: dict, before_facts: Optional[dict], target_id: str, candidates: list) -> tuple:
    """(observed, reason) pour un champ de saisie : valeur présente et attendue ?"""
    key = f"target:{target_id}"
    fact = facts.get(key)
    if not isinstance(fact, dict):
        before = (before_facts or {}).get(key)
        return "unknown", ("target_gone_after_action" if isinstance(before, dict) else "no_value_state")
    value = fact.get("value")
    if value is None:
        return "unknown", "no_value_state"
    norm = _norm_lc(value)
    if not norm:
        return "not_applied", None
    if norm in candidates:
        return "applied", None
    return "unknown", "value_differs_possible_formatting"


def _verdict(claimed: Optional[bool], observed: str) -> str:
    if claimed is None or observed == "unknown":
        return INCONCLUSIVE
    if claimed:
        return CONFIRMED if observed == "applied" else FALSE_POSITIVE
    return FALSE_NEGATIVE if observed == "applied" else CONFIRMED_FAILURE


def evaluate_action_trace(trace: Any) -> Optional[dict]:
    """Verdict par action à partir d'un action_trace (build_action_trace).

    Retourne None si la trace n'a pas les observations par action de BDL-3
    (ancien format) : on n'invente rien. Ne lève jamais sur une trace partielle.
    """
    actions = trace.get("requested_actions") if isinstance(trace, dict) else None
    if not isinstance(actions, list):
        return None
    entries = [a for a in actions if isinstance(a, dict)]
    if not any("dispatcher_action_success" in a for a in entries):
        return None

    after = trace.get("after")
    facts = after.get("facts") if isinstance(after, dict) else None
    before = trace.get("before")
    before_facts = before.get("facts") if isinstance(before, dict) else None
    results = []
    summary: dict = {}
    for entry in entries:
        itype = str(entry.get("itype") or "").strip().lower()
        target_id = str(entry.get("target_id") or "").strip()
        claimed = entry.get("dispatcher_action_success")
        claimed = claimed if isinstance(claimed, bool) else None

        if not isinstance(facts, dict):
            observed, reason = "unknown", "no_after_state"
        elif not target_id:
            observed, reason = "unknown", "no_target_id"
        elif itype in _CHOICE_ITYPES:
            observed, reason = _observe_choice(facts, before_facts, target_id, _candidates(entry))
        elif itype in _TEXT_ITYPES:
            observed, reason = _observe_text(facts, before_facts, target_id, _candidates(entry))
        else:
            observed, reason = "unknown", "unsupported_itype"

        verdict = _verdict(claimed, observed)
        item = {
            "index": entry.get("index"),
            "target_id": target_id or None,
            "itype": itype or None,
            "claimed_success": claimed,
            "observed": observed,
            "verdict": verdict,
        }
        if reason:
            item["reason"] = reason
        results.append(item)
        summary[verdict] = summary.get(verdict, 0) + 1

    return {"version": ORACLE_VERSION, "actions": results, "summary": summary}
