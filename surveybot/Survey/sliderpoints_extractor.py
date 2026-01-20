# Survey/sliderpoints_extractor.py
from __future__ import annotations

import re
from typing import Any, Dict, List

from selenium.webdriver.common.by import By

from Survey.dom_registry import make_target_id, register_target


def _norm_space(s: str) -> str:
    s = (s or "").replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _xpath_by_id(dom_id: str) -> str:
    dom_id = dom_id or ""
    if "'" not in dom_id:
        return f"//*[@id='{dom_id}']"
    if '"' not in dom_id:
        return f'//*[@id="{dom_id}"]'
    # cas ultra rare
    parts = dom_id.split("'")
    concat = ", \"'\", ".join([f"'{p}'" for p in parts])
    return f"//*[@id=concat({concat})]"


def _extract_legend_options(root) -> List[str]:
    opts: List[str] = []
    try:
        lis = root.find_elements(
            By.CSS_SELECTOR,
            ".sq-sliderpoints-legend li, .sliderpoints_legend .sliderpoints-legenditem"
        )
    except Exception:
        lis = []
    for li in lis:
        t = _norm_space(li.text)
        if t and t not in opts:
            opts.append(t)
    return opts


def _extract_select_options(sel) -> List[str]:
    out: List[str] = []
    try:
        options = sel.find_elements(By.CSS_SELECTOR, "option")
    except Exception:
        options = []
    for o in options:
        try:
            if (o.get_attribute("disabled") or "").strip():
                continue
        except Exception:
            pass
        t = _norm_space(o.text)
        if t and t not in out:
            out.append(t)
    return out


def _extract_continue_button(driver) -> List[Dict[str, Any]]:
    # best-effort : on ne “force” pas si absent
    selectors = [
        "#btn_continue",
        "input#btn_continue",
        "input[type='submit'][name='continue']",
        "button[type='submit']",
    ]
    for css in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, css)
            dom_id = (el.get_attribute("id") or "").strip()
            name = (el.get_attribute("name") or "").strip()
            label = _norm_space(el.get_attribute("value") or el.text or "Continuer")
            if dom_id:
                xp = _xpath_by_id(dom_id)
            else:
                # fallback: xpath par name
                if name:
                    xp = f"//*[@name='{name}']"
                else:
                    continue

            group_key = f"id:{dom_id}" if dom_id else f"name:{name}"
            tid = make_target_id("single", group_key, label)
            register_target(
                tid,
                {
                    "kind": "single",
                    "itype": "button",
                    "xpath": xp,
                    "meta": {"id": dom_id, "name": name, "source": "cta"},
                },
            )

            return [
                {
                    "question": label,
                    "itype": "button",
                    "options": [],
                    "max_select": 1,
                    "target_id": tid,
                    "context": {"id": dom_id, "name": name, "group_key": group_key, "source": "cta"},
                }
            ]
        except Exception:
            continue

    return []


def extract_sliderpoints_question_blocks(driver) -> List[Dict[str, Any]]:
    """
    FocusVision/Decipher sliderpoints:
    - 1 bloc par ligne (row-legend) => dropdown
    - target_id basé sur l'id du select (stable pour replay snapshot)
    - ignore sliderpoints_OO (checkboxes internes)
    """
    try:
        roots = driver.find_elements(By.CSS_SELECTOR, ".sq-sliderpoints")
    except Exception:
        roots = []

    if not roots:
        return []

    blocks: List[Dict[str, Any]] = []

    for root in roots:
        group_question = ""
        try:
            group_question = _norm_space(
                root.find_element(By.CSS_SELECTOR, ".sq-question-text, h1.question-text, .question-text").text
            )
        except Exception:
            group_question = ""

        legend_opts = _extract_legend_options(root)

        try:
            containers = root.find_elements(By.CSS_SELECTOR, ".sq-sliderpoints-container")
        except Exception:
            containers = []

        for c in containers:
            # chaque ligne doit avoir un select
            try:
                sel = c.find_element(By.CSS_SELECTOR, "select")
            except Exception:
                continue

            # texte ligne (row label)
            row_label = ""
            try:
                row_label = _norm_space(c.find_element(By.CSS_SELECTOR, ".sq-sliderpoints-row-legend").text)
            except Exception:
                row_label = ""

            if not row_label:
                # fallback (évite question vide)
                row_label = group_question or "sliderpoints"

            opts = legend_opts or _extract_select_options(sel)
            if not opts:
                continue

            sel_id = (sel.get_attribute("id") or "").strip()
            sel_name = (sel.get_attribute("name") or "").strip()

            # XPath stable (id présent dans tes DOM)
            if sel_id:
                xp = _xpath_by_id(sel_id)
                group_key = f"id:{sel_id}"
            elif sel_name:
                xp = f"//*[@name='{sel_name}']"
                group_key = f"name:{sel_name}"
            else:
                # dernier recours : on skip, pas assez stable pour canonique
                continue

            tid = make_target_id("single", group_key, row_label)
            register_target(
                tid,
                {
                    "kind": "single",
                    "itype": "dropdown",
                    "xpath": xp,
                    "meta": {"id": sel_id, "name": sel_name, "source": "sq-sliderpoints"},
                },
            )

            blocks.append(
                {
                    "question": row_label,
                    "itype": "dropdown",
                    "options": opts,
                    "max_select": 1,
                    "target_id": tid,
                    "context": {
                        "id": sel_id,
                        "name": sel_name,
                        "group_key": group_key,
                        "group_question": group_question,
                        "source": "sq-sliderpoints",
                    },
                }
            )

    # Ajoute CTA si trouvé
    blocks.extend(_extract_continue_button(driver))
    return blocks
