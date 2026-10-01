"""Correctif externe — case 20260929_033834_extraction_validation_failure.

`_extract_consent_modal_radio_block` (dom_extractors_misc.py) retourne à tort
le radiogroup de consentement alors que `#modal-container` est signalé masqué
(`style` contenant "none") et qu'une question radio distincte, réellement
visible (`.question.radio_question`, sans classe `hidden_div`), n'est jamais
extraite. Ce correctif est un faux positif d'une stratégie existante : il se
branche en position `before`, strictement gardé par ces faits DOM, et
n'altère pas le corps de la stratégie protégée.
"""

from __future__ import annotations

from typing import Any

from Survey.dom_question_extractor import _compute_max_select
from Survey.dom_registry import make_target_id, register_target
from Survey.dom_utils import _norm, _norm_key, _xpath_literal
from Survey.external_fix_registry import DomCondition, ExternalFix
from Survey.log_utils import log_debug

_TAG = "[FIX_HIDDEN_CONSENT_RADIO_Q]"
_CORE_FUNCTION_ID = "dom_extractors_misc.py::_extract_consent_modal_radio_block"
_EXPECTED_CORE_HASH = "0fd0a206a1adb3d99da988681af022b9892bb7dddfef2e8cdb36970380dbf7a7"
_CASE_ID = "20260929_033834_extraction_validation_failure"


def _extract_visible_radio_question_blocks(driver: Any, frame_chain: Any) -> list[dict] | None:
    """Extrait le(s) widget(s) `.question.radio_question` réellement visibles."""
    frame_chain = list(frame_chain or [])
    try:
        widgets = driver.query_selector_all(".question.radio_question")
    except Exception:
        return None

    blocks: list[dict] = []
    for widget in widgets:
        try:
            try:
                cls = widget.get_attribute("class") or ""
            except Exception:
                cls = ""
            if "hidden_div" in cls:
                continue
            try:
                if widget.query_selector_all(".hidden_div"):
                    continue
            except Exception:
                continue

            try:
                q_node = widget.query_selector(".radio_q_text")
                question = _norm(q_node.inner_text() or "") if q_node is not None else ""
            except Exception:
                question = ""
            if not question:
                continue

            try:
                rows = widget.query_selector_all(".answer_options")
            except Exception:
                rows = []
            if len(rows) < 2:
                continue

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}
            group_name = ""
            for row in rows:
                try:
                    radio = row.query_selector("input.radioQT")
                except Exception:
                    radio = None
                if radio is None:
                    continue
                try:
                    rid = (radio.get_attribute("id") or "").strip()
                    rname = (radio.get_attribute("name") or "").strip()
                except Exception:
                    rid, rname = "", ""
                if not rid or not rname:
                    continue
                if not group_name:
                    group_name = rname
                elif rname != group_name:
                    continue

                try:
                    label_node = row.query_selector(".option_label")
                    label_txt = _norm(label_node.inner_text() or "") if label_node is not None else ""
                except Exception:
                    label_txt = ""
                if not label_txt:
                    continue

                key = _norm_key(label_txt)
                if key in option_xpath_map:
                    continue
                option_xpath_map[key] = f"//input[@id={_xpath_literal(rid)}]"
                options.append(label_txt)

            if len(options) < 2 or not group_name:
                continue

            group_key = f"radio:name:{group_name}"
            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": _compute_max_select("radio", options),
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )
        except Exception:
            continue

    if not blocks:
        log_debug(_TAG, "aucun widget radio_question visible exploitable")
        return None
    return blocks


FIXES: tuple[ExternalFix, ...] = (
    ExternalFix(
        fix_id="hidden_consent_modal_visible_radio_question",
        core_function_id=_CORE_FUNCTION_ID,
        expected_core_hash=_EXPECTED_CORE_HASH,
        anchor_function_id=_CORE_FUNCTION_ID,
        position="before",
        case_ids=(_CASE_ID,),
        condition=DomCondition(
            required_selectors=(
                "#modal-container[style*=none]",
                ".consent-form-radiogroup input[name=consent]",
                ".question.radio_question .answer_options input.radioQT",
            ),
            excluded_selectors=(
                ".question.radio_question.hidden_div",
            ),
        ),
        handler=_extract_visible_radio_question_blocks,
        stage="extraction",
    ),
)
