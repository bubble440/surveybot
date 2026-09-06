"""
input_handler.py - Façade rétro-compatible pour la gestion des inputs de sondage

Ce fichier ré-exporte toutes les fonctions des modules spécialisés pour
maintenir la compatibilité avec le code existant.

Architecture modulaire:
- input_utils.py     : Constantes, normalisation, helpers DOM génériques
- input_frame.py     : Gestion des iframes
- input_dropdown.py  : Dropdowns et selects
- input_text.py      : Champs texte et textarea
- input_radio.py     : Boutons radio
- input_checkbox.py  : Cases à cocher
- input_slider.py    : Sliders (Decipher/Behaviorally)
- input_matrix.py    : Questions matricielles
- cta_handler.py     : Boutons CTA et navigation

Usage:
    from input_handler import click_radio_by_label, fill_text_input, ...
    # Fonctionne exactement comme avant
"""

import time
import re
import unicodedata






# =============================================================================
# IMPORTS DEPUIS LES MODULES SPÉCIALISÉS
# =============================================================================

# --- input_utils.py ---
from Survey.input_utils import (
    # Constantes
    DROPDOWN_PLACEHOLDERS,
    PLACEHOLDER_TOKENS,
    CTA_SYNONYMS,
    DATE_HINTS,
    MATRIX_COL_SYNONYMS,
    DEBUG_PAUSE,
    # Fonctions de normalisation
    norm_txt,
    norms_txt,
    normt_txt,
    norm,
    norm_text,
    norm_hint,
    norm_soft,
    norm_lc_soft,
    normalize_lbl,
    norm_btn_text,
    strip_accents,
    xpath_literal,
    # Helpers DOM génériques
    pause_here,
    scroll_into_view,
    js_click,
    safe_click,
    is_checked,
    looks_like_nav_label,
    set_input_value_with_events,
    find_inputs_by_hint,
    # Helpers contexte/scoping
    find_question_container_by_ctx,
    find_questions_container,
    find_context_container,
    # Helpers open-ended
    has_visible_open_ended_field,
    ensure_open_ended_open,
    split_typed_instruction,
    # Viewport helpers
    viewport_penalty,
    similarity,
)

# Aliases pour rétrocompatibilité (noms avec underscore)
_norm_txt = norm_txt
_norms_txt = norms_txt
_normt_txt = normt_txt
_norm = norm
_norm_text = norm_text
_norm_hint = norm_hint
_norm_soft = norm_soft
_norm_lc_soft = norm_lc_soft
_normalize_lbl = normalize_lbl
_norm_btn_text = norm_btn_text
_strip_accents = strip_accents
_xpath_literal = xpath_literal
_pause_here = pause_here
_scroll_into_view = scroll_into_view
_js_click = js_click
_safe_click = safe_click
_is_checked = is_checked
_looks_like_nav_label = looks_like_nav_label
_set_input_value_with_events = set_input_value_with_events
_find_inputs_by_hint = find_inputs_by_hint
_find_question_container_by_ctx = find_question_container_by_ctx
_find_questions_container = find_questions_container
_find_context_container = find_context_container
_has_visible_open_ended_field = has_visible_open_ended_field
_ensure_open_ended_open = ensure_open_ended_open
_split_typed_instruction = split_typed_instruction
_viewport_penalty = viewport_penalty
_similarity = similarity

# --- input_frame.py ---
from Survey.input_frame import (
    iter_iframes_safe,
    in_each_frame_recursive,
    click_button_by_text_any_context as _click_button_by_text_any_context_frame,
    click_icon_like_button_any_context as _click_icon_like_button_any_context_frame,
    click_primary_cta_any_context as _click_primary_cta_any_context_frame,
    try_click_navigation_cta_any_context as _try_click_navigation_cta_any_context_frame,
    click_cta_strong_any_context as _click_cta_strong_any_context_frame,
)

# Aliases pour rétrocompatibilité
_iter_iframes_safe = iter_iframes_safe
_in_each_frame_recursive = in_each_frame_recursive

# --- input_dropdown.py ---
from Survey.input_dropdown import (
    has_native_selects,
    select_like_elements,
    element_signature_text,
    viewport_penalty as dropdown_viewport_penalty,
    best_dropdown_for_hint,
    dropdown_visible_value,
    is_dropdown_filled,
    open_first_dropdown,
    open_dropdown_generic,
    select_option_with_hint,
    select_option_with_hint,
    select_native_option_by_target,
)

# Aliases pour rétrocompatibilité
_has_native_selects = has_native_selects
_select_like_elements = select_like_elements
_element_signature_text = element_signature_text
_best_dropdown_for_hint = best_dropdown_for_hint
_dropdown_visible_value = dropdown_visible_value
_is_dropdown_filled = is_dropdown_filled
_open_first_dropdown = open_first_dropdown
_open_dropdown_generic = open_dropdown_generic
_select_option_with_hint = select_option_with_hint
_select_option_with_hint = select_option_with_hint

# --- input_text.py ---
from Survey.input_text import (
    type_via_cdp,
    react_set_value_and_fire,
    is_numeric_field,
    swagbucks_zip_patch,
    fill_text_input,
    fill_native_date_input,
    fill_ifop_zip2city_widget,
    fill_text_input_by_id_in_frame,
)

# Aliases pour rétrocompatibilité
_type_via_cdp = type_via_cdp
_react_set_value_and_fire = react_set_value_and_fire
_is_numeric_field = is_numeric_field
_swagbucks_zip_patch = swagbucks_zip_patch

# --- input_radio.py ---
from Survey.input_radio import (
    click_decipher_grid_radio,
    click_decipher_grid_radio_strict,
    click_radio_label_in_scope,
    fallback_click_radio_js_generic,
    click_radio_by_label,
)

# Aliases pour rétrocompatibilité
_click_decipher_grid_radio = click_decipher_grid_radio
_click_decipher_grid_radio_strict = click_decipher_grid_radio_strict
_click_radio_label_in_scope = click_radio_label_in_scope
_fallback_click_radio_js_generic = fallback_click_radio_js_generic

# --- input_checkbox.py ---
from Survey.input_checkbox import (
    force_checkbox_events,
    privacy_checkbox_is_accepted,
    force_label_for_checkbox_js,
    fallback_click_checkbox_js_alchemer,
    fallback_click_checkbox_js_generic,
    click_checkbox_buttonish_by_label,
    click_confirmit_checktable,
    click_checkbox_by_label,
)

# Aliases pour rétrocompatibilité
_force_checkbox_events = force_checkbox_events
_privacy_checkbox_is_accepted = privacy_checkbox_is_accepted
_force_label_for_checkbox_js = force_label_for_checkbox_js
_fallback_click_checkbox_js_alchemer = fallback_click_checkbox_js_alchemer
_fallback_click_checkbox_js_generic = fallback_click_checkbox_js_generic

# --- input_slider.py ---
from Survey.input_slider import (
    set_sliderpoints,
)

# --- input_matrix.py ---
from Survey.input_matrix import (
    MATRIX_COL_SYNONYMS as MATRIX_COL_SYNONYMS_MODULE,
    looks_like_matrix,
    iter_matrix_rows,
    get_matrix_columns,
    select_cell_action,
    click_matrix_cell_by_row_and_col,
    apply_matrix_column_to_all_rows,
)

# Aliases pour rétrocompatibilité
_looks_like_matrix = looks_like_matrix
_iter_matrix_rows = iter_matrix_rows
_get_matrix_columns = get_matrix_columns
_select_cell_action = select_cell_action

# --- cta_handler.py ---
from Survey.cta_handler import (
    looks_like_nav_label as cta_looks_like_nav_label,
    click_button_by_text,
    click_icon_like_button,
    click_primary_cta,
    try_click_navigation_cta,
    click_button_by_text_any_context,
    click_icon_like_button_any_context,
    click_primary_cta_any_context,
    try_click_navigation_cta_any_context,
    click_cta_strong_any_context,
)


# =============================================================================
# FONCTIONS D'ORCHESTRATION (gardées ici car dépendent de plusieurs modules)
# =============================================================================

def handle_generic_input(driver, gpt_answer: str):
    """
    Détecte dynamiquement le type d'input et applique l'action.
    - Si 'gpt_answer' est un placeholder de dropdown → on ouvre un dropdown au lieu d'écrire.
    - Si des <select> existent et que 'gpt_answer' ressemble à une option → on tente de la sélectionner.
    - Si 'gpt_answer' ressemble à un CTA → on laisse la logique bouton.
    """
    page = driver
    try:
        if looks_like_nav_label(gpt_answer):
            return False  # géré côté CTA

        ans_norm = norm_txt(gpt_answer)

        # 🧮 Cas MATRICE : si la réponse ressemble à un EN-TÊTE DE COLONNE,
        # on applique cette colonne à toutes les lignes non répondues.
        try:
            if apply_matrix_column_to_all_rows(driver, gpt_answer):
                print(
                    f"🧮 Matrice détectée : colonne « {gpt_answer} » appliquée à toutes les lignes. source: input_handler.py"
                )
                return True
        except Exception as e:
            print("❌ Erreur matrix handler : source: input_handler.py", e)

        # 0) Gestion dropdowns en priorité quand placeholder
        if ans_norm in PLACEHOLDER_TOKENS:
            if has_native_selects(driver) or page.query_selector_all(
                "[role='combobox'], [aria-haspopup='listbox']"
            ):
                return open_first_dropdown(driver)
            print(
                "⚠️ Placeholder reçu mais aucun dropdown détecté. source: input_handler.py"
            )
            return False

        # 0-bis) Si on a un select visible et une réponse non-CTA, tenter la sélection directe
        if has_native_selects(driver):
            if select_option_with_hint(driver, gpt_answer):
                return True

        # 1. Radios
        radio_inputs = page.query_selector_all("input[type='radio'], [role='radio']")
        if radio_inputs:
            print("🔘 Options radio détectées. source: input_handler.py")
            return click_radio_by_label(driver, gpt_answer)

        # 2. Checkboxes
        checkboxes = page.query_selector_all("input[type='checkbox'], [role='checkbox']")
        if checkboxes:
            print("☑️ Checkboxs détectées. source: input_handler.py")
            return click_checkbox_by_label(driver, gpt_answer)

        # 3. Texte (⚠ ignorer les placeholders)
        text_inputs = page.query_selector_all("input[type='text'], textarea")
        if text_inputs:
            if ans_norm in PLACEHOLDER_TOKENS:
                print(
                    "⏭ Placeholder ignoré pour le champ texte. source: input_handler.py"
                )
                return False
            print("✍️ Champ texte détecté. source: input_handler.py")
            return fill_text_input(driver, gpt_answer)

        print("❌ Aucun input connu géré. source: input_handler.py")
        return False

    except Exception as e:
        print("💥 Erreur dans handle_generic_input : source: input_handler.py", e)
        return False


def apply_ai_response(driver, response):
    """
    Essaye d'appliquer dynamiquement la réponse de l'assistant IA
    à tous les types d'inputs (texte, bouton, checkbox...).
    ⚠ NEW: si 'response' ressemble à un CTA, on NE TOUCHE PAS aux checkboxes.
    """
    print("run: apply_ai_response")
    
    page = driver

    # 0) Si ça ressemble à un CTA, on laisse les stratégies bouton gérer.
    if looks_like_nav_label(response):
        # On tente juste du texte (rare) puis bouton; jamais checkbox
        try:
            input_fields = page.query_selector_all("input[type='text'], textarea")
            for field in input_fields:
                try:
                    field.fill(response)
                    time.sleep(1)
                    print(
                        f"✓ Réponse texte insérée (CTA-like ignoré côté checkbox) : {response}"
                    )
                    return True
                except:
                    continue
        except Exception as e:
            print(f"❌ Erreur saisie texte (CTA-like) : {e} source: input_handler.py")

        # Bouton par texte (au cas où)
        try:
            if click_button_by_text(driver, response):
                return True
        except Exception as e:
            print(f"❌ Erreur clic bouton (CTA-like) : {e} source: input_handler.py")

        # Ne pas toucher aux checkboxes ici
        return False

    # 1. Essayer comme champ texte
    try:
        input_fields = page.query_selector_all("input[type='text'], textarea")
        for field in input_fields:
            try:
                field.fill(response)
                time.sleep(1)
                print(f"✓ Réponse texte insérée : {response}")
                return True
            except:
                continue
    except Exception as e:
        print(f"❌ Erreur saisie texte : {e} source: input_handler.py")

    # 2. Essayer comme bouton ou élément cliquable
    try:
        if handle_generic_input(driver, response):
            return True
    except Exception as e:
        print(f"❌ Erreur generic_input : {e}: source: input_handler.py")

    try:
        if click_button_by_text(driver, response):
            return True
    except Exception as e:
        print(f"❌ Erreur clic bouton : {e} source: input_handler.py")

    # 3. Essayer comme checkbox (CTA déjà filtré au début)
    try:
        if click_checkbox_by_label(driver, response):
            return True
    except Exception as e:
        print(f"❌ Erreur clic checkbox : {e} source: input_handler.py")

    print(
        f"❌ Aucune méthode n'a fonctionné pour : {response} source: input_handler.py"
    )
    return False


def _click_next_any(driver):
    """
    Clique le bouton de navigation après sélection.
    Supporte data-test-id, <button> textuels et <input type=submit>.
    """
    page = driver
    deadline = time.time() + 5

    # a) selectors spécifiques (quand dispo)
    try:
        btn = page.query_selector('button[data-test-id="ps-common-actions-button"]')
        if btn and btn.is_visible() and btn.is_enabled():
            page.evaluate("(el) => el.scrollIntoView({block:'center'})", btn)
            time.sleep(0.2)
            page.evaluate("(el) => el.click()", btn)
            print("✅️ Bouton (data-test-id) cliqué.")
            return True
    except Exception:
        pass

    # b) libellés communs — polling jusqu'à deadline
    while time.time() < deadline:
        try:
            btn = page.query_selector(
                "xpath=//button[contains(., 'Suivant') or contains(., 'Continuer') or contains(., 'Next') or contains(., 'Continue')]"
            )
            if btn and btn.is_visible() and btn.is_enabled():
                page.evaluate("(el) => el.scrollIntoView({block:'center'})", btn)
                time.sleep(0.2)
                page.evaluate("(el) => el.click()", btn)
                print("✅️ Bouton navigation cliqué (texte).")
                return True
        except Exception:
            pass
        time.sleep(0.2)

    # c) submit
    try:
        sub = page.query_selector("input[type='submit']")
        if sub:
            page.evaluate("(el) => el.click()", sub)
            print("✅️ Submit cliqué.")
            return True
    except Exception:
        pass

    return False


# =============================================================================
# HELPERS ADDITIONNELS (gardés ici pour rétrocompatibilité)
# =============================================================================

def _find_best_label_text(el):
    """
    Récupère un texte pertinent pour le label (prend le inner_text, sinon plus long des spans descendants).
    """
    h = el
    try:
        txt = h.inner_text().strip()
    except Exception:
        txt = (h.get_attribute("innerText") or "").strip()
    if txt:
        return txt
    try:
        spans = h.query_selector_all("xpath=.//span[normalize-space(string())!='']")
        if spans:
            scored = []
            for s in spans:
                try:
                    t = s.inner_text().strip()
                    scored.append((len(t), t))
                except Exception:
                    pass
            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                return scored[0][1]
    except Exception:
        pass
    return ""


def _looks_checked(el, linked_input):
    """
    Heuristique succès : input sélectionné OU classe aria/ui passée à 'on/checked'.
    """
    try:
        if linked_input and linked_input.is_selected():
            return True
    except Exception:
        pass
    try:
        cls = (el.get_attribute("class") or "").lower()
        aria = (el.get_attribute("aria-pressed") or el.get_attribute("aria-checked") or "").lower()
        if "ui-checkbox-on" in cls or aria in ("true", "mixed"):
            return True
    except Exception:
        pass
    return False


def _find_linked_input_for_label(driver, label_el):
    """
    Tente de retrouver l'input[type=checkbox] correspondant au label :
    - via l'attribut 'for'
    - sinon via un sibling/descendant
    """
    page = driver
    el = label_el
    linked = None
    # 1) via for/id
    try:
        for_attr = el.get_attribute("for")
        if for_attr:
            linked = page.query_selector(f"#{for_attr}")
            if linked:
                t = (linked.get_attribute("type") or "").lower()
                if t != "checkbox":
                    linked = None
    except Exception:
        linked = None

    # 2) fallback : descendant/suivant
    if linked is None:
        try:
            linked = el.query_selector("xpath=.//input[@type='checkbox']")
        except Exception:
            pass
    if linked is None:
        try:
            linked = el.query_selector("xpath=following::input[@type='checkbox'][1]")
        except Exception:
            linked = None
    return linked


def _is_visible(driver, el):
    """Vérifie si un élément est visible avec une taille minimum."""
    try:
        h = el
        if not h.is_visible():
            return False
        box = h.bounding_box()
        return box is not None and box.get("width", 0) > 5 and box.get("height", 0) > 5
    except Exception:
        return False


# =============================================================================
# EXPORTS EXPLICITES
# =============================================================================

__all__ = [
    # Constantes
    "DROPDOWN_PLACEHOLDERS",
    "PLACEHOLDER_TOKENS",
    "CTA_SYNONYMS",
    "DATE_HINTS",
    "MATRIX_COL_SYNONYMS",
    "DEBUG_PAUSE",
    
    # Normalisation
    "norm_txt", "norms_txt", "normt_txt", "norm", "norm_text", "norm_hint",
    "norm_soft", "norm_lc_soft", "normalize_lbl", "norm_btn_text",
    "strip_accents", "xpath_literal",
    
    # Helpers DOM
    "pause_here", "scroll_into_view", "js_click", "safe_click", "is_checked",
    "looks_like_nav_label", "set_input_value_with_events", "find_inputs_by_hint",
    
    # Contexte/scoping
    "find_question_container_by_ctx", "find_questions_container", "find_context_container",
    
    # Open-ended
    "has_visible_open_ended_field", "ensure_open_ended_open", "split_typed_instruction",
    
    # Viewport
    "viewport_penalty", "similarity",
    
    # Frames
    "iter_iframes_safe", "in_each_frame_recursive",
    
    # Dropdowns
    "has_native_selects", "select_like_elements", "element_signature_text",
    "best_dropdown_for_hint", "dropdown_visible_value", "is_dropdown_filled",
    "open_first_dropdown", "open_dropdown_generic",
    "select_option_with_hint", "select_option_with_hint",
    "select_native_option_by_target",
    
    # Text
    "type_via_cdp", "react_set_value_and_fire", "is_numeric_field",
    "swagbucks_zip_patch", "fill_text_input", "fill_native_date_input",
    "fill_ifop_zip2city_widget", "fill_text_input_by_id_in_frame",
    
    # Radio
    "click_decipher_grid_radio", "click_decipher_grid_radio_strict",
    "click_radio_label_in_scope", "fallback_click_radio_js_generic",
    "click_radio_by_label",
    
    # Checkbox
    "force_checkbox_events", "privacy_checkbox_is_accepted",
    "force_label_for_checkbox_js", "fallback_click_checkbox_js_alchemer",
    "fallback_click_checkbox_js_generic", "click_checkbox_buttonish_by_label",
    "click_confirmit_checktable", "click_checkbox_by_label",
    
    # Slider
    "set_sliderpoints",
    
    # Matrix
    "looks_like_matrix", "iter_matrix_rows", "get_matrix_columns",
    "select_cell_action", "click_matrix_cell_by_row_and_col",
    "apply_matrix_column_to_all_rows",
    
    # CTA
    "click_button_by_text", "click_icon_like_button", "click_primary_cta",
    "try_click_navigation_cta",
    "click_button_by_text_any_context", "click_icon_like_button_any_context",
    "click_primary_cta_any_context", "try_click_navigation_cta_any_context",
    "click_cta_strong_any_context",
    
    # Orchestration
    "handle_generic_input", "apply_ai_response",
]