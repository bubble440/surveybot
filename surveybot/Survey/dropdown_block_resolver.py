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
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Playwright page helper
# ---------------------------------------------------------------------------
def _pw_page(d):
    if hasattr(d, '_page'):
        return d._page
    return d


# ------------------------------------------------------------
# Utils texte
# ------------------------------------------------------------

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace(" ", " ")
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
        trigger: Any,
        container: Optional[Any],
        options: Optional[List[str]],
    ):
        self.label = label
        self.trigger = trigger
        self.container = container
        self.options = options or []
        self.used = False


def _visible(el: Any) -> bool:
    try:
        bb = el.bounding_box() or {}
        return el.is_visible() and bb.get("width", 0) > 10 and bb.get("height", 0) > 10
    except Exception:
        return False


def _extract_label(el: Any) -> str:
    """
    Récupère le label humain d'un dropdown
    """
    # label[for=id]
    try:
        el_id = el.get_attribute("id")
        if el_id:
            labs = el.query_selector_all(f"xpath=//label[@for='{el_id}']")
            if labs:
                return labs[0].inner_text().strip()
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
        parent = el.query_selector("xpath=ancestor::*[self::div or self::td or self::li][1]")
        if parent is not None:
            txt = parent.inner_text().strip()
            if txt and len(txt) > 1:
                return txt
    except Exception:
        pass

    return ""


def _collect_dropdown_blocks(driver) -> List[DropdownBlock]:
    blocks: List[DropdownBlock] = []

    # 1) vrais <select>
    for sel in _pw_page(driver).query_selector_all("select"):
        if not _visible(sel):
            continue

        options = []
        try:
            for o in sel.query_selector_all("option"):
                if o.get_attribute("disabled"):
                    continue
                t = (o.inner_text() or "").strip()
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
    customs = _pw_page(driver).query_selector_all(
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
    2) L'ouvre
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

    # ---- 1️⃣5️⃣ Idempotence: si la valeur est déjà sélectionnée, NE RIEN FAIRE (évite reload)
    try:
        cur_txt = ""
        tag = best.trigger.evaluate("e => e.tagName.toLowerCase()")
        if tag == "select":
            try:
                cur_txt = (best.trigger.evaluate("e => e.options[e.selectedIndex]?.text || ''") or "").strip()
            except Exception:
                cur_txt = (best.trigger.get_attribute("value") or "").strip()
        else:
            # dropdown custom: texte affiché dans le trigger
            cur_txt = (best.trigger.inner_text() or "").strip()

        if cur_txt and _jaccard(_norm(value), cur_txt) >= 0.9:
            if debug:
                print(f"[DROPDOWN] (skip) valeur déjà sélectionnée: '{cur_txt}'")
            return True
    except Exception:
        pass

    # ---- 2️⃣ Ouvrir dropdown
    try:
        best.trigger.scroll_into_view_if_needed()
        best.trigger.click()
    except Exception:
        try:
            best.trigger.click()
        except Exception:
            return False

    # ---- 3️⃣ Récupérer options visibles
    opts = []
    try:
        items = _pw_page(driver).query_selector_all(
            "option, [role='option'], li, .dropdown-item"
        )
        for it in items:
            if not _visible(it):
                continue
            t = (it.inner_text() or "").strip()
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
            best_opt.click()
        except Exception:
            return False

    if debug:
        print(f"[DROPDOWN] ✅ '{value}' sélectionné pour '{best.label}'")

    return True
