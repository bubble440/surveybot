# Survey/dom_extractors_decipher.py
"""
DOM Extractors - Decipher/FocusVision

Ce module contient les extracteurs spécifiques aux plateformes:
- FocusVision (Decipher): radio/checkbox groups et cardsort
- Decipher answers list fallback

Ces extracteurs utilisent des patterns DOM spécifiques à ces plateformes
pour identifier et extraire les questions/options de manière fiable.
"""

from __future__ import annotations
from typing import List, Dict, Any, Set
import os
import re
from selenium.webdriver.common.by import By

# Import des utilitaires
try:
    from Survey.dom_utils import _norm_lc, _xpath_literal
    from Survey.dom_registry import register_target, make_target_id
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _norm_lc, _xpath_literal
    # dom_registry devra être disponible


# ================================================================================
# FOCUSVISION / DECIPHER - ANSWERS LIST GROUPS
# ================================================================================

_DECIPHER_TEMPLATE_MARKER_RE = re.compile(r"\{@[^}]*@\}")


def _clean_decipher_template_markers(text: str) -> str:
    """Supprime les marqueurs template Decipher du type {@...@} quand présents."""
    raw = (text or "").strip()
    if not raw or "{@" not in raw:
        return raw
    cleaned = _DECIPHER_TEMPLATE_MARKER_RE.sub("", raw)
    return re.sub(r"\s+", " ", cleaned).strip()

def _logical_answers_list_group_name(raw_name: str, all_raw_names: Set[str]) -> str:
    """Retourne le nom de groupe logique pour les names Decipher answers-list.

    Règle DOM-first:
    - si plusieurs names siblings `ans<d>.<d>.<d>` partagent la même base
      (`ans<d>.<d>`), on regroupe sur cette base;
    - sinon on conserve le name réel tel quel.

    Cette garde évite de fabriquer un alias artificiel (ex: `ans10538.0`)
    quand le DOM n'expose qu'un seul name réel (`ans10538.0.0`).
    """
    name = (raw_name or "").strip()
    if not re.fullmatch(r"ans\d+\.\d+\.\d+", name):
        return name

    base = ".".join(name.split(".")[:2])
    sibling_count = 0
    for raw in all_raw_names:
        raw_norm = (raw or "").strip()
        if not re.fullmatch(r"ans\d+\.\d+\.\d+", raw_norm):
            continue
        if raw_norm.startswith(f"{base}."):
            sibling_count += 1
            if sibling_count >= 2:
                return base
    return name

def _extract_focusvision_answers_list_groups(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait les groupes radio/checkbox FocusVision avec structure .answers.answers-list.
    
    Pattern DOM FocusVision:
    - Conteneurs: div.question[role='radiogroup'] / div.question.radio / div.question.checkbox
    - Liste d'options: .answers.answers-list OU .answers.answers-table
    - Inputs masqués avec wrappers cliquables (.clickableCell ou .element)
    - Labels: label[for=id] ou dans .clickableCell
    
    Stratégie:
    - Chercher les conteneurs .question avec .answers.answers-list
    - Grouper les inputs par attribut name
    - Extraire question text depuis .question-text
    - Construire XPath vers wrapper cliquable (pas l'input masqué)
    
    Args:
        driver: WebDriver Selenium
        frame_chain: Chaîne de frames ou None
    
    Returns:
        Liste de dicts avec métadonnées pour dom_registry
    """
    blocks: list[dict] = []

    def _visible_text(el) -> str:
        txt = (el.text or "").strip()
        if txt:
            return txt
        for attr in ("innerText", "textContent"):
            try:
                raw = (el.get_attribute(attr) or "").strip()
            except Exception:
                raw = ""
            if raw:
                return raw
        return ""

    def _extract_label_text(label_el) -> str:
        """Lit le texte d'un label même quand son conteneur est masqué (display:none)."""
        txt = (label_el.text or "").strip()
        if txt:
            return _clean_decipher_template_markers(txt)
        for attr in ("innerText", "textContent"):
            try:
                raw = (label_el.get_attribute(attr) or "").strip()
            except Exception:
                raw = ""
            if raw:
                return _clean_decipher_template_markers(raw)
        return ""

    # Question containers FocusVision
    q_containers = driver.find_elements(By.CSS_SELECTOR, "div.question[role='radiogroup'], div.question.radio, div.question.checkbox")
    for q in q_containers:
        try:
            answers = q.find_element(By.CSS_SELECTOR, ".answers.answers-list, .answers.answers-table")
        except Exception:
            continue

        # Inputs masqués (hidden). Variante avec clickableCell
        # Inputs masqués (hidden), variante avec clickableCell
        # => on élargit un peu, mais toujours sous .answers.answers-list (scope strict).
        inputs = answers.find_elements(
            By.CSS_SELECTOR,
            "input[type='radio'], input[type='checkbox']"
        )
        if len(inputs) < 2:
            continue

        # Question texte
        question = ""
        try:
            question = (q.find_element(By.CSS_SELECTOR, ".question-text").text or "").strip()
        except Exception:
            question = (q.text or "").strip().split("\n")[0].strip()

        group_by_row_table = None
        try:
            candidate_tables = answers.find_elements(
                By.CSS_SELECTOR,
                "table.grid[data-settings*='group-by-row'][data-settings*='table-mode']",
            )
            group_by_row_table = candidate_tables[0] if candidate_tables else None
        except Exception:
            group_by_row_table = None

        if group_by_row_table is not None:
            mx_stage_id = ""
            mx_scale_code_by_label_norm: dict[str, str] = {}
            question_id = (q.get_attribute("id") or "").strip()
            if question_id.startswith("question_"):
                expected_mx_stage_id = f"mx-stage-{question_id[len('question_'):]}"
                if driver.find_elements(By.ID, expected_mx_stage_id):
                    mx_stage_id = expected_mx_stage_id

            try:
                col_header_nodes = group_by_row_table.find_elements(By.CSS_SELECTOR, "th[scope='col']")
            except Exception:
                col_header_nodes = []
            col_labels: list[str] = []
            col_labels_by_header_id: dict[str, str] = {}
            col_codes_by_header_id: dict[str, str] = {}
            for h in col_header_nodes:
                txt = _visible_text(h)
                hid = (h.get_attribute("id") or "").strip()
                if txt and txt not in col_labels:
                    col_labels.append(txt)
                if txt and hid:
                    col_labels_by_header_id[hid] = txt
                    m_col = re.search(r"_c(\d+)$", hid)
                    if m_col:
                        col_codes_by_header_id[hid] = f"c{m_col.group(1)}"

                if mx_stage_id:
                    m = re.search(r"_c(\d+)$", hid)
                    if m and txt:
                        mx_scale_code_by_label_norm[_norm_lc(txt)] = f"c{m.group(1)}"

            if len(col_labels) >= 2:
                try:
                    row_nodes = group_by_row_table.find_elements(By.CSS_SELECTOR, "tr.row-elements")
                except Exception:
                    row_nodes = []

                for row in row_nodes:
                    row_label = ""
                    row_header_id = ""
                    try:
                        row_header = row.find_element(By.CSS_SELECTOR, "th[scope='row']")
                        row_label = _visible_text(row_header)
                        row_header_id = (row_header.get_attribute("id") or "").strip()
                    except Exception:
                        row_label = ""
                        row_header_id = ""
                    if not row_label:
                        continue

                    try:
                        row_inputs = row.find_elements(By.CSS_SELECTOR, "input[type='radio'], input[type='checkbox']")
                    except Exception:
                        row_inputs = []
                    if len(row_inputs) < 2:
                        continue

                    itype = "radio"
                    try:
                        if (row_inputs[0].get_attribute("type") or "").strip().lower() == "checkbox":
                            itype = "checkbox"
                    except Exception:
                        pass

                    options: list[str] = []
                    option_xpath_map: dict[str, str] = {}
                    mx_scale_xpath_map: dict[str, str] = {}
                    mx_input_id_map: dict[str, str] = {}
                    mx_row_code = ""
                    m_row = re.search(r"_r(\d+)_left$", row_header_id)
                    if m_row:
                        mx_row_code = f"r{m_row.group(1)}"
                    for row_col_idx, inp in enumerate(row_inputs):
                        inp_id = (inp.get_attribute("id") or "").strip()
                        if not inp_id:
                            continue

                        col_label = ""
                        try:
                            cell = inp.find_element(By.XPATH, "ancestor::td[1]")
                            headers_attr = (cell.get_attribute("headers") or "").strip()
                        except Exception:
                            headers_attr = ""
                        if headers_attr:
                            for header_id in headers_attr.split():
                                candidate = col_labels_by_header_id.get(header_id)
                                if candidate:
                                    col_label = candidate
                                    break

                        if not col_label:
                            raw_value = (inp.get_attribute("value") or "").strip()
                            if raw_value.isdigit():
                                idx = int(raw_value)
                                if 0 <= idx < len(col_labels):
                                    col_label = col_labels[idx]

                        if not col_label and 0 <= row_col_idx < len(col_labels):
                            col_label = col_labels[row_col_idx]

                        if not col_label:
                            continue

                        label_norm = _norm_lc(col_label)
                        if not label_norm or label_norm in option_xpath_map:
                            continue

                        col_code = ""
                        if headers_attr:
                            for header_id in headers_attr.split():
                                col_code = col_codes_by_header_id.get(header_id, "")
                                if col_code:
                                    break
                        if not col_code:
                            m_id = re.search(r"\.([0-9]+)\.[0-9]+$", inp_id)
                            if m_id:
                                col_code = f"c{int(m_id.group(1)) + 1}"

                        xp = (
                            f"//input[@id={_xpath_literal(inp_id)}]"
                            "//ancestor::*[contains(concat(' ',normalize-space(@class),' '),' clickableCell ')"
                            " or contains(concat(' ',normalize-space(@class),' '),' element ')][1]"
                        )
                        options.append(col_label)
                        option_xpath_map[label_norm] = xp
                        mx_input_id_map[label_norm] = inp_id

                        if mx_row_code and col_code:
                            question_id = (q.get_attribute("id") or "").strip()
                            if question_id:
                                mx_scale_xpath_map[label_norm] = (
                                    f"//div[@id={_xpath_literal(question_id)}]"
                                    f"//div[contains(concat(' ',normalize-space(@class),' '),' mx-carouselapp-scale ') and @data-code={_xpath_literal(col_code)}][1]"
                                )
                            else:
                                mx_scale_xpath_map[label_norm] = (
                                    f"(//div[contains(concat(' ',normalize-space(@class),' '),' mx-carouselapp-scale ') and @data-code={_xpath_literal(col_code)}])[1]"
                                )

                    if len(options) < 2:
                        continue

                    row_question = row_label
                    if question:
                        row_question = f"{question} [{row_label}]"
                    row_input_name = row_header_id or f"row:{_norm_lc(row_label)}"
                    group_key = f"{itype}:name:{row_input_name}"
                    target_id = make_target_id("group", group_key, row_question)

                    payload: dict[str, Any] = {
                        "kind": "group",
                        "frame_chain": list(frame_chain or []),
                        "itype": itype,
                        "group_key": group_key,
                        "question": row_question,
                        "input_name": row_input_name,
                        "max_select": 1 if itype == "radio" else len(options),
                        "options": options,
                        "option_xpath_map": option_xpath_map,
                    }

                    # Decipher MX Carousel: quand la table answers-list est masquée/non-interactable,
                    # on cible les cartes visibles du carousel et l'échelle (c1/c2/...) via data-code.
                    # Scope DOM strict: uniquement si .mx-stage est présent dans la même question.
                    if mx_stage_id:
                        row_code = ""
                        m_row = re.search(r"_r(\d+)_left$", row_header_id)
                        if m_row:
                            row_code = f"r{m_row.group(1)}"

                        mx_option_xpath_map: dict[str, str] = {}
                        for opt_label in options:
                            nk = _norm_lc(opt_label)
                            if not nk:
                                continue
                            scale_code = mx_scale_code_by_label_norm.get(nk)
                            if not scale_code:
                                continue
                            mx_option_xpath_map[nk] = (
                                f"//div[@id={_xpath_literal(mx_stage_id)}]"
                                f"//div[contains(concat(' ',normalize-space(@class),' '),' mx-carouselapp-scale ') and @data-code={_xpath_literal(scale_code)}]"
                                "//div[contains(concat(' ',normalize-space(@class),' '),' mx-card ')][1]"
                            )

                        if len(mx_option_xpath_map) >= 2:
                            payload["option_xpath_map"] = mx_option_xpath_map
                            if row_code:
                                payload["pre_click_xpaths"] = [
                                    (
                                        f"//div[@id={_xpath_literal(mx_stage_id)}]"
                                        f"//div[contains(concat(' ',normalize-space(@class),' '),' mx-carouselapp-item ') and @data-code={_xpath_literal(row_code)}]"
                                        "//div[contains(concat(' ',normalize-space(@class),' '),' mx-card ')][1]"
                                    )
                                ]

                    register_target(
                        target_id,
                        payload,
                    )
                    blocks.append(
                        {
                            "target_id": target_id,
                            "kind": "group",
                            "itype": itype,
                            "question": row_question,
                            "options": options,
                            "max_select": 1 if itype == "radio" else len(options),
                            "context": {
                                "kind": "group",
                                "group_key": group_key,
                                "focusvision_answers_list": True,
                            },
                        }
                    )

                if blocks:
                    continue

        # Regrouper par name logique
        atm1d_buttons = []
        try:
            atm1d_buttons = q.find_elements(
                By.CSS_SELECTOR,
                ".sq-atm1d-widget .sq-atm1d-buttons .sq-atm1d-button[data-label]",
            )
        except Exception:
            atm1d_buttons = []

        if len(atm1d_buttons) >= 2:
            options: list[str] = []
            option_xpath_map: dict[str, str] = {}
            exclusive_options_norm: list[str] = []
            question_id = (q.get_attribute("id") or "").strip()

            for btn in atm1d_buttons:
                data_label = (btn.get_attribute("data-label") or "").strip()
                if not data_label:
                    continue

                legend = ""
                try:
                    legend = (btn.find_element(By.CSS_SELECTOR, ".sq-atm1d-legend").text or "").strip()
                except Exception:
                    legend = ""
                if not legend:
                    continue

                legend_norm = _norm_lc(legend)
                if not legend_norm or legend_norm in option_xpath_map:
                    continue

                if question_id:
                    xp = (
                        f"//div[@id={_xpath_literal(question_id)}]"
                        "//li[contains(concat(' ',normalize-space(@class),' '),' sq-atm1d-button ') and @data-label="
                        f"{_xpath_literal(data_label)}][1]"
                    )
                else:
                    xp = (
                        "(//li[contains(concat(' ',normalize-space(@class),' '),' sq-atm1d-button ') and @data-label="
                        f"{_xpath_literal(data_label)}])[1]"
                    )

                options.append(legend)
                option_xpath_map[legend_norm] = xp

                if _norm_lc(data_label) == "none":
                    exclusive_options_norm.append(legend_norm)

            if len(options) >= 2:
                group_key = "checkbox:atm1d"
                target_id = make_target_id("group", group_key, question or "atm1d")
                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "frame_chain": list(frame_chain or []),
                        "itype": "checkbox",
                        "group_key": group_key,
                        "question": question,
                        "input_name": "atm1d",
                        "max_select": len(options),
                        "options": options,
                        "option_xpath_map": option_xpath_map,
                        "meta": {
                            "source": "sq-atm1d",
                            "exclusive_options_norm": exclusive_options_norm,
                        },
                    },
                )

                blocks.append(
                    {
                        "target_id": target_id,
                        "kind": "group",
                        "itype": "checkbox",
                        "question": question,
                        "options": options,
                        "max_select": len(options),
                        "context": {
                            "kind": "group",
                            "group_key": group_key,
                            "focusvision_answers_list": True,
                        },
                    }
                )
                continue

        matrix_mode = False
        matrix_group_name = ""
        matrix_row_labels: dict[str, str] = {}
        matrix_col_labels: dict[str, str] = {}
        matrix_table = None
        try:
            candidate_tables = answers.find_elements(By.CSS_SELECTOR, "table.grid")
            matrix_table = candidate_tables[0] if candidate_tables else None
        except Exception:
            matrix_table = None

        raw_triplets: list[tuple[str, str, str]] = []
        if matrix_table is not None:
            for inp in inputs:
                raw_name = (inp.get_attribute("name") or "").strip()
                m = re.fullmatch(r"(ans\d+)\.(\d+)\.(\d+)", raw_name)
                if not m:
                    raw_triplets = []
                    break
                raw_triplets.append((m.group(1), m.group(2), m.group(3)))

        if raw_triplets:
            stems = {t[0] for t in raw_triplets}
            col_idx = {t[1] for t in raw_triplets}
            row_idx = {t[2] for t in raw_triplets}
            if len(stems) == 1 and len(col_idx) >= 2 and len(row_idx) >= 2:
                matrix_mode = True
                matrix_group_name = next(iter(stems))

                # Headers de colonnes observables dans les grilles FocusVision/Decipher.
                try:
                    col_headers = matrix_table.find_elements(By.CSS_SELECTOR, "th[id*='_c']")
                except Exception:
                    col_headers = []
                for h in col_headers:
                    hid = (h.get_attribute("id") or "").strip()
                    m = re.search(r"_c(\d+)$", hid)
                    if not m:
                        continue
                    txt = (h.text or h.get_attribute("innerText") or h.get_attribute("textContent") or "").strip()
                    if txt:
                        matrix_col_labels[m.group(1)] = txt

                try:
                    row_headers = matrix_table.find_elements(By.CSS_SELECTOR, "th[id$='_left']")
                except Exception:
                    row_headers = []
                for h in row_headers:
                    hid = (h.get_attribute("id") or "").strip()
                    m = re.search(r"_r(\d+)_left$", hid)
                    if not m:
                        continue
                    txt = (h.text or h.get_attribute("innerText") or h.get_attribute("textContent") or "").strip()
                    if txt:
                        matrix_row_labels[m.group(1)] = txt

        by_name: dict[str, list] = {}
        all_raw_names = {
            (inp.get_attribute("name") or "").strip()
            for inp in inputs
            if (inp.get_attribute("name") or "").strip()
        }
        for inp in inputs:
            name = (inp.get_attribute("name") or "").strip()
            if not name:
                continue
            if matrix_mode:
                name = matrix_group_name
            else:
                name = _logical_answers_list_group_name(name, all_raw_names)
            by_name.setdefault(name, []).append(inp)

        for name, inps in by_name.items():
            # itype
            itype = "radio"
            try:
                if (inps[0].get_attribute("type") or "").strip().lower() == "checkbox":
                    itype = "checkbox"
            except Exception as e:
                if os.getenv("RUN_ENV", "local") == "local":
                    print(f"[DOM_ANALYZER][WARN] focusvision extract: {type(e).__name__}: {e}")
                continue

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}
            matrix_cell_xpath_map: dict[str, dict[str, str]] = {}
            aux_openended_input_names: set[str] = set()
            seen_options: set[str] = set()

            # Confirmit GridClick: la table .answers peut être masquée (display:none)
            # et les seuls éléments réellement interactifs sont les boutons .scale-button.
            # On active ce mode uniquement si le widget est détecté dans ce bloc.
            has_gridclick_widget = False
            try:
                has_gridclick_widget = bool(
                    q.find_elements(
                        By.CSS_SELECTOR,
                        ".gridclick .scale-container .scale-button[data-index]",
                    )
                )
            except Exception:
                has_gridclick_widget = False

            # Decipher MX Collapsible (liste plate non-matricielle):
            # certains questionnaires exposent une answers-list standard + un rendu cartes
            # interactif dans #mx-stage-{QID}. On ne bascule sur ces cartes que si le
            # pattern DOM complet est présent pour ce bloc.
            has_mx_collapsible = False
            mx_option_xpath_map: dict[str, str] = {}
            question_id = (q.get_attribute("id") or "").strip()
            m_qid = re.fullmatch(r"question_(.+)", question_id)
            if m_qid:
                qid_suffix = m_qid.group(1)
                try:
                    mx_stage = driver.find_element(By.CSS_SELECTOR, f"#mx-stage-{qid_suffix}")
                    mx_rows = mx_stage.find_elements(
                        By.CSS_SELECTOR,
                        ".mx-collapsible-groupholder .mx-collapsible-row-item[precode]",
                    )
                    if mx_rows:
                        for row in mx_rows:
                            precode = (row.get_attribute("precode") or "").strip()
                            if not precode:
                                continue
                            try:
                                row_label = _extract_label_text(
                                    row.find_element(By.CSS_SELECTOR, ".bottom .label")
                                )
                            except Exception:
                                row_label = ""
                            row_norm = _norm_lc(row_label)
                            if not row_norm:
                                continue
                            mx_option_xpath_map[row_norm] = (
                                f"//div[@id={_xpath_literal(f'mx-stage-{qid_suffix}')}][1]"
                                f"//div[contains(concat(' ',normalize-space(@class),' '),' mx-collapsible-groupholder ')]"
                                f"//div[contains(concat(' ',normalize-space(@class),' '),' mx-collapsible-row-item ')"
                                f" and @precode={_xpath_literal(precode)}][1]"
                            )

                        mx_exclusive = mx_stage.find_elements(
                            By.CSS_SELECTOR,
                            ".mx-collapsible-exclusive-holder .mx-collapsible-exclusive[class*='mx-button-r']",
                        )
                        for ex in mx_exclusive:
                            classes = (ex.get_attribute("class") or "").strip().split()
                            precode = ""
                            for cls in classes:
                                if cls.startswith("mx-button-r"):
                                    precode = cls.replace("mx-button-", "", 1)
                                    break
                            if not precode:
                                continue
                            try:
                                ex_label = _extract_label_text(
                                    ex.find_element(By.CSS_SELECTOR, ".mx-btn-label")
                                )
                            except Exception:
                                ex_label = ""
                            ex_norm = _norm_lc(ex_label)
                            if not ex_norm:
                                continue
                            mx_option_xpath_map[ex_norm] = (
                                f"//div[@id={_xpath_literal(f'mx-stage-{qid_suffix}')}][1]"
                                f"//div[contains(concat(' ',normalize-space(@class),' '),' mx-collapsible-exclusive-holder ')]"
                                f"//div[contains(concat(' ',normalize-space(@class),' '),' mx-collapsible-exclusive ')"
                                f" and contains(concat(' ',normalize-space(@class),' '),{_xpath_literal(f' {precode} ')})][1]"
                            )

                        has_mx_collapsible = bool(mx_option_xpath_map)
                except Exception:
                    has_mx_collapsible = False

            # Dans GridClick, l'item/segment courant (tuile active) apporte le contexte
            # de ligne sélectionnée (ex: "Épargne ...") qui n'est pas dans <h1>.
            # On préfixe la question uniquement quand ce libellé est observable dans le DOM,
            # pour éviter toute heuristique globale par provider.
            if has_gridclick_widget:
                try:
                    segment_txt = (
                        q.find_element(By.CSS_SELECTOR, ".gridclick .item.current .text-content").text
                        or ""
                    ).strip()
                except Exception:
                    segment_txt = ""
                if segment_txt:
                    q_norm = _norm_lc(question)
                    s_norm = _norm_lc(segment_txt)
                    if s_norm and s_norm not in q_norm:
                        question = f"{segment_txt} — {question}" if question else segment_txt

            for inp in inps:
                inp_id = (inp.get_attribute("id") or "").strip()
                if not inp_id:
                    continue

                # Label visible
                label_txt = ""
                try:
                    lab = answers.find_element(By.CSS_SELECTOR, f"label[for='{inp_id}']")
                    label_txt = _extract_label_text(lab)
                    try:
                        for oe in lab.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea"):
                            oe_name = (oe.get_attribute("name") or "").strip()
                            if oe_name:
                                aux_openended_input_names.add(oe_name)
                    except Exception:
                        pass
                except Exception:
                    try:
                        lab = inp.find_element(By.XPATH, "ancestor::*[contains(@class,'clickableCell')][1]//label")
                        label_txt = _extract_label_text(lab)
                        try:
                            for oe in lab.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea"):
                                oe_name = (oe.get_attribute("name") or "").strip()
                                if oe_name:
                                    aux_openended_input_names.add(oe_name)
                        except Exception:
                            pass
                    except Exception as e:
                        if os.getenv("RUN_ENV", "local") == "local":
                            print(f"[DOM_ANALYZER][WARN] focusvision extract: {type(e).__name__}: {e}")
                        continue

                if not label_txt:
                    continue

                # Option "Autre ... préciser" avec champ open-ended dans le même label:
                # on exclut cette option du bloc group principal (non gérée en action group ici).
                if lab is not None:
                    try:
                        if lab.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea"):
                            continue
                    except Exception:
                        pass

                cell_col_label = label_txt
                cell_row_label = ""
                raw_name = (inp.get_attribute("name") or "").strip()
                if matrix_mode:
                    m = re.fullmatch(r"ans\d+\.(\d+)\.(\d+)", raw_name)
                    if m:
                        row_key = m.group(2)
                        cell_row_label = matrix_row_labels.get(str(int(row_key) + 1)) or ""

                cell_col_norm = _norm_lc(cell_col_label)
                if cell_col_norm and cell_col_norm not in seen_options:
                    options.append(cell_col_label)
                    seen_options.add(cell_col_norm)

                # IMPORTANT: on clique un wrapper cliquable (pas l'input masqué).
                # Fallback: si clickableCell absent, on remonte sur .element.
                xp = (
                    f"//input[@id={_xpath_literal(inp_id)}]"
                    f"/ancestor::*["
                    f"contains(concat(' ',normalize-space(@class),' '),' clickableCell ')"
                    f" or contains(concat(' ',normalize-space(@class),' '),' element ')"
                    f"][1]"
                )

                # Cas spécifique GridClick (DOM-only, déclenché par pattern DOM explicite):
                # on mappe l'option vers le bouton d'échelle visible au lieu du <td> caché.
                if has_gridclick_widget:
                    try:
                        raw_col_idx = ""
                        m_col = re.fullmatch(r"ans\d+\.(\d+)\.\d+", raw_name)
                        if m_col:
                            raw_col_idx = m_col.group(1)
                        if raw_col_idx != "":
                            xp = (
                                "(//div[contains(@class,'gridclick')]"
                                "//div[contains(@class,'scale-button') and @data-index="
                                f"{_xpath_literal(raw_col_idx)}])[1]"
                            )
                    except Exception:
                        pass

                # Cas spécifique Decipher MX Collapsible (DOM-only + scope question):
                # on mappe le label vers la carte mx-collapsible correspondante.
                label_norm = _norm_lc(label_txt)
                if has_mx_collapsible and label_norm in mx_option_xpath_map:
                    xp = mx_option_xpath_map[label_norm]

                option_xpath_map[cell_col_norm or label_norm] = xp
                if matrix_mode and cell_row_label and cell_col_norm:
                    matrix_cell_xpath_map.setdefault(_norm_lc(cell_row_label), {})[cell_col_norm] = xp

            if len(options) < 2:
                continue

            resolved_itype = "matrix" if matrix_mode and itype == "checkbox" else itype
            group_key = f"{resolved_itype}:name:{name}"
            target_id = make_target_id("group", group_key, question or name)

            register_target(target_id, {
                "kind": "group",
                "frame_chain": list(frame_chain or []),
                "itype": resolved_itype,
                "group_key": group_key,
                "question": question,
                "input_name": name,
                "max_select": 1 if resolved_itype == "radio" else len(options),
                "options": options,
                "option_xpath_map": option_xpath_map,
                "matrix_rows": list(matrix_row_labels.values()) if matrix_mode else [],
                "matrix_columns": options if matrix_mode else [],
                "matrix_cell_xpath_map": matrix_cell_xpath_map if matrix_mode else {},
            })

            blocks.append({
                "target_id": target_id,
                "kind": "group",
                "itype": resolved_itype,
                "question": question,
                "options": options,
                "max_select": 1 if resolved_itype == "radio" else len(options),
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "focusvision_answers_list": True,
                    "aux_openended_names": sorted(aux_openended_input_names),
                    "matrix_rows": list(matrix_row_labels.values()) if matrix_mode else [],
                    "matrix_columns": options if matrix_mode else [],
                },
            })

    return blocks


# ================================================================================
# FOCUSVISION - CARDSORT BLOCK
# ================================================================================

def _extract_focusvision_cardsort_block(driver, frame_chain: list[int] | None) -> dict | None:
    """
    Extrait un bloc cardsort FocusVision (drag & drop de cartes).
    
    Pattern DOM (2 variantes observables):
    - Legacy: div.question.cardsort + .cardsort__card/.cardsort__bucket
    - Decipher sq-cardsort: div.question .sq-cardsort +
      .sq-cardsort-card/.sq-cardsort-bucket
    - Question: .question-text
    
    Stratégie:
    - Identifier le container .cardsort
    - Extraire liste des cartes disponibles
    - Extraire liste des buckets (destinations)
    - Retourner metadata pour traitement ultérieur
    
    Args:
        driver: WebDriver Selenium
        frame_chain: Chaîne de frames ou None
    
    Returns:
        Dict avec metadata ou None si pas trouvé
    """
    selector_profiles = (
        {
            "container": "div.question.cardsort",
            "cards": ".cardsort__card",
            "buckets": ".cardsort__bucket",
            "bucket_label": ".cardsort__bucket-label",
        },
        {
            # Scope DOM minimal: widget Decipher sq-cardsort explicitement présent.
            "container": ".sq-cardsort",
            "cards": ".sq-cardsort-card-legend",
            "buckets": ".sq-cardsort-bucket-legend",
            "bucket_label": ".sq-cardsort-bucket-legend",
        },
    )

    container = None
    profile = None
    for candidate in selector_profiles:
        try:
            container = driver.find_element(By.CSS_SELECTOR, candidate["container"])
            profile = candidate
            break
        except Exception:
            continue

    if container is None or profile is None:
        return None

    # Question text
    question = ""
    question_root = container
    if profile["container"] == ".sq-cardsort":
        try:
            question_root = container.find_element(By.XPATH, "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' question ')][1]")
        except Exception:
            question_root = container
    try:
        question = (question_root.find_element(By.CSS_SELECTOR, ".question-text").text or "").strip()
    except Exception:
        question = (question_root.text or "").strip().split("\n")[0].strip()

    # Cartes
    cards = []
    try:
        card_elements = container.find_elements(By.CSS_SELECTOR, profile["cards"])
        for card in card_elements:
            if profile["container"] == ".sq-cardsort":
                try:
                    card_root = card.find_element(By.XPATH, "ancestor::li[contains(concat(' ',normalize-space(@class),' '),' sq-cardsort-card ')][1]")
                    card_class = (card_root.get_attribute("class") or "").lower()
                    if "sq-cardsort-completion" in card_class:
                        continue
                except Exception:
                    pass
            card_text = (card.text or card.get_attribute("innerText") or "").strip()
            if card_text:
                cards.append(card_text)
    except Exception:
        pass

    # Buckets (catégories de destination)
    buckets = []
    try:
        bucket_elements = container.find_elements(By.CSS_SELECTOR, profile["buckets"])
        for bucket in bucket_elements:
            bucket_text = (bucket.text or bucket.get_attribute("innerText") or "").strip()

            if bucket_text:
                buckets.append(bucket_text)
    except Exception:
        pass

    if not cards or not buckets:
        return None

    # Créer un target_id pour ce cardsort
    target_id = make_target_id("cardsort", "focusvision_cardsort", question)

    # Enregistrer dans dom_registry
    register_target(target_id, {
        "kind": "cardsort",
        "frame_chain": list(frame_chain or []),
        "question": question,
        "cards": cards,
        "buckets": buckets,
        "platform": "focusvision"
    })

    return {
        "target_id": target_id,
        "kind": "cardsort",
        "question": question,
        "cards": cards,
        "buckets": buckets
    }


# ================================================================================
# DECIPHER - TABLE TEXT ROWS (i-question-table)
# ================================================================================

def _extract_decipher_table_text_rows_blocks(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Extrait les lignes texte d'une question Decipher rendue en table i-question-table.

    Pattern strict (garde-fou):
    - div.i-table-wrapper[data-widget-id]
    - table.i-question (texte parent)
    - table.i-question-table avec tr[data-widget-id]
    - 1 input texte/number/textarea éditable par ligne

    Exclusion:
    - lignes readonly (input[readonly])

    Returns:
        Liste de blocks `single` (itype="text") avec question parent + contexte de ligne.
    """
    blocks: List[Dict[str, Any]] = []

    wrappers = driver.find_elements(By.CSS_SELECTOR, "div.i-table-wrapper[data-widget-id]")
    for wrapper in wrappers:
        try:
            grid = wrapper.find_element(By.CSS_SELECTOR, "table.i-question-table")
        except Exception:
            continue

        try:
            rows = grid.find_elements(By.CSS_SELECTOR, "tr[data-widget-id]")
        except Exception:
            rows = []
        if not rows:
            continue

        try:
            question = (
                wrapper.find_element(By.CSS_SELECTOR, "table.i-question td.i-questext").text or ""
            ).strip()
        except Exception:
            question = ""

        if not question:
            continue

        for row in rows:
            try:
                row_label = (row.find_element(By.CSS_SELECTOR, "td.i-questext").text or "").strip()
            except Exception:
                row_label = ""

            try:
                field = row.find_element(By.CSS_SELECTOR, "input[type='text'], input[type='number'], textarea")
            except Exception:
                continue

            # Ne pas soumettre les lignes auto-calculées (ex: Total readonly)
            if field.get_attribute("readonly") is not None:
                continue

            field_id = (field.get_attribute("id") or "").strip()
            field_name = (field.get_attribute("name") or "").strip()
            if not field_id and not field_name:
                continue

            field_tag = (field.tag_name or "input").strip().lower()
            if field_id:
                xpath = f"//*[@id={_xpath_literal(field_id)}]"
            else:
                xpath = f"//{field_tag}[@name={_xpath_literal(field_name)}]"

            single_key = f"decipher_table_text:{field_id}:{field_name}"
            target_id = make_target_id("single", single_key, question)

            register_target(
                target_id,
                {
                    "kind": "single",
                    "itype": "text",
                    "question": question,
                    "xpath": xpath,
                    "alt_xpaths": [],
                    "tag": field_tag,
                    "name": field_name,
                    "id": field_id,
                    "frame_chain": list(frame_chain or []),
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "text",
                    "options": [],
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "single",
                        "tag": field_tag,
                        "name": field_name,
                        "id": field_id,
                        "role": field.get_attribute("role"),
                        "row_label": row_label,
                        "decipher_table_text_rows": True,
                    },
                }
            )

    return blocks


# ================================================================================
# DECIPHER - ANSWERS LIST FALLBACK
# ================================================================================

def _extract_decipher_answers_list_fallback(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Extracteur fallback pour Decipher answers-list (radio/checkbox groups).
    
    Utilisé quand les extracteurs standards échouent. Pattern alternatif:
    - Container: .answer-list
    - Inputs: input[type=radio] / input[type=checkbox]
    - Labels: via for= ou structure parent
    
    Stratégie:
    - Chercher tous les .answer-list containers
    - Pour chaque container, extraire inputs et labels
    - Grouper par name attribute
    - Construire option_xpath_map
    
    Args:
        driver: WebDriver Selenium
        frame_chain: Chaîne de frames
    
    Returns:
        Liste de dicts avec metadata pour dom_registry
    """
    blocks: List[Dict[str, Any]] = []

    try:
        # Chercher tous les containers .answer-list
        containers = driver.find_elements(By.CSS_SELECTOR, ".answer-list")
        
        for container in containers:
            try:
                # Trouver tous les inputs dans ce container
                inputs = container.find_elements(
                    By.CSS_SELECTOR,
                    "input[type='radio'], input[type='checkbox']"
                )
                
                if len(inputs) < 2:
                    continue

                # Déterminer le type (radio ou checkbox)
                first_type = (inputs[0].get_attribute("type") or "").strip().lower()
                itype = "checkbox" if first_type == "checkbox" else "radio"

                # Regrouper par name
                by_name: Dict[str, List] = {}
                for inp in inputs:
                    name = (inp.get_attribute("name") or "").strip()
                    if not name:
                        continue
                    by_name.setdefault(name, []).append(inp)

                # Extraire question text (chercher dans parent ou siblings)
                question = ""
                try:
                    # Essayer de trouver .question-text dans le parent
                    parent = container.find_element(By.XPATH, "..")
                    question_elem = parent.find_element(By.CSS_SELECTOR, ".question-text, .qtext")
                    question = (question_elem.text or "").strip()
                except Exception:
                    # Fallback: prendre le texte du container
                    question = (container.text or "").strip().split("\n")[0].strip()

                # Pour chaque groupe de name
                for name, group_inputs in by_name.items():
                    options: List[str] = []
                    option_xpath_map: Dict[str, str] = {}

                    for inp in group_inputs:
                        inp_id = (inp.get_attribute("id") or "").strip()
                        if not inp_id:
                            continue

                        # Chercher le label associé
                        label_txt = ""
                        try:
                            # Méthode 1: label[for=id]
                            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{inp_id}']")
                            label_txt = _clean_decipher_template_markers((label.text or "").strip())
                        except Exception:
                            try:
                                # Méthode 2: label parent
                                label = inp.find_element(By.XPATH, "ancestor::label[1]")
                                label_txt = _clean_decipher_template_markers((label.text or "").strip())
                            except Exception:
                                # Méthode 3: sibling label
                                try:
                                    label = inp.find_element(By.XPATH, "following-sibling::label[1]")
                                    label_txt = _clean_decipher_template_markers((label.text or "").strip())
                                except Exception:
                                    pass

                        if not label_txt:
                            continue

                        options.append(label_txt)

                        # XPath vers le label (cliquable)
                        xp = f"//label[@for={_xpath_literal(inp_id)}]"
                        option_xpath_map[_norm_lc(label_txt)] = xp

                    if len(options) < 2:
                        continue

                    # Créer group_key et target_id
                    group_key = f"{itype}:name:{name}"
                    target_id = make_target_id("group", group_key, question or name)

                    # Enregistrer dans dom_registry
                    register_target(target_id, {
                        "kind": "group",
                        "frame_chain": list(frame_chain or []),
                        "itype": itype,
                        "group_key": group_key,
                        "question": question,
                        "input_name": name,
                        "max_select": 1 if itype == "radio" else len(options),
                        "options": options,
                        "option_xpath_map": option_xpath_map
                    })

                    blocks.append({
                        "target_id": target_id,
                        "kind": "group",
                        "itype": itype,
                        "question": question,
                        "options": options
                    })

            except Exception as e:
                if os.getenv("RUN_ENV", "local") == "local":
                    print(f"[DOM_ANALYZER][WARN] decipher fallback: {type(e).__name__}: {e}")
                continue

    except Exception as e:
        if os.getenv("RUN_ENV", "local") == "local":
            print(f"[DOM_ANALYZER][ERROR] decipher fallback outer: {type(e).__name__}: {e}")

    return blocks
