from __future__ import annotations

"""Validation passive des actions demandées au dispatcher.

Phase 1A reste volontairement conservative : on vérifie seulement ce que le
DOM_REGISTRY permet d'affirmer sans réimplémenter les sélecteurs spécialisés.
Les vérifications d'état DOM fin (checked/value/widget) seront élargies en 1B
après collecte de cas réels afin d'éviter les faux positifs.
"""

import re
import unicodedata
from typing import Any

from Survey.dom_registry import get_target
from Survey.input_utils import is_checked


def _norm(value: Any) -> str:
    # Même forme canonique que la couche d'action : les libellés extraits du
    # DOM et ceux renvoyés par le modèle peuvent différer uniquement par la
    # composition Unicode d'un accent (NFC/NFD).
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).strip()


def _norm_lc(value: Any) -> str:
    return _norm(value).lower()


def _ifop_zip2city_block_matches(question_blocks: Any, target_id: str) -> bool:
    if not isinstance(question_blocks, list):
        return False
    for block in question_blocks:
        if not isinstance(block, dict) or _norm(block.get("target_id")) != target_id:
            continue
        context = block.get("context")
        if isinstance(context, dict) and context.get("ifop_zip2city_widget") is True:
            return True
    return False


def _ifop_zip2city_dom_signal(driver, expected_zip: str) -> dict | None:
    """Lit uniquement l'état final du widget IFOP, sans interaction."""
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        widgets = current_frame.evaluate("""() => Array.from(
            document.querySelectorAll('.jz2c-input[data-prefix]')
        ).map(input => {
            const label = input.nextElementSibling;
            return {
                zip: String(input.value || '').trim(),
                city_label: label && label.matches('.jz2c-label')
                    ? String(label.textContent || '').trim()
                    : '',
            };
        })""")
    except Exception:
        return None

    if not isinstance(widgets, list):
        return None

    error_terms = re.compile(
        r"\b(?:erreur|error|echec|invalide?|invalid|introuvable|not\s+found|"
        r"aucune?\s+(?:ville|commune|resultat)|no\s+(?:city|result)|veuillez|"
        r"saisir|chargement|loading|searching|indisponible|unavailable|unknown)\b",
        re.IGNORECASE,
    )
    for widget in widgets:
        if not isinstance(widget, dict):
            continue
        observed_zip = _norm(widget.get("zip"))
        city_label = _norm(widget.get("city_label"))
        folded_label = unicodedata.normalize("NFKD", city_label).encode("ascii", "ignore").decode()
        if (
            observed_zip != expected_zip
            or not city_label
            or re.search(r"[^\W\d_]", city_label) is None
            or error_terms.search(folded_label)
        ):
            continue
        return {
            "zip": observed_zip,
            "city_label": city_label,
        }
    return None


def _ifop_zip2city_false_negative_issue(
    action: Any,
    *,
    driver,
    question_blocks: Any,
) -> dict | None:
    if not isinstance(action, dict) or driver is None:
        return None

    target_id = _norm(action.get("target_id"))
    itype = _norm_lc(action.get("itype"))
    value = _norm(action.get("value"))
    if (
        itype != "text"
        or re.fullmatch(r"\d{5}", value) is None
        or not target_id
        or not _ifop_zip2city_block_matches(question_blocks, target_id)
    ):
        return None

    observed = _ifop_zip2city_dom_signal(driver, value)
    if observed is None:
        return None

    return {
        "failure_type": "dispatcher_false_negative",
        "target_id": target_id,
        "itype": itype,
        "value": value,
        "dom_signal": "ifop_zip2city_resolved",
        "observed": observed,
    }


def _checkbox_radio_option_xpath(payload: Any, value: str) -> str | None:
    """Résout la même `option_xpath_map` (registry) que la vérification existante
    `action_value_not_in_registry_options` ci-dessous, sans hypothèse de provider.
    Même garde-fou : map plate uniquement (matrices/nested maps ignorées).
    """
    if not isinstance(payload, dict):
        return None
    option_map = payload.get("option_xpath_map")
    if not isinstance(option_map, dict) or not option_map:
        return None
    if not all(not isinstance(v, dict) for v in option_map.values()):
        return None

    target_value = _norm_lc(value)
    for label, xpath in option_map.items():
        if isinstance(xpath, str) and xpath and _norm_lc(label) == target_value:
            return xpath
    return None


def _checkbox_radio_dom_checked_signal(driver, xpath: str) -> bool | None:
    """Lit uniquement l'état `checked` réel de l'élément ciblé par `xpath`, sans
    interaction. Réutilise `Survey.input_utils.is_checked` (déjà générique :
    input natif -> aria-checked -> classes "checked"/"is-checked"). Si `xpath`
    résout un `<label>` plutôt que l'input lui-même, on descend au premier
    input checkbox/radio qu'il contient avant de lire son état.
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        el = current_frame.query_selector("xpath=" + xpath)
    except Exception:
        return None
    if el is None:
        return None

    target_el = el
    try:
        tag = (el.evaluate("e => e.tagName.toLowerCase()") or "").lower()
    except Exception:
        tag = ""
    if tag != "input":
        try:
            nested = el.query_selector("input[type='checkbox'], input[type='radio']")
        except Exception:
            nested = None
        if nested is None:
            return None
        target_el = nested

    try:
        return bool(is_checked(target_el))
    except Exception:
        return None


def _checkbox_radio_false_negative_issue(action: Any, *, driver) -> dict | None:
    """Faux négatif dispatcher générique pour itype checkbox/radio (cf.
    `_ifop_zip2city_false_negative_issue` ci-dessus, référence pour ce type de
    détection — non modifiée). Constat DOM passif scopé à l'élément ciblé par
    le `target_id` de l'action via le registry, sans hypothèse de provider :
    couvre aussi bien les widgets déjà supportés par une stratégie du
    dispatcher que les futurs widgets checkbox/radio qui n'en ont pas encore.
    """
    if not isinstance(action, dict) or driver is None:
        return None

    target_id = _norm(action.get("target_id"))
    itype = _norm_lc(action.get("itype"))
    value = _norm(action.get("value"))
    if itype not in {"checkbox", "radio"} or not target_id or not value:
        return None

    payload = get_target(target_id)
    xpath = _checkbox_radio_option_xpath(payload, value)
    if not xpath:
        return None

    if _checkbox_radio_dom_checked_signal(driver, xpath) is not True:
        return None

    return {
        "failure_type": "dispatcher_false_negative",
        "target_id": target_id,
        "itype": itype,
        "value": value,
        "dom_signal": "checkbox_radio_checked",
    }


_SELECTED_MARKER_CLASS_RE = re.compile(r"selected", re.IGNORECASE)
_SELECTED_MARKER_NEGATION_RE = re.compile(r"(?:un|de|not)-?selected", re.IGNORECASE)


def _checkbox_radio_marker_selected_signal(driver, xpath: str) -> bool | None:
    """Constat DOM passif pour widgets checkbox/radio SANS input natif (ex: options
    en cartes stylées MUI, uniquement une div marqueur de sélection). Fonction
    nouvelle et distincte, complémentaire à `_checkbox_radio_dom_checked_signal`
    ci-dessus (référence, non modifiée) : même résolution `xpath` via le registry,
    mais garde-fou strict inversé — ne se déclenche QUE si aucun
    `<input type=checkbox|radio>` n'existe dans le sous-arbre de l'élément résolu
    (sinon la détection existante à base d'input natif reste seule responsable).
    N'élargit pas `input_utils.is_checked()` (autres appelants dans
    `input_checkbox.py`) : lit directement les classes des descendants de
    l'élément ciblé et reconnaît le vocabulaire "selected" (hors négations
    un-/de-/not-selected) comme équivalent à un état sélectionné.
    """
    try:
        current_frame = getattr(driver, "_current_frame", driver)
        el = current_frame.query_selector("xpath=" + xpath)
    except Exception:
        return None
    if el is None:
        return None

    try:
        tag = (el.evaluate("e => e.tagName.toLowerCase()") or "").lower()
    except Exception:
        tag = ""
    if tag == "input":
        return None

    try:
        has_native_input = el.query_selector(
            "input[type='checkbox'], input[type='radio']"
        ) is not None
    except Exception:
        return None
    if has_native_input:
        return None

    try:
        class_list = el.evaluate(
            "e => [e, ...e.querySelectorAll('*')].map(n => "
            "(n.className && n.className.baseVal !== undefined) "
            "? n.className.baseVal : (n.className || ''))"
        )
    except Exception:
        return None
    if not isinstance(class_list, list):
        return None

    for cls in class_list:
        if not isinstance(cls, str) or not cls:
            continue
        if _SELECTED_MARKER_NEGATION_RE.search(cls):
            continue
        if _SELECTED_MARKER_CLASS_RE.search(cls):
            return True
    return False


def _checkbox_radio_marker_false_negative_issue(action: Any, *, driver) -> dict | None:
    """Faux négatif dispatcher pour checkbox/radio sans input natif (cf.
    `_checkbox_radio_false_negative_issue` ci-dessus, référence pour ce type de
    détection — non modifiée). Même résolution `option_xpath_map` que celle-ci,
    mais constat d'état via `_checkbox_radio_marker_selected_signal` (marqueur de
    sélection stylé) au lieu d'un input natif.
    """
    if not isinstance(action, dict) or driver is None:
        return None

    target_id = _norm(action.get("target_id"))
    itype = _norm_lc(action.get("itype"))
    value = _norm(action.get("value"))
    if itype not in {"checkbox", "radio"} or not target_id or not value:
        return None

    payload = get_target(target_id)
    xpath = _checkbox_radio_option_xpath(payload, value)
    if not xpath:
        return None

    if _checkbox_radio_marker_selected_signal(driver, xpath) is not True:
        return None

    return {
        "failure_type": "dispatcher_false_negative",
        "target_id": target_id,
        "itype": itype,
        "value": value,
        "dom_signal": "checkbox_radio_marker_selected",
    }


def _checkbox_radio_captured_state_false_negative_issue(
    action: Any, *, captured_facts: Any
) -> dict | None:
    """Faux négatif dispatcher checkbox/radio à partir de l'état déjà capturé
    (runtime_state.json, `facts` par option de la cible, cf. browser_capsule.py),
    pour un pilote statique (rejeu de failure_case) qui ne peut pas lire la
    propriété `checked` réelle : l'attribut HTML `checked` n'est jamais sérialisé
    par outerHTML. Complémentaire aux détecteurs live ci-dessus (non modifiés) :
    n'agit que si `captured_facts` est fourni (jamais le cas du chemin live).
    Seul un `checked` strictement True de l'option demandée compte.
    """
    if not isinstance(action, dict) or not isinstance(captured_facts, dict):
        return None

    target_id = _norm(action.get("target_id"))
    itype = _norm_lc(action.get("itype"))
    value = _norm(action.get("value"))
    if itype not in {"checkbox", "radio"} or not target_id or not value:
        return None

    prefix = f"target:{target_id}:option:"
    target_value = _norm_lc(value)
    for key, fact in captured_facts.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        if _norm_lc(key[len(prefix):]) != target_value:
            continue
        if isinstance(fact, dict) and fact.get("checked") is True:
            return {
                "failure_type": "dispatcher_false_negative",
                "target_id": target_id,
                "itype": itype,
                "value": value,
                "dom_signal": "checkbox_radio_checked_captured",
            }
        return None
    return None


def _dispatcher_false_negative_issue(
    action: Any,
    *,
    driver,
    question_blocks: Any,
    captured_option_states: Any = None,
) -> dict | None:
    """Point d'appel combiné : essaie d'abord la détection ifop_zip2city de
    référence (non modifiée), puis la détection générique checkbox/radio à input
    natif (non modifiée), puis en complément la détection pour widgets
    checkbox/radio sans input natif (marqueur de sélection stylé). Chaque
    détecteur reste indépendant et scopé à son propre garde-fou DOM ; aucune des
    fonctions existantes n'est modifiée par les autres.
    """
    return (
        _ifop_zip2city_false_negative_issue(
            action,
            driver=driver,
            question_blocks=question_blocks,
        )
        or _checkbox_radio_false_negative_issue(action, driver=driver)
        or _checkbox_radio_marker_false_negative_issue(action, driver=driver)
        or _checkbox_radio_captured_state_false_negative_issue(
            action, captured_facts=captured_option_states
        )
    )


def validate_actions(
    actions: list[dict] | None,
    *,
    dispatcher_success: bool | None = None,
    driver=None,
    question_blocks: Any = None,
    captured_option_states: Any = None,
) -> dict:
    """Retourne un rapport JSON-sérialisable sans modifier les actions.

    `captured_option_states` (optionnel, rejeu uniquement) : `facts` de
    runtime_state.json, utilisés en dernier recours quand le pilote ne peut pas
    lire l'état `checked` réel. Non fourni par le chemin live.
    """
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
        false_negatives = [
            issue
            for action in requested
            if (issue := _dispatcher_false_negative_issue(
                action,
                driver=driver,
                question_blocks=question_blocks,
                captured_option_states=captured_option_states,
            )) is not None
        ]
        # Le booléen du dispatcher porte sur le plan entier. On ne le requalifie
        # que si chaque action demandée possède la preuve DOM forte attendue.
        if len(false_negatives) == len(requested):
            issues.extend(false_negatives)
            try:
                from Survey.log_utils import log_debug
                log_debug(
                    "[OBSERVABILITY]",
                    f"dispatcher false negative confirmed targets="
                    f"{[issue['target_id'] for issue in false_negatives]!r}",
                )
            except Exception:
                pass
        else:
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
