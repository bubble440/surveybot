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
        trigger: Any,
        label: str,
        container: Optional[Any],
        options: Optional[List[str]],
        is_native: bool = False,
    ):
        self.trigger = trigger
        self.label = label
        self.container = container
        self.options = options or []
        self.used = False
        # True pour les <select> natifs — détermine la branche idempotence et sélection
        self.is_native = is_native


def _visible(el: Any) -> bool:
    try:
        bb = el.bounding_box() or {}
        return el.is_visible() and bb.get("width", 0) > 10 and bb.get("height", 0) > 10
    except Exception:
        return False


def _el_is_select(el: Any) -> bool:
    """Retourne True si l'élément DOM est un <select> natif."""
    try:
        return el.evaluate("e => e.tagName.toLowerCase()") == "select"
    except Exception:
        return False


def _extract_label(el: Any, driver: Any = None) -> str:
    """
    Récupère le label humain d'un dropdown.

    Ordre de priorité :
    1. aria-labelledby → texte de l'élément référencé (robuste, Nielsen/Decipher)
    2. label[for=id]
    3. aria-label
    4. placeholder
    5. name
    6. texte du conteneur parent immédiat (risqué sur <select> : contient les options)

    La branche (6) est volontairement ignorée pour les <select> natifs car
    inner_text() sur le parent retourne la concaténation de toutes les options,
    ce qui pollue le label et fausse le score Jaccard.
    """
    is_select = _el_is_select(el)

    # 1. aria-labelledby (ex. Nielsen : aria-labelledby="question_text_S11")
    try:
        labelledby = (el.get_attribute("aria-labelledby") or "").strip()
        if labelledby and driver is not None:
            for ref_id in labelledby.split():
                ref = driver.query_selector(f"#{ref_id}")
                if ref is not None:
                    t = (ref.inner_text() or "").strip()
                    if t:
                        return t
    except Exception:
        pass

    # 2. label[for=id]
    try:
        el_id = el.get_attribute("id")
        if el_id:
            # Recherche globale depuis la racine du document
            labs = (driver or el).query_selector_all(f"xpath=//label[@for='{el_id}']")
            if labs:
                t = labs[0].inner_text().strip()
                if t:
                    return t
    except Exception:
        pass

    # 3–5. attributs scalaires
    for attr in ("aria-label", "placeholder", "name"):
        try:
            v = el.get_attribute(attr)
            if v and len(v.strip()) > 1:
                return v.strip()
        except Exception:
            pass

    # 6. texte parent proche — uniquement pour les dropdowns custom (pas les <select>)
    if not is_select:
        try:
            parent = el.query_selector(
                "xpath=ancestor::*[self::div or self::td or self::li][1]"
            )
            if parent is not None:
                txt = parent.inner_text().strip()
                if txt and len(txt) > 1:
                    return txt
        except Exception:
            pass

    return ""


def _collect_dropdown_blocks(driver) -> List[DropdownBlock]:
    blocks: List[DropdownBlock] = []

    # 1) <select> natifs — collectés en premier, marqués is_native=True
    for sel in driver.query_selector_all("select"):
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
                trigger=sel,
                label=_extract_label(sel, driver),
                container=None,
                options=options,
                is_native=True,
            )
        )

    # 2) dropdowns custom (combobox / role=listbox / bouton)
    # Le sélecteur `.dropdown` et `.select` peut matcher des <select> qui portent
    # ces classes CSS (ex. Nielsen : <select class="input dropdown">).
    # On exclut tout élément dont le tagName est "select" pour éviter la double collecte.
    customs = driver.query_selector_all(
        "[role='combobox'], [aria-haspopup='listbox'], .dropdown, .select"
    )

    for el in customs:
        # Exclure les <select> natifs — déjà collectés ci-dessus
        if _el_is_select(el):
            continue

        if not _visible(el):
            continue

        blocks.append(
            DropdownBlock(
                trigger=el,
                label=_extract_label(el, driver),
                container=None,
                options=None,   # options chargées après ouverture
                is_native=False,
            )
        )

    return blocks


# ------------------------------------------------------------
# Résolution principale
# ------------------------------------------------------------

def _selected_text_native(trigger: Any) -> str:
    """
    Lit le texte de l'option actuellement sélectionnée dans un <select> natif.
    N'appelle jamais inner_text() qui retournerait toutes les options concaténées.
    """
    try:
        return (
            trigger.evaluate("e => e.options[e.selectedIndex]?.text || ''") or ""
        ).strip()
    except Exception:
        return ""


def try_resolve_dropdown_block(
    driver,
    *,
    context_question: str,
    value: str,
    debug: bool = False,
) -> bool:
    """
    1) Trouve le dropdown correspondant au contexte question
    2) Vérifie l'idempotence (valeur déjà sélectionnée ?)
    3) Sélectionne la valeur

    Pour les <select> natifs, utilise select_option() Playwright + dispatch events.
    Pour les dropdowns custom, ouvre puis clique l'option visible.
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

    # ---- 1️⃣5️⃣ Idempotence : si la valeur est déjà sélectionnée, NE RIEN FAIRE
    #
    # IMPORTANT : ne jamais appeler inner_text() sur un <select> natif.
    # inner_text() retourne la concaténation de toutes les options (le texte
    # visible du widget entier), pas seulement l'option sélectionnée.
    # Pour les natifs, on lit options[selectedIndex].text via evaluate().
    try:
        if best.is_native:
            cur_txt = _selected_text_native(best.trigger)
        else:
            cur_txt = (best.trigger.inner_text() or "").strip()

        if cur_txt and _jaccard(_norm(value), _norm(cur_txt)) >= 0.9:
            if debug:
                print(f"[DROPDOWN] (skip) valeur déjà sélectionnée: '{cur_txt}'")
            return True
    except Exception:
        pass

    # ---- 2️⃣ Sélectionner la valeur
    if best.is_native:
        # <select> natif : select_option() Playwright (par label) + dispatch events
        try:
            best.trigger.scroll_into_view_if_needed()
            best.trigger.select_option(label=value)
            try:
                best.trigger.evaluate(
                    "(s) => { ['input','change','blur'].forEach(t => "
                    "{ try { s.dispatchEvent(new Event(t, {bubbles:true})) } catch(e) {} }) }"
                )
            except Exception:
                pass
            if debug:
                print(f"[DROPDOWN] ✅ (natif) '{value}' sélectionné pour '{best.label}'")
            return True
        except Exception:
            # Fallback : select_option par valeur d'attribut (fuzzy match sur le texte)
            try:
                opts_data = best.trigger.evaluate(
                    "e => Array.from(e.options).map(o => ({value: o.value, text: o.text.trim()}))"
                )
                target = _norm(value)
                matched_value = None
                for o in (opts_data or []):
                    if _jaccard(target, _norm(o.get("text", ""))) >= 0.85:
                        matched_value = o.get("value")
                        break
                if matched_value is not None:
                    best.trigger.select_option(value=matched_value)
                    if debug:
                        print(f"[DROPDOWN] ✅ (natif/value) '{value}' sélectionné pour '{best.label}'")
                    return True
            except Exception:
                pass
            return False

    # Custom dropdown : ouvrir puis cliquer l'option visible
    try:
        best.trigger.scroll_into_view_if_needed()
        best.trigger.click()
    except Exception:
        return False

    # ---- 3️⃣ Récupérer options visibles après ouverture
    opts = []
    try:
        items = driver.query_selector_all(
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
        return False

    if debug:
        print(f"[DROPDOWN] ✅ (custom) '{value}' sélectionné pour '{best.label}'")

    return True