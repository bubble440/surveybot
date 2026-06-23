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

# Import des utilitaires
try:
    from Survey.dom_utils import _norm_lc, _xpath_literal
    from Survey.dom_registry import register_target, make_target_id
    from Survey.log_utils import is_debug, log_debug
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _norm_lc, _xpath_literal
    # dom_registry devra être disponible
    def is_debug(): return False
    def log_debug(tag, msg): pass


def _pw_page(d):
    """Extrait la Page Playwright native depuis un PlaywrightDriverShim ou retourne d tel quel."""
    if hasattr(d, "_page"):
        return d._page
    return d


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


def _has_inline_display_none(el) -> bool:
    """Retourne True quand l'élément porte un style inline `display:none`."""
    style_attr = (el.get_attribute("style") or "").strip().lower()
    if not style_attr:
        return False
    return bool(re.search(r"(?:^|;)\s*display\s*:\s*none\s*(?:;|$)", style_attr))

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
        driver: WebDriver Playwright (Page native ou shim)
        frame_chain: Chaîne de frames ou None

    Returns:
        Liste de dicts avec métadonnées pour dom_registry
    """
    blocks: list[dict] = []
    page = _pw_page(driver)

    def _visible_text(el) -> str:
        txt = (el.inner_text() or "").strip()
        if txt:
            return txt
        raw = (el.text_content() or "").strip()
        return raw

    def _extract_label_text(label_el) -> str:
        """Lit le texte d'un label même quand son conteneur est masqué (display:none)."""
        txt = (label_el.inner_text() or "").strip()
        if txt:
            return _clean_decipher_template_markers(txt)
        raw = (label_el.text_content() or "").strip()
        if raw:
            return _clean_decipher_template_markers(raw)
        return ""

    # Question containers FocusVision
    q_containers = page.query_selector_all("div.question[role='radiogroup'], div.question.radio, div.question.checkbox")
    for q in q_containers:
        if _has_inline_display_none(q):
            continue

        answers = q.query_selector(".answers.answers-list, .answers.answers-table")
        if answers is None:
            continue

        # Inputs masqués (hidden). Variante avec clickableCell
        # Inputs masqués (hidden), variante avec clickableCell
        # => on élargit un peu, mais toujours sous .answers.answers-list (scope strict).
        inputs = answers.query_selector_all(
            "input[type='radio'], input[type='checkbox']"
        )
        if len(inputs) < 2:
            continue

        # Question texte
        question = ""
        _qt = q.query_selector(".question-text")
        if _qt is not None:
            question = (_qt.inner_text() or "").strip()
        else:
            question = (q.inner_text() or "").strip().split("\n")[0].strip()

        group_by_row_table = None
        try:
            candidate_tables = answers.query_selector_all(
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
                if page.query_selector_all(f"#{expected_mx_stage_id}"):
                    mx_stage_id = expected_mx_stage_id

            try:
                col_header_nodes = group_by_row_table.query_selector_all("th[scope='col']")
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
                    row_nodes = group_by_row_table.query_selector_all("tr.row-elements")
                except Exception:
                    row_nodes = []

                for row in row_nodes:
                    row_label = ""
                    row_header_id = ""
                    row_header = row.query_selector("th[scope='row']")
                    if row_header is not None:
                        row_label = _visible_text(row_header)
                        row_header_id = (row_header.get_attribute("id") or "").strip()
                    if not row_label:
                        continue
                    # Skip open-ended rows (e.g. "Autre, préciser"): selecting a radio
                    # on such a row would fail the CTA because the inline OE field is empty.
                    if row_header is not None and row_header.query_selector_all(
                        "input[type='text'].oe"
                    ):
                        continue

                    try:
                        row_inputs = row.query_selector_all("input[type='radio'], input[type='checkbox']")
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
                        cell = inp.query_selector("xpath=ancestor::td[1]")
                        headers_attr = (cell.get_attribute("headers") or "").strip() if cell is not None else ""
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

                            payload["mx_vertical_carousel_next_xpath"] = (
                                f"//div[@id={_xpath_literal(mx_stage_id)}]"
                                "//div[contains(concat(' ',normalize-space(@class),' '),' swiper-button-next ')]"
                            )

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

        # --- group-by-col table : 1 bloc par colonne, lignes = options ---
        # Guard DOM strict : table.grid[data-settings*='group-by-col'][data-settings*='table-mode']
        # + ≥2 th[scope='col'] (en-têtes de colonnes = sous-questions)
        # + inputs name=ans{Q}.{col_idx}.{N} avec col_idx discriminant (middle group)
        group_by_col_table: Any = None
        try:
            cand_gc = answers.query_selector_all(
                "table.grid[data-settings*='group-by-col'][data-settings*='table-mode']",
            )
            group_by_col_table = cand_gc[0] if cand_gc else None
        except Exception:
            group_by_col_table = None

        if group_by_col_table is not None:
            try:
                col_hdr_nodes_gc = group_by_col_table.query_selector_all("th[scope='col']")
            except Exception:
                col_hdr_nodes_gc = []
            col_hdr_texts_gc: list[str] = [_visible_text(h) for h in col_hdr_nodes_gc if _visible_text(h)]

            try:
                row_nodes_gc = group_by_col_table.query_selector_all("tr.row-elements")
            except Exception:
                row_nodes_gc = []

            # col_idx (int) → list of (row_label, input_element)
            col_inputs_gc: dict[int, list[tuple[str, Any]]] = {}
            for row_gc in row_nodes_gc:
                row_lbl_gc = ""
                rh_gc = row_gc.query_selector("th[scope='row']")
                if rh_gc is None:
                    pass
                else:
                    if rh_gc.query_selector_all("input[type='text'].oe"):
                        continue
                    row_lbl_gc = _visible_text(rh_gc)
                if not row_lbl_gc:
                    continue
                try:
                    row_inps_gc = row_gc.query_selector_all(
                        "input[type='radio'], input[type='checkbox']"
                    )
                except Exception:
                    row_inps_gc = []
                for inp_gc in row_inps_gc:
                    raw_nm_gc = (inp_gc.get_attribute("name") or "").strip()
                    m_gc = re.fullmatch(r"ans\d+\.(\d+)\.\d+", raw_nm_gc)
                    if not m_gc:
                        continue
                    ci_gc = int(m_gc.group(1))
                    col_inputs_gc.setdefault(ci_gc, []).append((row_lbl_gc, inp_gc))

            col_indices_gc = sorted(col_inputs_gc.keys())
            if len(col_indices_gc) >= 2 and len(col_hdr_texts_gc) >= 2:
                for ci_gc in col_indices_gc:
                    pairs_gc = col_inputs_gc[ci_gc]
                    if len(pairs_gc) < 2:
                        continue
                    hdr_gc = col_hdr_texts_gc[ci_gc] if ci_gc < len(col_hdr_texts_gc) else f"Col {ci_gc + 1}"
                    itype_gc = "radio"
                    try:
                        if (pairs_gc[0][1].get_attribute("type") or "").strip().lower() == "checkbox":
                            itype_gc = "checkbox"
                    except Exception:
                        pass
                    opts_gc: list[str] = []
                    xmap_gc: dict[str, str] = {}
                    seen_gc: set[str] = set()
                    for lbl_gc, inp_gc2 in pairs_gc:
                        iid_gc = (inp_gc2.get_attribute("id") or "").strip()
                        if not iid_gc:
                            continue
                        lnorm_gc = _norm_lc(lbl_gc)
                        if not lnorm_gc or lnorm_gc in seen_gc:
                            continue
                        seen_gc.add(lnorm_gc)
                        opts_gc.append(lbl_gc)
                        xmap_gc[lnorm_gc] = (
                            f"//input[@id={_xpath_literal(iid_gc)}]"
                            "/ancestor::*[contains(concat(' ',normalize-space(@class),' '),' clickableCell ')"
                            " or contains(concat(' ',normalize-space(@class),' '),' element ')][1]"
                        )
                    if len(opts_gc) < 2:
                        continue
                    raw_col_nm_gc = f"col_{ci_gc}"
                    if pairs_gc:
                        m2_gc = re.match(r"(ans\d+\.\d+)", (pairs_gc[0][1].get_attribute("name") or ""))
                        if m2_gc:
                            raw_col_nm_gc = m2_gc.group(1)
                    col_q_gc = f"{question} [{hdr_gc}]" if question else hdr_gc
                    gkey_gc = f"{itype_gc}:name:{raw_col_nm_gc}"
                    tid_gc = make_target_id("group", gkey_gc, col_q_gc)
                    register_target(tid_gc, {
                        "kind": "group",
                        "frame_chain": list(frame_chain or []),
                        "itype": itype_gc,
                        "group_key": gkey_gc,
                        "question": col_q_gc,
                        "input_name": raw_col_nm_gc,
                        "max_select": 1 if itype_gc == "radio" else len(opts_gc),
                        "options": opts_gc,
                        "option_xpath_map": xmap_gc,
                    })
                    blocks.append({
                        "target_id": tid_gc,
                        "kind": "group",
                        "itype": itype_gc,
                        "question": col_q_gc,
                        "options": opts_gc,
                        "max_select": 1 if itype_gc == "radio" else len(opts_gc),
                        "min_select": 1,
                        "context": {
                            "kind": "group",
                            "group_key": gkey_gc,
                            "focusvision_answers_list": True,
                            "aux_openended_names": [],
                            "matrix_rows": [],
                            "matrix_columns": [],
                        },
                    })
                if blocks:
                    continue

        # Regrouper par name logique
        atm1d_buttons = []
        try:
            atm1d_buttons = q.query_selector_all(
                ".sq-atm1d-widget .sq-atm1d-buttons .sq-atm1d-button[data-label]",
            )
        except Exception:
            atm1d_buttons = []

        if len(atm1d_buttons) >= 2:
            # Deduce itype from role="radiogroup" on the ul, or role="radio" on the li.
            itype_atm1d = "checkbox"
            ul_buttons = q.query_selector(".sq-atm1d-widget .sq-atm1d-buttons")
            if ul_buttons is not None:
                ul_role = (ul_buttons.get_attribute("role") or "").strip().lower()
                if ul_role == "radiogroup":
                    itype_atm1d = "radio"
                elif (atm1d_buttons[0].get_attribute("role") or "").strip().lower() == "radio":
                    itype_atm1d = "radio"

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}
            exclusive_options_norm: list[str] = []
            question_id = (q.get_attribute("id") or "").strip()

            for btn in atm1d_buttons:
                data_label = (btn.get_attribute("data-label") or "").strip()
                if not data_label:
                    continue

                legend = ""
                _le = btn.query_selector(".sq-atm1d-legend")
                if _le is not None:
                    legend = (_le.inner_text() or "").strip()
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
                group_key = f"{itype_atm1d}:atm1d"
                max_sel = 1 if itype_atm1d == "radio" else len(options)
                target_id = make_target_id("group", group_key, question or "atm1d")
                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "frame_chain": list(frame_chain or []),
                        "itype": itype_atm1d,
                        "group_key": group_key,
                        "question": question,
                        "input_name": "atm1d",
                        "max_select": max_sel,
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
                        "itype": itype_atm1d,
                        "question": question,
                        "options": options,
                        "max_select": max_sel,
                        "min_select": 1,
                        "context": {
                            "kind": "group",
                            "group_key": group_key,
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
            candidate_tables = answers.query_selector_all("table.grid")
            matrix_table = candidate_tables[0] if candidate_tables else None
        except Exception:
            matrix_table = None

        raw_triplets: list[tuple[str, str, str]] = []
        if matrix_table is not None:
            for inp in inputs:
                inp_type = (inp.get_attribute("type") or "").strip().lower()
                if inp_type not in ("radio", "checkbox"):
                    continue
                raw_name = (inp.get_attribute("name") or "").strip()
                m = re.fullmatch(r"(ans\d+)\.(\d+)\.(\d+)", raw_name)
                if not m:
                    raw_triplets = []
                    break
                raw_triplets.append((m.group(1), m.group(2), m.group(3)))

        if matrix_table is not None and raw_triplets:
            stems = {t[0] for t in raw_triplets}
            # Use DOM-structural counts instead of name-based col/row cardinality:
            # the name pattern ans{Q}.{VALUE}.{ROW} encodes a value, not a column index,
            # so len(col_idx) would always be 1 even on a multi-column grid.
            try:
                dom_col_count = len(matrix_table.query_selector_all("th[scope='col']"))
            except Exception:
                dom_col_count = 0
            try:
                dom_row_count = len(matrix_table.query_selector_all("th[scope='row']"))
            except Exception:
                dom_row_count = 0
            if len(stems) == 1 and dom_col_count >= 2 and dom_row_count >= 2:
                matrix_mode = True
                matrix_group_name = next(iter(stems))

                # Headers de colonnes observables dans les grilles FocusVision/Decipher.
                try:
                    col_headers = matrix_table.query_selector_all("th[id*='_c']")
                except Exception:
                    col_headers = []
                for h in col_headers:
                    hid = (h.get_attribute("id") or "").strip()
                    m = re.search(r"_c(\d+)$", hid)
                    if not m:
                        continue
                    txt = (h.inner_text() or h.text_content() or "").strip()
                    if txt:
                        matrix_col_labels[m.group(1)] = txt

                try:
                    row_headers = matrix_table.query_selector_all("th[id$='_left']")
                except Exception:
                    row_headers = []
                for h in row_headers:
                    hid = (h.get_attribute("id") or "").strip()
                    m = re.search(r"_r(\d+)_left$", hid)
                    if not m:
                        continue
                    # Exclude open-ended "Autre (préciser)" rows: their row-header
                    # contains an inline text input, so selecting a radio option would
                    # trigger a CTA failure due to the empty OE field.
                    if h.query_selector_all("input[type='text'].oe"):
                        continue
                    txt = (h.inner_text() or h.text_content() or "").strip()
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
                    q.query_selector_all(
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
                    mx_stage = page.query_selector(f"#mx-stage-{qid_suffix}")
                    if mx_stage is not None:
                        mx_rows = mx_stage.query_selector_all(
                            ".mx-collapsible-groupholder .mx-collapsible-row-item[precode]",
                        )
                        if mx_rows:
                            for row in mx_rows:
                                precode = (row.get_attribute("precode") or "").strip()
                                if not precode:
                                    continue
                                _rl = row.query_selector(".label")
                                row_label = _extract_label_text(_rl) if _rl is not None else ""
                                row_norm = _norm_lc(row_label)
                                if not row_norm:
                                    continue
                                mx_option_xpath_map[row_norm] = (
                                    f"//div[@id={_xpath_literal(f'mx-stage-{qid_suffix}')}][1]"
                                    f"//div[contains(concat(' ',normalize-space(@class),' '),' mx-collapsible-groupholder ')]"
                                    f"//div[contains(concat(' ',normalize-space(@class),' '),' mx-collapsible-row-item ')"
                                    f" and @precode={_xpath_literal(precode)}][1]"
                                )

                            mx_exclusive = mx_stage.query_selector_all(
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
                                _exl = ex.query_selector(".mx-btn-label")
                                ex_label = _extract_label_text(_exl) if _exl is not None else ""
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
                _se = q.query_selector(".gridclick .item.current .text-content")
                segment_txt = (_se.inner_text() if _se is not None else "") or ""
                segment_txt = segment_txt.strip()
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
                lab = None
                lab = answers.query_selector(f"label[for='{inp_id}']")
                if lab is not None:
                    label_txt = _extract_label_text(lab)
                    for oe in lab.query_selector_all("input[type='text'], textarea"):
                        oe_name = (oe.get_attribute("name") or "").strip()
                        if oe_name:
                            aux_openended_input_names.add(oe_name)
                else:
                    lab = inp.query_selector("xpath=ancestor::*[contains(@class,'clickableCell')][1]//label")
                    if lab is not None:
                        label_txt = _extract_label_text(lab)
                        for oe in lab.query_selector_all("input[type='text'], textarea"):
                            oe_name = (oe.get_attribute("name") or "").strip()
                            if oe_name:
                                aux_openended_input_names.add(oe_name)
                    else:
                        if os.getenv("RUN_ENV", "local") == "local":
                            pass
                        continue

                if not label_txt:
                    continue

                # Option "Autre ... préciser" avec champ open-ended dans le même label:
                # on exclut cette option du bloc group principal (non gérée en action group ici).
                if lab is not None:
                    if lab.query_selector_all("input[type='text'], textarea"):
                        continue

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
        driver: WebDriver Playwright (Page native ou shim)
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

    page = _pw_page(driver)
    container = None
    profile = None
    for candidate in selector_profiles:
        container = page.query_selector(candidate["container"])
        if container is not None:
            profile = candidate
            break

    if container is None or profile is None:
        return None

    # Question text
    question = ""
    question_root = container
    if profile["container"] == ".sq-cardsort":
        _qr = container.query_selector("xpath=ancestor::div[contains(concat(' ',normalize-space(@class),' '),' question ')][1]")
        question_root = _qr if _qr is not None else container
    _qt = question_root.query_selector(".question-text")
    if _qt is not None:
        question = (_qt.inner_text() or "").strip()
    else:
        question = (question_root.inner_text() or "").strip().split("\n")[0].strip()

    # Cartes
    cards = []
    try:
        card_elements = container.query_selector_all(profile["cards"])
        for card in card_elements:
            if profile["container"] == ".sq-cardsort":
                card_root = card.query_selector("xpath=ancestor::li[contains(concat(' ',normalize-space(@class),' '),' sq-cardsort-card ')][1]")
                if card_root is not None:
                    card_class = (card_root.get_attribute("class") or "").lower()
                    if "sq-cardsort-completion" in card_class:
                        continue
            card_text = (card.inner_text() or "").strip()
            if card_text:
                cards.append(card_text)
    except Exception:
        pass

    # Buckets (catégories de destination)
    buckets = []
    try:
        bucket_elements = container.query_selector_all(profile["buckets"])
        for bucket in bucket_elements:
            bucket_text = (bucket.inner_text() or "").strip()
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
    page = _pw_page(driver)

    wrappers = page.query_selector_all("div.i-table-wrapper[data-widget-id]")
    for wrapper in wrappers:
        grid = wrapper.query_selector("table.i-question-table")
        if grid is None:
            continue

        try:
            rows = grid.query_selector_all("tr[data-widget-id]")
        except Exception:
            rows = []
        if not rows:
            continue

        _qt = wrapper.query_selector("table.i-question td.i-questext")
        question = (_qt.inner_text() if _qt is not None else "") or ""
        question = question.strip()

        if not question:
            continue

        for row in rows:
            _rl = row.query_selector("td.i-questext")
            row_label = (_rl.inner_text() if _rl is not None else "") or ""
            row_label = row_label.strip()

            field = row.query_selector("input[type='text'], input[type='number'], textarea")
            if field is None:
                continue

            # Ne pas soumettre les lignes auto-calculées (ex: Total readonly)
            if field.get_attribute("readonly") is not None:
                continue

            field_id = (field.get_attribute("id") or "").strip()
            field_name = (field.get_attribute("name") or "").strip()
            if not field_id and not field_name:
                continue

            field_tag = (field.evaluate("e => e.tagName.toLowerCase()") or "input").strip()
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
# DECIPHER / FOCUSVISION - GRID SINGLE-COL TEXT ROWS
# ================================================================================

def _extract_decipher_grid_single_col_text_rows(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Extrait les lignes texte d'une grille Decipher/FocusVision rendue en
    table.grid.grid-table-mode avec data-settings contenant 'single-col'.

    Pattern strict (garde-fou):
    - div.question contenant table.grid.grid-table-mode[data-settings*='single-col']
    - tr.row-elements (PAS tr.row-no-answer) avec th.row-legend + td.element input[type='text']
    - Inputs portant la classe 'no-answer' exclus

    Returns:
        Liste de blocks `single` (itype="text"), un par ligne de grille.
    """
    blocks: List[Dict[str, Any]] = []
    page = _pw_page(driver)

    questions = page.query_selector_all("div.question")
    for q_el in questions:
        grid = q_el.query_selector(
            "table.grid.grid-table-mode[data-settings*='single-col']"
        )
        if grid is None:
            continue

        try:
            candidate_inputs = grid.query_selector_all(
                "tr.row-elements:not(.row-no-answer) td.element input[type='text']"
            )
        except Exception:
            candidate_inputs = []

        # Exclure no-answer et vérifier qu'il y a au moins un champ éligible
        candidate_inputs = [
            i for i in candidate_inputs
            if "no-answer" not in (i.get_attribute("class") or "").split()
        ]
        if not candidate_inputs:
            continue

        _qt = q_el.query_selector(".question-text")
        question = (_qt.inner_text() if _qt is not None else "") or ""
        question = question.strip()
        if not question:
            continue

        try:
            rows = grid.query_selector_all(
                "tr.row-elements:not(.row-no-answer)"
            )
        except Exception:
            continue

        for row in rows:
            field = row.query_selector("td.element input[type='text']")
            if field is None:
                continue

            if "no-answer" in (field.get_attribute("class") or "").split():
                continue

            _rl = row.query_selector("th.row-legend")
            row_label = (_rl.inner_text() if _rl is not None else "") or ""
            row_label = row_label.strip()

            field_id = (field.get_attribute("id") or "").strip()
            field_name = (field.get_attribute("name") or "").strip()
            if not field_id and not field_name:
                continue

            field_tag = (field.evaluate("e => e.tagName.toLowerCase()") or "input").strip()
            if field_id:
                xpath = f"//*[@id={_xpath_literal(field_id)}]"
            else:
                xpath = f"//{field_tag}[@name={_xpath_literal(field_name)}]"

            q_label = f"{question} - {row_label}" if row_label else question
            single_key = f"decipher_grid_sc_text:{field_id}:{field_name}"
            target_id = make_target_id("single", single_key, q_label)

            register_target(
                target_id,
                {
                    "kind": "single",
                    "itype": "text",
                    "question": q_label,
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
                    "question": q_label,
                    "itype": "text",
                    "options": [],
                    "max_select": 1,
                    "min_select": 1,
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
# DECIPHER - GRID SELECT (group-by-col + div.fir-select > select)
# ================================================================================

def _extract_decipher_grid_select_blocks(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Extrait les blocs dropdown d'une grille Decipher/FocusVision de type
    div.question.select + table.grid[data-settings*='group-by-col'] +
    cellules div.fir-select > select.input.dropdown.

    Garde-fous DOM stricts:
    - div.question.select (classe 'select' présente — PAS .radio, PAS .checkbox)
    - table.grid.grid-table-mode[data-settings*='group-by-col'][data-settings*='table-mode']
    - présence effective de div.fir-select > select.input.dropdown dans la grille

    Un bloc indépendant (itype='dropdown') est produit par combinaison (ligne × colonne).
    Les options vides (value="-1") sont exclues.
    """
    blocks: List[Dict[str, Any]] = []
    page = _pw_page(driver)

    try:
        q_containers = page.query_selector_all("div.question.select")
    except Exception:
        return blocks

    for q_el in q_containers:
        try:
            if _has_inline_display_none(q_el):
                continue

            # Garde-fou: grille group-by-col table-mode uniquement
            grid = q_el.query_selector(
                "table.grid.grid-table-mode[data-settings*='group-by-col'][data-settings*='table-mode']",
            )
            if grid is None:
                continue

            # Garde-fou: au moins un select dans la grille
            try:
                probe = grid.query_selector_all("div.fir-select > select.input.dropdown")
            except Exception:
                probe = []
            if not probe:
                continue

            # Question text
            question = ""
            _qt = q_el.query_selector(".question-text")
            if _qt is not None:
                question = (_qt.inner_text() or "").strip()
            if not question:
                continue

            # En-têtes de colonnes (dans l'ordre DOM)
            col_labels: List[str] = []
            try:
                for h in grid.query_selector_all("th[scope='col']"):
                    txt = (h.inner_text() or h.text_content() or "").strip()
                    if txt:
                        col_labels.append(txt)
            except Exception:
                pass

            # Lignes de données
            try:
                rows = grid.query_selector_all("tr.row.row-elements")
            except Exception:
                continue

            for row in rows:
                # Label de ligne
                row_label = ""
                rh = row.query_selector("th[scope='row']")
                if rh is not None:
                    row_label = (rh.inner_text() or rh.text_content() or "").strip()

                # Select de la ligne (dans l'ordre DOM = colonnes)
                try:
                    row_selects = row.query_selector_all("div.fir-select > select.input.dropdown")
                except Exception:
                    row_selects = []

                for col_idx, sel in enumerate(row_selects):
                    sel_id = (sel.get_attribute("id") or "").strip()
                    sel_name = (sel.get_attribute("name") or "").strip()
                    if not sel_id and not sel_name:
                        continue

                    # Options (valeur "-1" = placeholder vide → exclue)
                    options: List[str] = []
                    try:
                        for opt in sel.query_selector_all("option"):
                            if (opt.get_attribute("value") or "").strip() == "-1":
                                continue
                            txt = (opt.inner_text() or opt.text_content() or "").strip()
                            txt = txt.replace("\xa0", " ").strip()
                            if txt:
                                options.append(txt)
                    except Exception:
                        pass

                    col_label = col_labels[col_idx] if col_idx < len(col_labels) else f"col{col_idx + 1}"

                    parts = [question]
                    if row_label:
                        parts.append(row_label)
                    parts.append(col_label)
                    q_label = " - ".join(parts)

                    xpath = (
                        f"//*[@id={_xpath_literal(sel_id)}]"
                        if sel_id
                        else f"//select[@name={_xpath_literal(sel_name)}]"
                    )

                    target_id = make_target_id("single", f"select:{sel_id or sel_name}", q_label)

                    register_target(
                        target_id,
                        {
                            "kind": "single",
                            "itype": "select",
                            "question": q_label,
                            "xpath": xpath,
                            "alt_xpaths": [],
                            "tag": "select",
                            "name": sel_name,
                            "id": sel_id,
                            "frame_chain": list(frame_chain or []),
                            "options": options,
                            "row_label": row_label,
                            "col_label": col_label,
                            "decipher_grid_select": True,
                        },
                    )

                    blocks.append(
                        {
                            "question": q_label,
                            "itype": "dropdown",
                            "options": options,
                            "max_select": 1,
                            "min_select": 1,
                            "target_id": target_id,
                            "context": {
                                "kind": "single",
                                "itype": "select",
                                "name": sel_name,
                                "id": sel_id,
                                "row_label": row_label,
                                "col_label": col_label,
                                "decipher_grid_select": True,
                            },
                        }
                    )

        except Exception as e:
            from Survey.log_utils import is_debug, log_debug
            if is_debug():
                log_debug("[DECIPHER_GRID_SELECT]", f"error on q_el: {type(e).__name__}: {e}")
            continue

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
        driver: WebDriver Playwright (Page native ou shim)
        frame_chain: Chaîne de frames

    Returns:
        Liste de dicts avec metadata pour dom_registry
    """
    blocks: List[Dict[str, Any]] = []
    page = _pw_page(driver)

    try:
        # Chercher tous les containers .answer-list
        containers = page.query_selector_all(".answer-list")

        for container in containers:
            try:
                # Trouver tous les inputs dans ce container
                inputs = container.query_selector_all(
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
                parent = container.query_selector("xpath=..")
                if parent is not None:
                    question_elem = parent.query_selector(".question-text, .qtext")
                    if question_elem is not None:
                        question = (question_elem.inner_text() or "").strip()
                if not question:
                    # Fallback: prendre le texte du container
                    question = (container.inner_text() or "").strip().split("\n")[0].strip()

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
                        label = page.query_selector(f"label[for='{inp_id}']")
                        if label is not None:
                            label_txt = _clean_decipher_template_markers((label.inner_text() or "").strip())
                        else:
                            label = inp.query_selector("xpath=ancestor::label[1]")
                            if label is not None:
                                label_txt = _clean_decipher_template_markers((label.inner_text() or "").strip())
                            else:
                                label = inp.query_selector("xpath=following-sibling::label[1]")
                                if label is not None:
                                    label_txt = _clean_decipher_template_markers((label.inner_text() or "").strip())

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


# ================================================================================
# DECIPHER — ATMRATING (sq-atmrating, boutons 1..N sur inputs text cachés)
# ================================================================================

def _extract_decipher_atmrating_blocks(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Extrait les blocs d'une question Decipher sq-atmrating (rating par affirmations).

    Guard DOM strict (double) :
    1. div.question.sq-atmrating présent dans le DOM
    2. contient au moins un div.sq-atmrating-container avec span.atmrating-btn

    Produit N blocs radio (un par sous-question / sq-atmrating-container) :
    - itype='radio', options = valeurs des boutons (ex: ["1","2","3","4","5"])
    - question = texte global h1.question-text + " - " + texte div.sq-atmrating-row-legend
    - target_id ancré sur input[@name] (discriminant par container)
    - XPath cible : span.atmrating-btn dans le container (clic JS)
    - Flag payload : decipher_atmrating=True
    Log discriminant : [DOM_DECIPHER_ATMRATING] blocks_extracted=N
    """
    blocks: List[Dict[str, Any]] = []
    page = _pw_page(driver)

    # Guard 1 : question container sq-atmrating
    try:
        q_containers = page.query_selector_all("div.question.sq-atmrating")
    except Exception:
        return blocks
    if not q_containers:
        return blocks

    for q_el in q_containers:
        try:
            # Guard 2 : au moins un container avec boutons atmrating
            try:
                probe = q_el.query_selector_all("div.sq-atmrating-container span.atmrating-btn")
            except Exception:
                probe = []
            if not probe:
                continue

            # Question globale
            global_q = ""
            _qte = q_el.query_selector("h1.question-text")
            if _qte is not None:
                global_q = (_qte.inner_text() or "").strip()

            # Instruction optionnelle (fusionnée)
            instruction = ""
            _ins = q_el.query_selector("h2.instruction-text")
            if _ins is not None:
                instruction = (_ins.inner_text() or "").strip()
            q_prefix = f"{global_q} {instruction}".strip() if instruction else global_q

            # Valeurs des boutons (identiques pour tous les containers — lire une fois)
            btn_values: List[str] = []
            try:
                first_btns = q_el.query_selector_all(
                    "div.sq-atmrating-container:first-child span.atmrating-btn"
                )
                for b in first_btns:
                    # Les boutons portent des zero-width spaces — strip agressif
                    raw = (b.text_content() or b.inner_text() or "").strip()
                    raw = raw.replace("​", "").strip()
                    if raw:
                        btn_values.append(raw)
            except Exception:
                pass
            if not btn_values:
                continue

            # Un bloc par sous-question (div.sq-atmrating-container)
            try:
                row_containers = q_el.query_selector_all("div.sq-atmrating-container")
            except Exception:
                continue

            for row_el in row_containers:
                # Texte de la sous-question
                row_legend = ""
                _rle = row_el.query_selector("div.sq-atmrating-row-legend")
                if _rle is not None:
                    row_legend = (_rle.text_content() or "").strip()
                if not row_legend:
                    continue

                # Input caché portant le name discriminant
                inp_name = ""
                inp_id = ""
                inp = row_el.query_selector("input[type='text']")
                if inp is not None:
                    inp_name = (inp.get_attribute("name") or "").strip()
                    inp_id = (inp.get_attribute("id") or "").strip()
                if not inp_name and not inp_id:
                    continue

                question_text = f"{q_prefix} - {row_legend}" if q_prefix else row_legend

                # XPath par bouton : ancré sur le container via l'input discriminant,
                # puis span.atmrating-btn correspondant à la valeur (position 1-based).
                option_xpath_map: Dict[str, str] = {}
                for btn_idx, val in enumerate(btn_values, start=1):
                    val_norm = _norm_lc(val)
                    if not val_norm:
                        continue
                    # Scope strict : container qui contient l'input discriminant
                    if inp_id:
                        xp = (
                            f"(//div[contains(concat(' ',normalize-space(@class),' '),' sq-atmrating-container ')"
                            f" and .//input[@id={_xpath_literal(inp_id)}]]"
                            f"//span[contains(concat(' ',normalize-space(@class),' '),' atmrating-btn ')])[{btn_idx}]"
                        )
                    else:
                        xp = (
                            f"(//div[contains(concat(' ',normalize-space(@class),' '),' sq-atmrating-container ')"
                            f" and .//input[@name={_xpath_literal(inp_name)}]]"
                            f"//span[contains(concat(' ',normalize-space(@class),' '),' atmrating-btn ')])[{btn_idx}]"
                        )
                    option_xpath_map[val_norm] = xp

                if len(option_xpath_map) < 2:
                    continue

                group_key = f"radio:atmrating:{inp_name or inp_id}"
                target_id = make_target_id("group", group_key, question_text)

                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "frame_chain": list(frame_chain or []),
                        "itype": "radio",
                        "group_key": group_key,
                        "question": question_text,
                        "input_name": inp_name,
                        "max_select": 1,
                        "options": btn_values,
                        "option_xpath_map": option_xpath_map,
                        "decipher_atmrating": True,
                    },
                )

                blocks.append(
                    {
                        "target_id": target_id,
                        "kind": "group",
                        "itype": "radio",
                        "question": question_text,
                        "options": list(btn_values),
                        "max_select": 1,
                        "min_select": 1,
                        "context": {
                            "kind": "group",
                            "group_key": group_key,
                            "decipher_atmrating": True,
                        },
                    }
                )

        except Exception as exc:
            if is_debug():
                log_debug("[DOM_DECIPHER_ATMRATING]", f"error: {type(exc).__name__}: {exc}")
            continue

    if blocks:
        log_debug("[DOM_DECIPHER_ATMRATING]", f"blocks_extracted={len(blocks)}")

    return blocks


# ================================================================================
# DECIPHER / NORSTAT — RANKSORT DROPDOWN (sq-ranksort, table.grid display:none)
# ================================================================================

def _extract_decipher_ranksort_dropdown_blocks(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Extrait UN seul bloc checkbox pour une question de classement Decipher/NorstatSurveys.

    Guard DOM strict :
    - div.question.sq-ranksort (classe 'sq-ranksort' obligatoire)
    - table.grid contenant des tr.row.row-elements avec <th> (item) + select.input.dropdown

    Cas couvert : la table.grid a style="display:none" → les selects sont CSS-cachés
    et rejetés par le filtre not_actionable_visible du pipeline générique.

    Produit UN bloc unique :
    - itype='checkbox', options = liste des textes d'items dans l'ordre DOM
    - max_select = nombre de rangs disponibles (= nombre d'options hors placeholder)
    - Registry payload : rank_labels + item_select_map (item_norm → {sel_id, sel_name})
    - Le dispatcher assigne "Rang N" au Nième item retourné par GPT.
    """
    blocks: List[Dict[str, Any]] = []
    page = _pw_page(driver)

    try:
        containers = page.query_selector_all("div.question.sq-ranksort")
    except Exception:
        return blocks

    if not containers:
        return blocks

    for q_el in containers:
        try:
            # Question globale
            global_q = ""
            _qte = q_el.query_selector("h1.question-text")
            if _qte is not None:
                global_q = (_qte.inner_text() or "").strip()
            if not global_q:
                continue

            # Instruction optionnelle fusionnée dans la question
            instruction = ""
            _ins = q_el.query_selector("h2.instruction-text")
            if _ins is not None:
                instruction = (_ins.inner_text() or "").strip()
            question_text = f"{global_q} {instruction}".strip() if instruction else global_q

            # Table grid (display:none — accès DOM direct sans vérification visibilité)
            grid = q_el.query_selector("table.grid")
            if grid is None:
                continue

            # Lignes de données
            try:
                rows = grid.query_selector_all("tr.row.row-elements")
            except Exception:
                rows = []

            if not rows:
                continue

            # Collecter items + sélects dans l'ordre DOM
            item_texts: List[str] = []
            item_select_map: Dict[str, Dict[str, str]] = {}
            rank_labels: List[str] = []

            for row in rows:
                # Texte de l'item (th)
                item_text = ""
                th = row.query_selector("th")
                if th is not None:
                    item_text = (th.inner_text() or th.text_content() or "").strip()
                if not item_text:
                    continue

                # Select dropdown de cet item
                sel = row.query_selector("select.input.dropdown")
                if sel is None:
                    continue

                sel_id = (sel.get_attribute("id") or "").strip()
                sel_name = (sel.get_attribute("name") or "").strip()
                if not sel_id and not sel_name:
                    continue

                # Rang labels (identiques pour tous les selects — lire une fois)
                if not rank_labels:
                    try:
                        for opt in sel.query_selector_all("option"):
                            if (opt.get_attribute("value") or "").strip() == "-1":
                                continue
                            txt = (opt.inner_text() or opt.text_content() or "").replace("\xa0", " ").strip()
                            if txt:
                                rank_labels.append(txt)
                    except Exception:
                        pass

                item_texts.append(item_text)
                item_select_map[_norm_lc(item_text)] = {"sel_id": sel_id, "sel_name": sel_name}

            if not item_texts or not rank_labels:
                continue

            max_select = len(rank_labels)

            # group_key stable (basé sur la question normalisée)
            group_key = f"decipher_ranksort:checkbox:{_norm_lc(global_q)[:60]}"
            target_id = make_target_id("group", group_key, question_text)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "checkbox",
                    "question": question_text,
                    "options": item_texts,
                    "max_select": max_select,
                    "rank_labels": rank_labels,
                    "item_select_map": item_select_map,
                    "frame_chain": list(frame_chain or []),
                    "decipher_ranksort_dropdown": True,
                },
            )

            blocks.append(
                {
                    "question": question_text,
                    "itype": "checkbox",
                    "options": item_texts,
                    "max_select": max_select,
                    "min_select": max_select,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "itype": "checkbox",
                        "decipher_ranksort_dropdown": True,
                    },
                }
            )

            log_debug(
                "[DOM_DECIPHER_RANKSORT]",
                f"extracted 1 ranksort block: {len(item_texts)} items, {max_select} ranks",
            )

        except Exception as exc:
            if is_debug():
                log_debug("[DOM_DECIPHER_RANKSORT]", f"error: {type(exc).__name__}: {exc}")
            continue

    return blocks


# ================================================================================
# DECIPHER / QARTS - HIDDEN ANSWERS CONTAINER
# ================================================================================

def _extract_qarts_hidden_answers_groups(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Extrait les blocs radio/checkbox du conteneur masqué des widgets QARTS (Decipher/FocusVision).

    Garde-fous DOM stricts (double signal obligatoire) :
    - div[data-test="main-contain"] présent  →  widget QARTS actif
    - div.hidden.answers contenant input[type='radio'] ou input[type='checkbox']

    Les inputs sont stables (id, name, value, qartsid) mais dans un div CSS-masqué.
    On cible l'ancêtre td.clickableCell via JS-click (bypass visibilité).
    """
    blocks: List[Dict[str, Any]] = []
    page = _pw_page(driver)

    # Guard 1 : interface visuelle QARTS active
    #   div[id^="sq-QARTS-container-"] contenant div._rowpicker
    try:
        qarts_containers = [
            c for c in page.query_selector_all("div[id^='sq-QARTS-container-']")
            if c.query_selector_all("div._rowpicker")
        ]
        if not qarts_containers:
            return blocks
    except Exception:
        return blocks

    # Guard 2 : grille cachée avec inputs portant l'attribut qartsqname
    try:
        hidden_containers = page.query_selector_all("div.hidden.answers")
    except Exception:
        return blocks
    if not hidden_containers:
        return blocks
    try:
        has_qartsqname = any(
            hc.query_selector_all("input[qartsqname]")
            for hc in hidden_containers
        )
    except Exception:
        has_qartsqname = False
    if not has_qartsqname:
        return blocks

    def _label_text(el) -> str:
        txt = (el.inner_text() or "").strip()
        if txt:
            return _clean_decipher_template_markers(txt)
        raw = (el.text_content() or "").strip()
        if raw:
            return _clean_decipher_template_markers(raw)
        return ""

    for hidden_div in hidden_containers:
        try:
            inputs = hidden_div.query_selector_all(
                "input[type='radio'], input[type='checkbox']"
            )
        except Exception:
            continue
        if len(inputs) < 2:
            continue

        # Question text — chercher globalement le .question-text le plus proche
        question = ""
        qt_el = page.query_selector(".question-text, h1.question-text")
        if qt_el is not None:
            question = _label_text(qt_el)

        itype = "radio"
        try:
            if (inputs[0].get_attribute("type") or "").lower() == "checkbox":
                itype = "checkbox"
        except Exception:
            pass

        all_raw_names: Set[str] = {
            (inp.get_attribute("name") or "").strip()
            for inp in inputs
            if (inp.get_attribute("name") or "").strip()
        }
        by_name: Dict[str, list] = {}
        for inp in inputs:
            name = (inp.get_attribute("name") or "").strip()
            if not name:
                continue
            name = _logical_answers_list_group_name(name, all_raw_names)
            by_name.setdefault(name, []).append(inp)

        for name, inps in by_name.items():
            options: List[str] = []
            option_xpath_map: Dict[str, str] = {}
            seen: Set[str] = set()

            for inp in inps:
                inp_id = (inp.get_attribute("id") or "").strip()
                if not inp_id:
                    continue

                label_txt = ""
                lab = hidden_div.query_selector(f"label[for='{inp_id}']")
                if lab is not None:
                    label_txt = _label_text(lab)

                if not label_txt:
                    continue

                norm = _norm_lc(label_txt)
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                options.append(label_txt)

                # Cible td.clickableCell (ancêtre visible-JS) ; fallback .element
                xp = (
                    f"//input[@id={_xpath_literal(inp_id)}]"
                    f"/ancestor::*[contains(concat(' ',normalize-space(@class),' '),' clickableCell ')"
                    f" or contains(concat(' ',normalize-space(@class),' '),' element ')][1]"
                )
                option_xpath_map[norm] = xp

            if len(options) < 2:
                continue

            # Detect qa:autosubmit flag from page-level DQ.questions JS object.
            # Only applies to radio: on checkbox qa:autosubmit does not trigger immediate
            # navigation (it may enable the CTA button, but a CTA click is still required).
            qarts_autosubmit = False
            if itype == "radio":
                try:
                    qname = (inps[0].get_attribute("qartsqname") or "").strip()
                    if qname:
                        qarts_autosubmit = bool(page.evaluate(
                            "(q) => { try { return !!(window.DQ&&DQ.questions&&DQ.questions[q]"
                            "&&DQ.questions[q].q&&DQ.questions[q].q['qa:autosubmit']); }"
                            " catch(e) { return false; } }",
                            qname
                        ))
                        log_debug("[QARTS_HIDDEN]", f"autosubmit={qarts_autosubmit} qname={qname}")
                except Exception:
                    qarts_autosubmit = False

            group_key = f"{itype}:name:{name}"
            target_id = make_target_id("group", group_key, question or name)

            register_target(target_id, {
                "kind": "group",
                "frame_chain": list(frame_chain or []),
                "itype": itype,
                "group_key": group_key,
                "question": question,
                "input_name": name,
                "max_select": 1 if itype == "radio" else len(options),
                "options": options,
                "option_xpath_map": option_xpath_map,
                "qarts_hidden": True,
                "qarts_widget": True,
                "qarts_autosubmit": qarts_autosubmit,
            })

            blocks.append({
                "target_id": target_id,
                "kind": "group",
                "itype": itype,
                "question": question,
                "options": options,
                "max_select": 1 if itype == "radio" else len(options),
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "qarts_hidden": True,
                    "qarts_autosubmit": qarts_autosubmit,
                },
            })

            log_debug("[QARTS_HIDDEN]", f"extracted group name={name} options={len(options)} question={question[:40]!r}")

    return blocks
