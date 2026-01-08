# Survey/dropdown_block_resolver.py
# ------------------------------------------------------------
# Dropdown Block Resolver
#
# Objectif :
# Associer un CONTEXTE QUESTION → BON DROPDOWN → BONNE OPTION
#
# - Aucun OCR
# - Aucun screenshot
# - Basé DOM + texte
# - Fallback safe
#
# Pensé pour :
# - DOM éclaté
# - dropdowns custom ou <select>
# - 100+ bots en prod
# ------------------------------------------------------------

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement


# ------------------------------------------------------------
# Utils texte
# ------------------------------------------------------------

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00a0", " ")
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _jaccard(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ------------------------------------------------------------
# Détection dropdowns
# ------------------------------------------------------------

class DropdownBlock:
    def __init__(
        self,
        label: str,
        trigger: WebElement,
        container: Optional[WebElement],
        options: Optional[List[str]],
    ):
        self.label = label
        self.trigger = trigger
        self.container = container
        self.options = options or []
        self.used = False


def _visible(el: WebElement) -> bool:
    try:
        return el.is_displayed() and el.rect["width"] > 10 and el.rect["height"] > 10
    except Exception:
        return False


def _extract_label(el: WebElement) -> str:
    """
    Récupère le label humain d’un dropdown
    """
    # label[for=id]
    try:
        el_id = el.get_attribute("id")
        if el_id:
            labs = el.find_elements(By.XPATH, f"//label[@for='{el_id}']")
            if labs:
                return labs[0].text.strip()
    except Exception:
        pass

    # aria / placeholder / name
    for attr in ("aria-label", "placeholder", "name"):
        try:
            v = el.get_attribute(attr)
            if v and len(v.strip()) > 1:
                return v.strip()
        except Exception:
            pass

    # texte parent proche
    try:
        parent = el.find_element(By.XPATH, "ancestor::*[self::div or self::td or self::li][1]")
        if parent.text and len(parent.text.strip()) > 1:
            return parent.text.strip()
    except Exception:
        pass

    return ""


def _collect_dropdown_blocks(driver) -> List[DropdownBlock]:
    blocks: List[DropdownBlock] = []

    # 1) vrais <select>
    for sel in driver.find_elements(By.TAG_NAME, "select"):
        if not _visible(sel):
            continue

        options = []
        try:
            for o in sel.find_elements(By.TAG_NAME, "option"):
                if o.get_attribute("disabled"):
                    continue
                t = (o.text or "").strip()
                if t:
                    options.append(t)
        except Exception:
            pass

        blocks.append(
            DropdownBlock(
                label=_extract_label(sel),
                trigger=sel,
                container=None,
                options=options,
            )
        )

    # 2) dropdowns custom (combobox / role=listbox / button)
    customs = driver.find_elements(
        By.CSS_SELECTOR,
        "[role='combobox'], [aria-haspopup='listbox'], .dropdown, .select"
    )

    for el in customs:
        if not _visible(el):
            continue

        blocks.append(
            DropdownBlock(
                label=_extract_label(el),
                trigger=el,
                container=None,
                options=None,  # options chargées après ouverture
            )
        )

    return blocks


# ------------------------------------------------------------
# Résolution principale
# ------------------------------------------------------------

def try_resolve_dropdown_block(
    driver,
    *,
    context_question: str,
    value: str,
    debug: bool = False,
) -> bool:
    """
    1) Trouve le dropdown correspondant au contexte
    2) L’ouvre
    3) Sélectionne la valeur
    """
    ctx = _norm(context_question)
    if not ctx:
        return False

    blocks = _collect_dropdown_blocks(driver)
    if not blocks:
        return False

    # ---- 1️⃣ Match contexte → dropdown
    best: Optional[DropdownBlock] = None
    best_score = 0.0

    for b in blocks:
        score = _jaccard(ctx, b.label)
        if score > best_score:
            best_score = score
            best = b

    if not best or best_score < 0.45:
        if debug:
            print("[DROPDOWN] Aucun dropdown pertinent pour ctx:", context_question)
        return False

    # ---- 2️⃣ Ouvrir dropdown
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best.trigger)
        best.trigger.click()
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", best.trigger)
        except Exception:
            return False

    # ---- 3️⃣ Récupérer options visibles
    opts = []
    try:
        items = driver.find_elements(
            By.CSS_SELECTOR,
            "option, [role='option'], li, .dropdown-item"
        )
        for it in items:
            if not _visible(it):
                continue
            t = (it.text or "").strip()
            if t:
                opts.append((t, it))
    except Exception:
        pass

    if not opts:
        return False

    # ---- 4️⃣ Match valeur → option
    val = _norm(value)
    best_opt = None
    best_opt_score = 0.0

    for txt, el in opts:
        sc = _jaccard(val, txt)
        if sc > best_opt_score:
            best_opt_score = sc
            best_opt = el

    if not best_opt or best_opt_score < 0.45:
        if debug:
            print("[DROPDOWN] Valeur non trouvée:", value)
        return False

    # ---- 5️⃣ Cliquer option
    try:
        best_opt.click()
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", best_opt)
        except Exception:
            return False

    if debug:
        print(f"[DROPDOWN] ✅ '{value}' sélectionné pour '{best.label}'")

    return True
