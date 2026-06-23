# Survey/dom_extractors_areyounet.py
"""
DOM Extractors - AreYouNet

Ce module contient les extracteurs spécifiques à la plateforme AreYouNet:
- Matrix blocks (grilles de réponses)
- Switch radio blocks (boutons radio avec switch)
- Switch checkbox blocks (checkboxes avec switch)

AreYouNet utilise des patterns DOM particuliers avec des structures
.answer-group, .switch-item, et des inputs cachés associés à des wrappers cliquables.
"""

from __future__ import annotations
from typing import List, Dict, Any
import os, re

# Import des utilitaires
try:
    from Survey.dom_utils import _norm_lc, _xpath_literal, _best_xpath_for_element, _norm, _norm_key, _is_question_text
    from Survey.dom_registry import register_target, make_target_id
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _norm_lc, _xpath_literal, _best_xpath_for_element
    # dom_registry devra être disponible


def _pw_page(d):
    """Extrait la Page Playwright native depuis un PlaywrightDriverShim ou retourne d tel quel."""
    if hasattr(d, "_page"):
        return d._page
    return d


# ================================================================================
# AREYOUNET - MATRIX BLOCKS
# ================================================================================

def _extract_areyounet_matrix_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait les matrices AreYouNet (div.MatriceViewElement).
    Retourne 1 question_block par ligne de la matrice.
    """
    blocks: list[dict] = []
    frame_chain = frame_chain or []

    # Pattern spécifique plateforme
    try:
        matrices = _pw_page(driver).query_selector_all("div.MatriceViewElement")
    except Exception:
        return []

    if not matrices:
        return []

    rx_radio = re.compile(r"switch_radio\((?:'|\")(?P<qname>[^'\"]+)(?:'|\")\s*,\s*(?P<idx>\d+)")
    rx_checkbox = re.compile(r"switch_checkbox\((?:'|\")(?P<qname>[^'\"]+)(?:'|\")\s*,\s*(?P<idx>\d+)")

    # Pattern spécifique plateforme
    seen_qnames: set[str] = set()

    for matrix in matrices:
        try:
            # 1) Extraire le titre global de la question
            title = ""
            title_el = matrix.query_selector("span.elementTitle")
            if title_el is not None:
                title = _norm(title_el.inner_text())

            if not title:
                # Fallback: chercher dans p.titleQuestionElement
                title_el = matrix.query_selector("p.titleQuestionElement")
                if title_el is not None:
                    title = _norm(title_el.inner_text())

            if not title:
                continue

            # Pattern spécifique plateforme
            col_headers: list[str] = []
            try:
                header_cells = matrix.query_selector_all("td.tableHeader")
                for hc in header_cells:
                    txt = _norm(hc.inner_text())
                    if txt:
                        col_headers.append(txt)
            except Exception:
                pass

            if len(col_headers) < 2:
                continue

            # 3) Extraire les lignes (chaque ligne = 1 question)
            # Structure: <tr> contenant <td class="tableRow">Label</td> + plusieurs <td onclick="switch_radio(...)">
            try:
                rows = matrix.query_selector_all("tr")
            except Exception:
                continue

            for row in rows:
                try:
                    # Chercher le label de ligne (td.tableRow)
                    row_label = ""
                    row_label_el = row.query_selector("td.tableRow")
                    if row_label_el is None:
                        continue
                    row_label = _norm(row_label_el.inner_text())

                    if not row_label:
                        continue

                    # Chercher d'abord switch_radio, sinon switch_checkbox
                    clickables = row.query_selector_all("td[onclick*='switch_radio']")
                    cell_type = "radio"
                    if len(clickables) < 2:
                        clickables = row.query_selector_all("td[onclick*='switch_checkbox']")
                        cell_type = "checkbox"
                    if len(clickables) < 2:
                        continue

                    # Extraire le qname depuis le premier onclick
                    qname = ""
                    rx = rx_radio if cell_type == "radio" else rx_checkbox
                    for cl in clickables:
                        try:
                            oc = (cl.get_attribute("onclick") or "").strip()
                            m = rx.search(oc)
                            if m:
                                qname = (m.group("qname") or "").strip()
                                break
                        except Exception:
                            continue

                    if not qname:
                        continue

                    # Pattern spécifique plateforme
                    if qname in seen_qnames:
                        continue
                    seen_qnames.add(qname)

                    # Pattern spécifique plateforme
                    question = f"{title} [{row_label}]"

                    # Construire option_xpath_map: option_label -> xpath du td cliquable
                    option_xpath_map: dict[str, str] = {}
                    ayn_value_map: dict[str, str] = {}

                    for idx, header in enumerate(col_headers):
                        if idx >= len(clickables):
                            break

                        # XPath pour cibler le td avec onclick contenant qname et idx
                        func_name = "switch_radio" if cell_type == "radio" else "switch_checkbox"
                        # Pattern spécifique plateforme
                        xp = (
                            f"//td[contains(@onclick,\"{func_name}('{qname}',{idx}\")]"
                        )
                        option_xpath_map[_norm_key(header)] = xp

                        # Pattern spécifique plateforme
                        v_name = f"{qname}_{'rad' if cell_type == 'radio' else 'chk'}_{idx}_value"
                        v_el = clickables[idx].query_selector(f"input[name='{v_name}']")
                        if v_el is not None:
                            value = (v_el.get_attribute("value") or "").strip()
                            if value:
                                ayn_value_map[_norm_key(header)] = value

                    if len(option_xpath_map) < 2:
                        continue

                    # Enregistrer le bloc
                    group_key = f"areyounet:matrix:{qname}"
                    target_id = make_target_id("group", group_key, question)

                    itype = cell_type  # "radio" ou "checkbox"
                    max_select = 1 if cell_type == "radio" else len(col_headers) - 1  # checkbox: multi-select (sauf NSP)

                    register_target(
                        target_id,
                        {
                            "kind": "group",
                            "itype": "radio",
                            "group_key": group_key,
                            "question": question,
                            "option_xpath_map": option_xpath_map,
                            "frame_chain": frame_chain,
                            "ayn_field_name": qname,
                            "ayn_value_map": ayn_value_map,
                        },
                    )

                    blocks.append(
                        {
                            "question": question,
                            "itype": itype,
                            "options": col_headers.copy(),
                            "max_select": max_select,
                            "target_id": target_id,
                            "context": {"kind": "group", "group_key": group_key},
                        }
                    )

                except Exception:
                    continue

        except Exception:
            continue

    return blocks



# ================================================================================
# AREYOUNET - SWITCH RADIO BLOCKS
# ================================================================================

def _extract_areyounet_switch_radio_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    blocks: list[dict] = []
    frame_chain = frame_chain or []

    # Pattern spécifique plateforme
    try:
        containers = _pw_page(driver).query_selector_all("td[id^='QCB_'], div[id^='QCB_']")
    except Exception:
        return []

    rx = re.compile(r"switch_radio\((?:'|\")(?P<qname>[^'\"]+)(?:'|\")\s*,\s*(?P<idx>\d+)")

    for cont in containers:
        try:
            clickables = cont.query_selector_all("[onclick*='switch_radio(']")
        except Exception:
            continue

        if not clickables:
            continue

        cont_id = ""
        try:
            cont_id = (cont.get_attribute("id") or "").strip()
        except Exception:
            cont_id = ""

        # question text
        question = ""
        q_el = cont.query_selector("p.titleQuestionElement .elementTitle")
        if q_el is not None:
            question = _norm(q_el.inner_text())
        else:
            # Pattern spécifique plateforme
            try:
                raw = cont.inner_text() or ""
                for line in (raw.splitlines() if raw else []):
                    t = _norm(line)
                    if _is_question_text(t):
                        question = t
                        break
            except Exception:
                pass

        if not question:
            continue

        by_qname: dict[str, dict[int, dict[str, str]]] = {}

        for el in clickables:
            try:
                oc = (el.get_attribute("onclick") or "").strip()
            except Exception:
                oc = ""
            if not oc:
                continue

            m = rx.search(oc)
            if not m:
                continue

            qname = (m.group("qname") or "").strip()
            try:
                idx = int(m.group("idx"))
            except Exception:
                continue

            if not qname:
                continue

            # ------------------------------------------------------------
            # Pattern spécifique plateforme
            # On cherche le label dans le TD courant ou le TD sibling suivant,
            # PAS dans toute la row (sinon on prend le mauvais label).
            # ------------------------------------------------------------
            label = ""
            value = ""

            # Pattern spécifique plateforme
            try:
                sp = el.query_selector_all("span.elementText")
                if sp:
                    label = _norm(sp[0].inner_text())
            except Exception:
                pass

            # Pattern spécifique plateforme
            if not label:
                next_td = el.query_selector("xpath=following-sibling::td[1]")
                if next_td is not None:
                    try:
                        sp = next_td.query_selector_all("span.elementText")
                        if sp:
                            label = _norm(sp[0].inner_text())
                    except Exception:
                        pass

            # 3) Fallback: texte brut du TD courant (si pas de span.elementText)
            if not label:
                try:
                    raw = el.inner_text() or ""
                    label = _norm(raw)
                except Exception:
                    pass

            # Pattern spécifique plateforme
            v_name = f"{qname}_rad_{idx}_value"
            v_el = el.query_selector(f"input[name='{v_name}']")
            if v_el is not None:
                value = (v_el.get_attribute("value") or "").strip()
            else:
                # Pattern spécifique plateforme
                row = el.query_selector("xpath=ancestor::tr[1]")
                if row is not None:
                    v_el2 = row.query_selector(f"input[name='{v_name}']")
                    if v_el2 is not None:
                        value = (v_el2.get_attribute("value") or "").strip()

            if not label:
                continue

            by_qname.setdefault(qname, {})[idx] = {"label": label, "value": value}

        for qname, idx_map in by_qname.items():
            if len(idx_map) < 2:
                continue  # Pattern spécifique plateforme

            options = [idx_map[i]["label"] for i in sorted(idx_map.keys()) if idx_map[i].get("label")]
            if len(options) < 2:
                continue

            group_key = f"areyounet:switch_radio:{qname}"
            target_id = make_target_id("group", group_key, question)

            option_xpath_map: dict[str, str] = {}
            ayn_value_map: dict[str, str] = {}

            # XPath stable-ish: scope par container id + tokens onclick
            base = f"//*[@id={_xpath_literal(cont_id)}]" if cont_id else "//*"
            for i in sorted(idx_map.keys()):
                lbl = idx_map[i].get("label") or ""
                if not lbl:
                    continue

                xp = (
                    f"({base}//*[contains(@onclick,'switch_radio') and "
                    f"contains(@onclick,{_xpath_literal(qname)}) and "
                    f"contains(@onclick,{_xpath_literal(','+str(i))}) and "
                    f".//span[contains(@class,'elementText')]][1] | "
                    f"{base}//*[contains(@onclick,'switch_radio') and "
                    f"contains(@onclick,{_xpath_literal(qname)}) and "
                    f"contains(@onclick,{_xpath_literal(','+str(i))})][1])"
                )

                option_xpath_map[_norm_key(lbl)] = xp
                if idx_map[i].get("value"):
                    ayn_value_map[_norm_key(lbl)] = idx_map[i]["value"]

            if len(option_xpath_map) < 2:
                continue

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                    # Pattern spécifique plateforme
                    "ayn_field_name": qname,
                    "ayn_value_map": ayn_value_map,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )

    return blocks



# ================================================================================
# AREYOUNET - SWITCH CHECKBOX BLOCKS
# ================================================================================

def _extract_areyounet_switch_checkbox_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    AreYouNet CHECKBOX (areyounet.com / runet) : checkboxes simulées via onclick switch_checkbox().
    Pattern: <td onclick="switch_checkbox('QA04:215604',0,...)"><img class="img_checkbox">
    Les vrais inputs sont tous hidden ; la sélection se fait via JS sur les images.
    """
    blocks: list[dict] = []
    frame_chain = frame_chain or []

    # Pattern spécifique plateforme
    try:
        containers = _pw_page(driver).query_selector_all("td[id^='QCB_'], div[id^='QCB_']")
    except Exception:
        return []

    rx = re.compile(r"switch_checkbox\((?:'|\")(?P<qname>[^'\"]+)(?:'|\")\s*,\s*(?P<idx>\d+)")

    for cont in containers:
        try:
            clickables = cont.query_selector_all("[onclick*='switch_checkbox(']")
        except Exception:
            continue

        if not clickables:
            continue

        cont_id = ""
        try:
            cont_id = (cont.get_attribute("id") or "").strip()
        except Exception:
            cont_id = ""

        # question text
        question = ""
        q_el = cont.query_selector("p.titleQuestionElement .elementTitle")
        if q_el is not None:
            question = _norm(q_el.inner_text())
        else:
            # Pattern spécifique plateforme
            try:
                raw = cont.inner_text() or ""
                for line in (raw.splitlines() if raw else []):
                    t = _norm(line)
                    if _is_question_text(t):
                        question = t
                        break
            except Exception:
                pass

        if not question:
            continue

        by_qname: dict[str, dict[int, dict[str, str]]] = {}

        for el in clickables:
            try:
                oc = (el.get_attribute("onclick") or "").strip()
            except Exception:
                oc = ""
            if not oc:
                continue

            m = rx.search(oc)
            if not m:
                continue

            qname = (m.group("qname") or "").strip()
            try:
                idx = int(m.group("idx"))
            except Exception:
                continue

            if not qname:
                continue

            # Pattern spécifique plateforme
            # Chercher le label dans le TD courant ou le TD sibling suivant.
            label = ""
            value = ""

            # 1) span.elementText dans le TD courant
            try:
                sp = el.query_selector_all("span.elementText")
                if sp:
                    label = _norm(sp[0].inner_text())
            except Exception:
                pass

            # Pattern spécifique plateforme
            if not label:
                next_td = el.query_selector("xpath=following-sibling::td[1]")
                if next_td is not None:
                    try:
                        sp = next_td.query_selector_all("span.elementText")
                        if sp:
                            label = _norm(sp[0].inner_text())
                    except Exception:
                        pass

            # 3) Fallback: texte brut du TD courant
            if not label:
                try:
                    raw = el.inner_text() or ""
                    label = _norm(raw)
                except Exception:
                    pass

            # option value : pattern {qname}_chk_{idx}_value
            v_name = f"{qname}_chk_{idx}_value"
            v_el = el.query_selector(f"input[name='{v_name}']")
            if v_el is not None:
                value = (v_el.get_attribute("value") or "").strip()
            else:
                # Pattern spécifique plateforme
                row = el.query_selector("xpath=ancestor::tr[1]")
                if row is not None:
                    v_el2 = row.query_selector(f"input[name='{v_name}']")
                    if v_el2 is not None:
                        value = (v_el2.get_attribute("value") or "").strip()

            if not label:
                continue

            by_qname.setdefault(qname, {})[idx] = {"label": label, "value": value}

        for qname, idx_map in by_qname.items():
            if len(idx_map) < 2:
                continue

            options = [idx_map[i]["label"] for i in sorted(idx_map.keys()) if idx_map[i].get("label")]
            if len(options) < 2:
                continue

            group_key = f"areyounet:switch_checkbox:{qname}"
            target_id = make_target_id("group", group_key, question)

            option_xpath_map: dict[str, str] = {}
            ayn_value_map: dict[str, str] = {}

            base = f"//*[@id={_xpath_literal(cont_id)}]" if cont_id else "//*"
            for i in sorted(idx_map.keys()):
                lbl = idx_map[i].get("label") or ""
                if not lbl:
                    continue

                xp = (
                    f"({base}//*[contains(@onclick,'switch_checkbox') and "
                    f"contains(@onclick,{_xpath_literal(qname)}) and "
                    f"contains(@onclick,{_xpath_literal(','+str(i))}) and "
                    f".//span[contains(@class,'elementText')]][1] | "
                    f"{base}//*[contains(@onclick,'switch_checkbox') and "
                    f"contains(@onclick,{_xpath_literal(qname)}) and "
                    f"contains(@onclick,{_xpath_literal(','+str(i))})][1])"
                )

                option_xpath_map[_norm_key(lbl)] = xp
                if idx_map[i].get("value"):
                    ayn_value_map[_norm_key(lbl)] = idx_map[i]["value"]

            if len(option_xpath_map) < 2:
                continue

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "checkbox",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                    # Pattern spécifique plateforme
                    "ayn_field_name": qname,
                    "ayn_value_map": ayn_value_map,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "checkbox",
                    "options": options,
                    "max_select": len(options),  # checkbox = multi-select
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )

    return blocks
