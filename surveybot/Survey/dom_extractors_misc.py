# Survey/dom_extractors_misc.py
"""
DOM Extractors - Plateformes diverses

Ce module contient les extracteurs pour les plateformes:
- Angular Material (radio groups)
- WALR (cardsort)
- AskAndAnswer (mobile matrix, selection list)
- CMIX (simple grid, radio questions)
- CloudResearch/Sentry (Vue.js components)

Ces extracteurs utilisent des patterns DOM spécifiques à chaque plateforme.
"""

from __future__ import annotations
from typing import List, Dict, Any, Set, Tuple
import os, re, time, zlib
from selenium.webdriver.common.by import By
from Survey.log_utils import log_debug, log_info, is_debug

# Import des utilitaires
try:
    from Survey.dom_utils import _norm_lc, _xpath_literal, _best_xpath_for_element, _norm, _norm_key, _looks_like_system_field
    from Survey.dom_question_extractor import _find_question_text_near_element, _compute_max_select
    from Survey.dom_registry import register_target, make_target_id
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _norm_lc, _xpath_literal, _best_xpath_for_element, _norm, _norm_key, _looks_like_system_field
    from Survey.dom_question_extractor import _find_question_text_near_element, _compute_max_select
    from Survey.dom_registry import register_target, make_target_id
    # dom_registry devra être disponible


# ================================================================================
# ANGULAR MATERIAL - RADIO GROUPS
# ================================================================================

def _extract_angular_material_radio_groups(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extraction Angular Material radio groups (mat-radio-group / mat-radio-button).
    
    Concerne: sites modernes Angular Material (ex: innovatemr, EdgeSurvey).
    Structure typique:
      <mat-radio-group name="radioOptField" ...>
        <mat-radio-button id="mat-radio-X" ...>
          <input type="radio" class="mdc-radio__native-control" value="1" ...>
          <label class="mdc-label" for="mat-radio-X-input"> 1 </label>
        </mat-radio-button>
        ...
      </mat-radio-group>
    """
    blocks: list[dict] = []
    
    try:
        radio_groups = driver.find_elements(By.CSS_SELECTOR, "mat-radio-group")
    except Exception:
        return blocks
    
    for group in radio_groups:
        try:
            # Pattern spécifique
            name = (group.get_attribute("name") or "").strip()
            if not name:
                continue
            
            # Trouver tous les mat-radio-button visibles
            buttons = group.find_elements(By.CSS_SELECTOR, "mat-radio-button")
            if len(buttons) < 2:
                continue
            
            # Extraire les options et input_ids
            options: list[str] = []
            input_ids: list[str] = []
            
            for btn in buttons:
                try:
                    # Pattern spécifique
                    try:
                        if not btn.is_displayed():
                            continue
                        r = btn.rect or {}
                        if r.get("width", 0) <= 2 or r.get("height", 0) <= 2:
                            continue
                    except Exception:
                        continue
                    
                    # Pattern spécifique
                    label_txt = ""
                    
                    # 1) label.mdc-label (Angular Material standard)
                    try:
                        label_el = btn.find_element(By.CSS_SELECTOR, "label.mdc-label")
                        label_txt = (label_el.text or label_el.get_attribute("innerText") or "").strip()
                    except Exception:
                        pass
                    
                    # 2) Fallback: texte complet du bouton
                    if not label_txt:
                        label_txt = (btn.text or btn.get_attribute("innerText") or "").strip()
                    
                    if not label_txt:
                        continue
                    
                    # Trouver l'input radio sous-jacent
                    try:
                        inp = btn.find_element(By.CSS_SELECTOR, "input[type='radio']")
                        inp_id = (inp.get_attribute("id") or "").strip()
                        if not inp_id:
                            continue
                        
                        options.append(label_txt)
                        input_ids.append(inp_id)
                    except Exception:
                        continue
                        
                except Exception:
                    continue
            
            # Pattern spécifique
            if len(options) < 2 or len(input_ids) < 2:
                continue
            
            # Extraire la question (chercher h5, h3, mat-card-title, etc.)
            question = ""
            try:
                # Conteneur parent (souvent un form ou div.survey-window)
                container = None
                try:
                    container = group.find_element(By.XPATH, "ancestor::form[1]")
                except Exception:
                    try:
                        container = group.find_element(By.XPATH, "ancestor::div[contains(@class,'survey')][1]")
                    except Exception:
                        container = group.find_element(By.XPATH, "ancestor::div[1]")
                
                if container:
                    # Chercher le texte de question (h5, h3, mat-card-title, etc.)
                    for sel in ["h5.question-text", "h3.question-text", "h5", "h3", "mat-card-title", "div.question-text"]:
                        try:
                            q_el = container.find_element(By.CSS_SELECTOR, sel)
                            question = (q_el.text or q_el.get_attribute("innerText") or "").strip()
                            if question:
                                break
                        except Exception:
                            continue
            except Exception:
                pass
            
            question = _norm(question)
            if not question:
                # Fallback: utiliser le name comme question
                question = f"Question {name}"
            
            # Construire option_xpath_map (on clique sur le mat-radio-button ou label)
            option_xpath_map: dict[str, str] = {}
            clean_options: list[str] = []
            
            for opt_txt, inp_id in zip(options, input_ids):
                if not opt_txt or not inp_id:
                    continue
                k = _norm_lc(opt_txt)
                if not k or k in option_xpath_map:
                    continue
                
                # Input masqué
                # Pattern spécifique
                xpath_click = (
                    f"//input[@id={_xpath_literal(inp_id)}]"
                    f"/ancestor::mat-radio-button[1]"
                )
                option_xpath_map[k] = xpath_click
                clean_options.append(opt_txt)
            
            if len(clean_options) < 2:
                continue
            
            # Pattern spécifique
            itype = "radio"
            
            # Pattern spécifique
            group_key = f"{itype}:name:{name}"
            target_id = make_target_id("group", group_key, question)
            
            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": itype,
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": list(frame_chain or []),
                },
            )
            
            blocks.append(
                {
                    "question": question,
                    "itype": itype,
                    "options": clean_options,
                    "max_select": _compute_max_select(itype, clean_options),
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )
            
        except Exception as e:
            if os.getenv("RUN_ENV", "local") == "local":
                print(f"[DOM_ANALYZER][WARN] angular_material extract: {type(e).__name__}: {e}")
            continue
    
    return blocks



# ================================================================================
# WALR - CARDSORT
# ================================================================================

def _extract_walr_cardsort_block(driver, frame_chain: list[int] | None) -> dict | None:
    """
    Walr CardSort: UI avec boutons simples (pas de radios natifs).
    
    Pattern DOM:
        <div id="cardSortContainer">
            <div class="statement-box">Question text</div>
            <div class="button-container">
                <button class="answer-button">Option 1</button>
                <button class="answer-button">Option 2</button>
                ...
            </div>
        </div>
    
    Retourne un bloc radio avec XPath vers les boutons pour clic direct.
    """
    frame_chain = list(frame_chain or [])
    
    try:
        container = driver.find_elements(By.CSS_SELECTOR, "#cardSortContainer")
    except Exception:
        container = []
    
    if not container:
        log_debug("[WALR_CS]", "Pas de #cardSortContainer")
        return None
    
    container = container[0]
    
    # Pattern spécifique
    try:
        displayed = container.is_displayed()
        log_debug("[WALR_CS]", f"is_displayed={displayed}")
        if not displayed:
            # Pattern spécifique
            pass
    except Exception as e:
        log_debug("[WALR_CS]", f"is_displayed exception: {e}")
    
    # Extraire la question depuis .statement-box
    question = ""
    try:
        stmt = container.find_elements(By.CSS_SELECTOR, ".statement-box")
        log_debug("[WALR_CS]", f".statement-box count={len(stmt)}")
        if stmt:
            raw_text = stmt[0].text
            inner_text = stmt[0].get_attribute("innerText")
            text_content = stmt[0].get_attribute("textContent")
            log_debug("[WALR_CS]", f".text='{raw_text}' innerText='{inner_text}' textContent='{text_content}'")
            question = _norm(raw_text or inner_text or text_content or "")
    except Exception as e:
        log_debug("[WALR_CS]", f"statement-box exception: {e}")
    
    # Pattern spécifique
    main_title = ""
    try:
        # Chercher le titre dans div.cQuestionText ou div.rs-ht strong
        title_selectors = [
            "div.cQuestionText p strong",
            "div.rs-ht.cReg p strong", 
            "div.rs-ht p strong",
            "div.cQuestionText",
        ]
        for sel in title_selectors:
            titles = driver.find_elements(By.CSS_SELECTOR, sel)
            for t in titles:
                txt = _norm(t.text or t.get_attribute("innerText") or "")
                if txt and len(txt) > 5 and txt != question:
                    main_title = txt
                    log_debug("[WALR_CS]", f"main_title trouvé via '{sel}': '{main_title}'")
                    break
            if main_title:
                break
    except Exception as e:
        log_debug("[WALR_CS]", f"main_title exception (non bloquant): {e}")
    
    # Combiner titre + statement si disponible
    if main_title and question:
        question = f"{main_title} {question}"
        log_debug("[WALR_CS]", f"question combinée: '{question}'")

    log_debug("[WALR_CS]", f"question finale='{question}'")
    
    if not question:
        log_debug("[WALR_CS]", "ABANDON - question vide")
        return None
    
    # Extraire les options depuis button.answer-button
    try:
        buttons = container.find_elements(By.CSS_SELECTOR, "button.answer-button")
    except Exception:
        buttons = []
    
    log_debug("[WALR_CS]", f"buttons count={len(buttons)}")
    
    if len(buttons) < 2:
        log_debug("[WALR_CS]", "ABANDON - moins de 2 boutons")
        return None
    
    options = []
    option_xpath_map = {}
    
    for idx, btn in enumerate(buttons):
        try:
            raw = btn.text
            inner = btn.get_attribute("innerText")
            txt = _norm(raw or inner or "")
            log_debug("[WALR_CS]", f"btn[{idx}] raw='{raw}' inner='{inner}' norm='{txt}'")
            if not txt:
                continue
            
            # XPath pour clic direct sur le bouton
            # On utilise l'index 1-based pour XPath
            xpath = f"//*[@id='cardSortContainer']//button[contains(@class,'answer-button')][{idx + 1}]"
            
            options.append(txt)
            option_xpath_map[txt] = xpath
        except Exception as e:
            log_debug("[WALR_CS]", f"btn[{idx}] exception: {e}")
            continue

    log_debug("[WALR_CS]", f"options finales={len(options)}")
    
    if len(options) < 2:
        log_debug("[WALR_CS]", "ABANDON - moins de 2 options extraites")
        return None

    log_info("[WALR_CS]", f"detected=true options={len(options)}")
    
    # Pattern spécifique
    group_key = f"walr_cardsort:{question[:30]}"
    log_debug("[WALR_CS]", f"group_key='{group_key}'")
    
    # Pattern spécifique
    target_id = make_target_id("walr_cardsort", group_key, question)
    log_debug("[WALR_CS]", f"target_id={target_id}")
    
    # Enregistrer dans le registry
    log_debug("[WALR_CS]", "Appel register_target...")
    try:
        register_target(target_id, {
            "kind": "walr_cardsort",
            "group_key": group_key,
            "question": question,
            "option_xpath_map": option_xpath_map,
            "frame_chain": frame_chain,
            "walr_cardsort": True,
        })
        log_debug("[WALR_CS]", "register_target OK")
    except Exception as e:
        log_debug("[WALR_CS]", f"register_target EXCEPTION: {type(e).__name__}: {e}")
        if is_debug():
            import traceback
            traceback.print_exc()
        raise
    
    result = {
        "question": question,
        "itype": "radio",
        "options": options,
        "max_select": 1,
        "target_id": target_id,
        "context": {"kind": "group", "group_key": group_key, "walr_cardsort": True},
    }
    log_debug("[WALR_CS]", f"Returning result: itype={result['itype']}, options={len(result['options'])}")
    return result


# =============================================================================
# DISABLED: _extract_walr_image_eval_block
# Reason: Image evaluation questions require Vision API which is not supported 
#         in V1 prod. These surveys are now abandoned via survey_difficulty_guard
#         with reason="image_evaluation".
# Date: 2026-02-10
# =============================================================================
# def _extract_walr_image_eval_block(driver, frame_chain: list[int] | None) -> dict | None:
#     """
#     Walr Image Evaluation - DISABLED
#     Handled by survey_difficulty_guard.detect_strict_survey() -> "image_evaluation"
#     """
#     return None




# ================================================================================
# ASKANDANSWER - MOBILE MATRIX ROWS
# ================================================================================

def _extract_askandanswer_mobile_matrix_rows(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Ask&Answer / FirstInsight (Angular Material) : matrices en mode *mobile*
    rendues comme une liste de <mat-expansion-panel class="mobile-matrix-question">.

    ProblÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨me : les <input type=radio> des panels repliÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©s ne sont pas "visibles" (height=0, visibility:hidden)
    => notre extraction gÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©nÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©rique (qui filtre sur visibilitÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©) ne sort que la/les lignes dÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©jÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â  ouvertes.

    StratÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©gie DOM-only, prÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©dictible:
    - dÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©tecter les panels mobile-matrix-question
    - crÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©er 1 bloc radio par ligne (header = libellÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â© de la ligne)
    - options = textes des labels dans le panel
    - registry: option_xpath_map pointe sur label[for=inputId] DANS le panel
      + pre_click_xpaths pour ouvrir le panel avant de cliquer l'option
    """
    frame_chain = list(frame_chain or [])

    try:
        panels = driver.find_elements(By.CSS_SELECTOR, "mat-expansion-panel.mobile-matrix-question")
    except Exception:
        panels = []

    if not panels:
        return []

    # Question globale (titre de la carte)
    global_q = ""
    try:
        titles = driver.find_elements(By.CSS_SELECTOR, "mat-card-title div")
        if titles:
            global_q = _norm(titles[0].text or titles[0].get_attribute("innerText") or "")
    except Exception:
        global_q = ""

    blocks: list[dict] = []

    # Pattern spécifique
    try:
        max_rows = int(os.getenv("AA_MATRIX_MAX_ROWS", "40") or "40")
        if max_rows <= 0:
            max_rows = 40
    except Exception:
        max_rows = 40

    def _open_panel_if_needed(panel) -> None:
        """
        Angular Material: le contenu (radios) peut ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Âªtre rendu via *ngIf uniquement quand le panel est ouvert.
        On ouvre le panel (1 fois) puis on attend briÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨vement que les radios apparaissent.
        """
        try:
            hdr = panel.find_element(By.CSS_SELECTOR, "mat-expansion-panel-header")
        except Exception:
            return

        try:
            if (hdr.get_attribute("aria-expanded") or "").strip().lower() == "true":
                return
        except Exception:
            pass

        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", hdr)
        except Exception:
            pass
        time.sleep(0.05)

        pre_count = 0
        try:
            pre_count = len(panel.find_elements(By.CSS_SELECTOR, "mat-radio-button"))
        except Exception:
            pre_count = 0

        try:
            hdr.click()
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", hdr)
            except Exception:
                return

        t0 = time.time()
        while time.time() - t0 < 1.2:
            # Pattern spécifique
            try:
                if (hdr.get_attribute("aria-expanded") or "").strip().lower() == "true":
                    break
            except Exception:
                pass

            # 2) fallback: si le contenu est lazy-rendered, attendre l'apparition des radios.
            if pre_count == 0:
                try:
                    if panel.find_elements(By.CSS_SELECTOR, "mat-radio-button"):
                        break
                except Exception:
                    pass

            time.sleep(0.05)

    for panel in panels[:max_rows]:
        try:
            panel_id = (panel.get_attribute("id") or "").strip()
            if not panel_id:
                panel_id = f"panel_{zlib.adler32((panel.get_attribute('outerHTML') or '').encode('utf-8'))}"

            # Pattern spécifique
            row_label = ""
            try:
                htxt = panel.find_elements(By.CSS_SELECTOR, "mat-expansion-panel-header .matrix-text-color")
                if htxt:
                    row_label = _norm(htxt[0].text or htxt[0].get_attribute("innerText") or "")
            except Exception:
                row_label = ""

            if not row_label:
                # Pattern spécifique
                try:
                    hdrs = panel.find_elements(By.CSS_SELECTOR, "mat-expansion-panel-header")
                    if hdrs:
                        raw = hdrs[0].text or hdrs[0].get_attribute("innerText") or ""
                        raw = (raw.splitlines()[0] if raw else "")
                        row_label = _norm(raw)
                except Exception:
                    row_label = ""

            if not row_label:
                continue

            # options (dans le panel body)
            options: list[str] = []

            def _collect_opt_nodes():
                try:
                    return panel.find_elements(By.CSS_SELECTOR, "mat-radio-button .mat-radio-label-content")
                except Exception:
                    return []

            def _read_options(nodes) -> list[str]:
                opts: list[str] = []
                for n in nodes:
                    try:
                        t = _norm(n.text or n.get_attribute("innerText") or "")
                        if t and t not in opts:
                            opts.append(t)
                    except Exception:
                        continue
                return opts

            opt_nodes = _collect_opt_nodes()
            options = _read_options(opt_nodes)

            # Pattern spécifique
            # On force une ouverture (1 fois) puis on relit.
            if not options:
                _open_panel_if_needed(panel)
                opt_nodes = _collect_opt_nodes()
                options = _read_options(opt_nodes)

            if not options:
                continue

            # registry: map option -> xpath (label[for=inputId]) scoped au panel
            def _build_option_xpath_map() -> dict[str, str]:
                m: dict[str, str] = {}
                try:
                    rbs = panel.find_elements(By.CSS_SELECTOR, "mat-radio-button")
                except Exception:
                    rbs = []

                # Pattern spécifique
                for rb in rbs:
                    try:
                        lab_txt = ""
                        try:
                            lc = rb.find_elements(By.CSS_SELECTOR, ".mat-radio-label-content")
                            if lc:
                                lab_txt = _norm(lc[0].text or lc[0].get_attribute("innerText") or "")
                        except Exception:
                            lab_txt = ""

                        if not lab_txt:
                            try:
                                lab_txt = _norm(rb.text or rb.get_attribute("innerText") or "")
                            except Exception:
                                lab_txt = ""

                        if not lab_txt:
                            continue

                        pid = _xpath_literal(panel_id)
                        # Si on peut, on construit un XPath sur @value (stable)
                        val = ""
                        try:
                            inp = rb.find_element(By.CSS_SELECTOR, "input.mat-radio-input")
                            val = (inp.get_attribute("value") or "").strip()
                        except Exception:
                            val = ""

                        if val:
                            vlit = _xpath_literal(val)
                            xp = (
                                f"(//mat-expansion-panel[@id={pid}]"
                                f"//mat-radio-button[.//input[@type='radio' and @value={vlit}]]"
                                f"//label[contains(@class,'mat-radio-label')])[1]"
                            )
                        else:
                            # Fallback texte (si @value indisponible)
                            lit = _xpath_literal(lab_txt)
                            xp = (
                                f"(//mat-expansion-panel[@id={pid}]"
                                f"//mat-radio-button[.//*[contains(@class,'mat-radio-label-content') and normalize-space(.)={lit}]]"
                                f"//label[contains(@class,'mat-radio-label')])[1]"
                            )
                        m[_norm_key(lab_txt)] = xp
                    except Exception:
                        continue

                # 2) fallback: certains DOM (ex: mat-table) n'ont pas le texte dans chaque radio.
                # Pattern spécifique
                if not m and options:
                    try:
                        pid = _xpath_literal(panel_id)
                        for i, opt in enumerate(options):
                            if not opt:
                                continue
                            xp = (
                                f"(//mat-expansion-panel[@id={pid}]//*[contains(@class,'mat-expansion-panel-body')]//mat-radio-button)[{i+1}]"
                                f"//label[contains(@class,'mat-radio-label')][1]"
                            )
                            m[_norm_key(opt)] = xp
                    except Exception:
                        pass

                return m

            option_xpath_map = _build_option_xpath_map()
            if not option_xpath_map:
                _open_panel_if_needed(panel)
                option_xpath_map = _build_option_xpath_map()

            if not option_xpath_map:
                continue

            group_key = f"aa_mobile_matrix_row:{panel_id}"
            question = f"{global_q} ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â¦ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â {row_label}" if global_q else row_label
            target_id = make_target_id("group", group_key, question)

            # Pattern spécifique
            pre_click_xpaths = []
            try:
                pid = _xpath_literal(panel_id)
                pre_click_xpaths = [f"(//mat-expansion-panel[@id={pid}]//mat-expansion-panel-header)[1]"]
            except Exception:
                pre_click_xpaths = []

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "pre_click_xpaths": pre_click_xpaths,
                    "frame_chain": frame_chain,
                    "aa_mobile_matrix": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key, "aa_mobile_matrix": True},
                }
            )
        except Exception:
            continue

    return blocks



# ================================================================================
# ASKANDANSWER - SELECTION LIST QUESTIONS
# ================================================================================

def _extract_askandanswer_selection_list_questions(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Ask&Answer / FirstInsight (Angular Material) : questions rendues via <mat-selection-list>.

    ProblÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨me:
    - les options ne sont pas des <input type=checkbox>, donc l'extraction gÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©nÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©rique (radios/checkbox) ne voit rien.
    - le seul <input> prÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©sent est souvent l'option "Autre (veuillez prÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©ciser)" => on extrait une fausse question.

    StratÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©gie DOM-only, stricte et non-invasive:
    - ne s'active que si on dÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©tecte un <app-survey-page> ET des <mat-selection-list> sous appQuestionContainer
    - retourne 1 bloc par selection-list:
        - question = mat-card-title
        - options = texte des mat-list-option (fallback mat-label pour l'option Autre)
        - option_xpath_map = XPath stable sur l'id de chaque mat-list-option (answer-*-*)

    Objectif:
    - corriger ce provider sans impacter les cas canoniques non-Angular.
    """
    frame_chain = list(frame_chain or [])

    # Gate strict : pages Ask&Answer (Angular) uniquement
    try:
        if not driver.find_elements(By.CSS_SELECTOR, "app-survey-page"):
            return []
    except Exception:
        return []

    try:
        lists = driver.find_elements(
            By.CSS_SELECTOR,
            "div[id^='appQuestionContainer-'] mat-selection-list[role='listbox']",
        )
    except Exception:
        lists = []

    if not lists:
        return []

    blocks: list[dict] = []
    processed_container_ids: set[str] = set()

    # Pattern spécifique
    try:
        max_lists = int(os.getenv("AA_SELECTION_LIST_MAX", "10") or "10")
        if max_lists <= 0:
            max_lists = 10
    except Exception:
        max_lists = 10

    for sl in lists[:max_lists]:
        try:
            # options candidates
            try:
                opt_els = sl.find_elements(By.CSS_SELECTOR, "mat-list-option[role='option']")
            except Exception:
                opt_els = []

            # ignore templates/vides
            if len(opt_els) < 2:
                continue

            # remonter au conteneur de question
            q_container = None
            try:
                q_container = sl.find_element(
                    By.XPATH,
                    "ancestor::div[starts-with(@id,'appQuestionContainer-')][1]",
                )
            except Exception:
                q_container = None

            # texte de question
            question = ""
            try:
                scope = q_container or sl
                titles = scope.find_elements(By.CSS_SELECTOR, "mat-card-title div")
                if titles:
                    question = _norm(titles[0].text or titles[0].get_attribute("innerText") or "")
            except Exception:
                question = ""

            if not question:
                continue

            # Pattern spécifique
            itype = "checkbox"
            try:
                am = (sl.get_attribute("aria-multiselectable") or "").strip().lower()
                if am in {"false", "0", "no"}:
                    itype = "radio"
            except Exception:
                pass

            # options + mapping option->xpath
            options: list[str] = []
            option_xpath_map: dict[str, str] = {}

            for opt in opt_els:
                try:
                    label = _norm(opt.text or opt.get_attribute("innerText") or "")
                    # nettoie les multi-lignes (l'option "Autre" peut inclure du bruit)
                    if label:
                        label = _norm(label.splitlines()[0])

                    # Pattern spécifique
                    if not label:
                        try:
                            labs = opt.find_elements(By.CSS_SELECTOR, "mat-label")
                            if labs:
                                label = _norm(labs[0].text or labs[0].get_attribute("innerText") or "")
                        except Exception:
                            label = ""

                    if not label:
                        continue

                    # xpath stable : l'id answer-*-* est unique et cliquable
                    xp = ""
                    try:
                        oid = (opt.get_attribute("id") or "").strip()
                        if oid:
                            xp = f"(//*[@id={_xpath_literal(oid)}])[1]"
                        else:
                            xp = _best_xpath_for_element(driver, opt)
                    except Exception:
                        xp = ""

                    if not xp:
                        continue

                    nk = _norm_key(label)
                    if nk in option_xpath_map:
                        continue

                    option_xpath_map[nk] = xp
                    options.append(label)
                except Exception:
                    continue

            if len(options) < 2 or not option_xpath_map:
                continue

            sl_id = ""
            cont_id = ""
            try:
                sl_id = (sl.get_attribute("id") or "").strip()
            except Exception:
                sl_id = ""
            try:
                cont_id = (q_container.get_attribute("id") or "").strip() if q_container else ""
            except Exception:
                cont_id = ""

            if cont_id:
                processed_container_ids.add(cont_id)

            group_key = f"aa_selection_list:{cont_id}:{sl_id}".strip(":")
            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": itype,
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                    "aa_selection_list": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": itype,
                    "options": options,
                    "max_select": _compute_max_select(itype, options),
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key, "aa_selection_list": True},
                }
            )

        except Exception:
            continue

    # Même survey Angular Material: certaines questions sont rendues en mat-radio-group
    # (data-question-type=PULLDOWN) sous le même pattern appQuestionContainer-*.
    try:
        rg_containers = driver.find_elements(
            By.CSS_SELECTOR,
            "div[id^='appQuestionContainer-'] mat-radio-group[role='radiogroup']",
        )
    except Exception:
        rg_containers = []

    for rg in rg_containers[:20]:
        try:
            q_container = None
            try:
                q_container = rg.find_element(
                    By.XPATH,
                    "ancestor::div[starts-with(@id,'appQuestionContainer-')][1]",
                )
            except Exception:
                q_container = None

            cont_id = ""
            try:
                cont_id = (q_container.get_attribute("id") or "").strip() if q_container else ""
            except Exception:
                cont_id = ""

            if cont_id and cont_id in processed_container_ids:
                continue

            question = ""
            try:
                scope = q_container or rg
                titles = scope.find_elements(By.CSS_SELECTOR, "mat-card-title div")
                if titles:
                    question = _norm(titles[0].text or titles[0].get_attribute("innerText") or "")
            except Exception:
                question = ""

            if not question:
                continue

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}
            try:
                rb_els = rg.find_elements(By.CSS_SELECTOR, "mat-radio-button")
            except Exception:
                rb_els = []

            for rb in rb_els:
                try:
                    label = _norm(rb.text or rb.get_attribute("innerText") or "")
                    if label:
                        label = _norm(label.splitlines()[-1])
                    if not label:
                        continue

                    xp = ""
                    try:
                        rid = (rb.get_attribute("id") or "").strip()
                        if rid:
                            xp = f"(//*[@id={_xpath_literal(rid)}])[1]"
                        else:
                            xp = _best_xpath_for_element(driver, rb)
                    except Exception:
                        xp = ""

                    if not xp:
                        continue

                    nk = _norm_key(label)
                    if nk in option_xpath_map:
                        continue

                    option_xpath_map[nk] = xp
                    options.append(label)
                except Exception:
                    continue

            if len(options) < 2 or not option_xpath_map:
                continue

            rg_id = ""
            try:
                rg_id = (rg.get_attribute("id") or "").strip()
            except Exception:
                rg_id = ""

            group_key = f"aa_selection_list:{cont_id}:{rg_id}".strip(":")
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
                    "aa_selection_list": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key, "aa_selection_list": True},
                }
            )

            if cont_id:
                processed_container_ids.add(cont_id)
        except Exception:
            continue

    return blocks


# ================================================================================
# REACT-NATIVE-WEB - IONICON MULTI-CHOICE
# ================================================================================

def _extract_rnw_ionicon_multi_choice_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction DOM-only des listes choix custom React-Native-Web (sans input natif).

    Pattern ciblé (strict, sans logique provider):
    - options rendues par des wrappers `div[tabindex="0"]` ayant les classes `r-rnv2vh` et `r-rs99b7`
    - chaque option contient une icône ionicons (`font-family: ionicons`)
    - une question visible est trouvable près du groupe

    Retourne un bloc group checkbox/radio selon l'indice de multisélection.
    """
    frame_chain = list(frame_chain or [])

    try:
        option_nodes = driver.find_elements(By.CSS_SELECTOR, "div[tabindex='0'].r-rnv2vh.r-rs99b7")
    except Exception:
        return []

    if len(option_nodes) < 3:
        return []

    blocks: list[dict] = []
    seen_group_keys: set[str] = set()

    for opt in option_nodes[:80]:
        try:
            container = opt.find_element(By.XPATH, "ancestor::div[.//div[@tabindex='0' and contains(@class,'r-rnv2vh') and contains(@class,'r-rs99b7')]][1]")
        except Exception:
            container = None

        if not container:
            continue

        try:
            rows = container.find_elements(By.CSS_SELECTOR, "div[tabindex='0'].r-rnv2vh.r-rs99b7")
        except Exception:
            rows = []

        if len(rows) < 3:
            continue

        option_xpath_map: dict[str, str] = {}
        options: list[str] = []

        for row in rows[:50]:
            try:
                # garde-fou DOM: ce pattern doit afficher une icône ionicons par option
                if not row.find_elements(By.XPATH, ".//*[contains(translate(@style, 'IONICS', 'ionics'),'font-family: ionicons')]"):
                    continue
            except Exception:
                continue

            label = ""
            try:
                txt_nodes = row.find_elements(By.XPATH, ".//div[normalize-space()] | .//span[normalize-space()]")
            except Exception:
                txt_nodes = []

            for node in txt_nodes:
                try:
                    candidate = _norm(node.text or node.get_attribute("innerText") or "")
                except Exception:
                    candidate = ""
                if not candidate:
                    continue
                if "ionicons" in _norm_lc(node.get_attribute("style") or ""):
                    continue
                if len(candidate) <= 1:
                    continue
                label = candidate
                break

            if not label:
                continue

            try:
                xp = _best_xpath_for_element(driver, row)
            except Exception:
                xp = ""

            if not xp:
                continue

            nk = _norm_key(label)
            if nk in option_xpath_map:
                continue

            option_xpath_map[nk] = xp
            options.append(label)

        if len(options) < 3 or len(option_xpath_map) < 3:
            continue

        question = ""
        try:
            q_nodes = container.find_elements(By.XPATH, ".//div[contains(normalize-space(), '?')]")
        except Exception:
            q_nodes = []

        for qn in q_nodes:
            qtxt = _norm(qn.text or qn.get_attribute("innerText") or "")
            if qtxt and len(qtxt) >= 12:
                question = qtxt
                break

        if not question:
            try:
                question = _norm(_find_question_text_near_element(driver, rows[0]) or "")
            except Exception:
                question = ""

        if not question:
            continue

        hint_text = ""
        try:
            hint_nodes = container.find_elements(By.XPATH, ".//div[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'réponses possibles') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'multiple') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'plusieurs')]")
            if hint_nodes:
                hint_text = _norm(hint_nodes[0].text or hint_nodes[0].get_attribute("innerText") or "")
        except Exception:
            hint_text = ""

        is_multi = bool(hint_text)
        itype = "checkbox" if is_multi else "radio"

        group_key = f"rnw_ionicon:{itype}:{_norm_key(question[:120])}:{len(options)}"
        if group_key in seen_group_keys:
            continue

        target_id = make_target_id("group", group_key, question)
        register_target(
            target_id,
            {
                "kind": "group",
                "itype": itype,
                "group_key": group_key,
                "question": question,
                "option_xpath_map": option_xpath_map,
                "frame_chain": frame_chain,
                "rnw_ionicon": True,
            },
        )

        blocks.append(
            {
                "question": question,
                "itype": itype,
                "options": options,
                "max_select": _compute_max_select(itype, options),
                "target_id": target_id,
                "context": {"kind": "group", "group_key": group_key, "rnw_ionicon": True},
            }
        )
        seen_group_keys.add(group_key)

    return blocks


# ================================================================================
# GENERIC TABLE MATRIX (RADIO PER ROW)
# ================================================================================

def _extract_table_matrix_radio_rows(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extraction DOM-only pour matrices HTML classiques:
    - table avec thead (colonnes)
    - tbody avec lignes
    - radios groupés par ligne (name identique dans une ligne, différent entre lignes)

    Retourne 1 question_block radio par ligne (stable pour le pipeline existant),
    avec métadonnées de matrice (matrix_question / matrix_row / matrix_columns).
    """
    frame_chain = list(frame_chain or [])

    try:
        tables = driver.find_elements(By.CSS_SELECTOR, "table")
    except Exception:
        return []

    blocks: list[dict] = []

    for table in tables[:20]:  # budget anti-explosion
        try:
            table_cls = _norm_lc(table.get_attribute("class") or "")
            if "cm-simple-grid__table" in table_cls:
                # Déjà géré par _extract_cmix_simple_grid_question_blocks
                continue

            col_headers: list[str] = []
            try:
                ths = table.find_elements(By.CSS_SELECTOR, "thead tr th")
            except Exception:
                ths = []

            if len(ths) < 3:
                continue

            for th in ths:
                txt = _norm(th.text or th.get_attribute("innerText") or "")
                if txt:
                    col_headers.append(txt)

            if len(col_headers) < 2:
                continue

            try:
                rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
            except Exception:
                rows = []

            if len(rows) < 2:
                continue

            row_candidates: list[dict[str, Any]] = []
            row_names_seen: set[str] = set()

            for row in rows[:30]:
                try:
                    row_cells = row.find_elements(By.CSS_SELECTOR, "td, th")
                    if len(row_cells) < 2:
                        continue

                    row_label = _norm(row_cells[0].text or row_cells[0].get_attribute("innerText") or "")
                    if not row_label:
                        continue

                    radios = row.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                    if len(radios) < 2:
                        continue

                    row_name = _norm_lc(radios[0].get_attribute("name") or "")
                    if not row_name:
                        continue

                    # Tous les radios de la ligne doivent partager le même name.
                    same_name = True
                    for radio in radios:
                        if _norm_lc(radio.get_attribute("name") or "") != row_name:
                            same_name = False
                            break
                    if not same_name:
                        continue

                    row_names_seen.add(row_name)
                    row_candidates.append({
                        "row": row,
                        "row_label": row_label,
                        "row_name": row_name,
                        "radios": radios,
                    })
                except Exception:
                    continue

            # Vrai pattern matrice: au moins 2 lignes distinctes de radios groupés.
            # Exception DOM-scopée: Alchemer/SurveyGizmo peut afficher une matrice
            # radio mono-ligne (même structure de grille + en-têtes de colonnes),
            # avec des names `sge-<id>-<qid>-<rowid>`.
            sge_like_row_names = [
                c["row_name"]
                for c in row_candidates
                if re.match(r"^sge-\d+-\d+-\d+$", c.get("row_name", ""))
            ]
            sge_like_matrix = (
                bool(row_candidates)
                and len(sge_like_row_names) == len(row_candidates)
                and len(set(sge_like_row_names)) == len(sge_like_row_names)
            )

            if (len(row_candidates) < 2 or len(row_names_seen) < 2) and not sge_like_matrix:
                continue

            # Aligner les en-têtes sur le vrai nombre de colonnes de réponse observé
            # dans le DOM de la matrice. Certaines pages ont un th supplémentaire
            # (libellé de ligne), d'autres non.
            max_radio_count = max((len(c.get("radios") or []) for c in row_candidates), default=0)
            if max_radio_count >= 2 and len(col_headers) > max_radio_count:
                col_headers = col_headers[-max_radio_count:]

            if len(col_headers) < 2:
                continue

            matrix_question = _norm(_find_question_text_near_element(driver, table))
            if not matrix_question:
                matrix_question = _norm(table.get_attribute("aria-label") or "")

            if sge_like_matrix:
                first = row_candidates[0]
                row_count = len(row_candidates)
                matrix_rows = [r["row_label"] for r in row_candidates]
                matrix_key = re.sub(r"-\d+$", "", first["row_name"])
                group_key = f"table_matrix_sge:name:{matrix_key}"
                target_id = make_target_id("group", group_key, matrix_question or matrix_key)

                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "itype": "matrix",
                        "group_key": group_key,
                        "question": matrix_question,
                        "frame_chain": frame_chain,
                        "matrix_question": matrix_question,
                        "matrix_rows": matrix_rows,
                        "matrix_columns": col_headers,
                        "table_matrix_radio": True,
                        "table_matrix_sge": True,
                    },
                )

                blocks.append(
                    {
                        "question": matrix_question,
                        "itype": "matrix",
                        "options": col_headers,
                        "max_select": 1,
                        "target_id": target_id,
                        "context": {
                            "kind": "group",
                            "group_key": group_key,
                            "matrix_question": matrix_question,
                            "matrix_rows": matrix_rows,
                            "matrix_columns": col_headers,
                            "table_matrix_radio": True,
                            "table_matrix_sge": True,
                            "matrix_row_count": row_count,
                        },
                    }
                )
                continue

            for row_data in row_candidates:
                try:
                    row_label = row_data["row_label"]
                    row_name = row_data["row_name"]
                    radios = row_data["radios"]

                    option_xpath_map: dict[str, str] = {}
                    options: list[str] = []

                    num_options = min(len(col_headers), len(radios))
                    if num_options < 2:
                        continue

                    for idx in range(num_options):
                        opt_text = col_headers[idx]
                        radio = radios[idx]

                        radio_id = (radio.get_attribute("id") or "").strip()
                        if radio_id:
                            xp = f"(//*[@id={_xpath_literal(radio_id)}])[1]"
                        else:
                            radio_value = (radio.get_attribute("value") or "").strip()
                            if not radio_value:
                                continue
                            xp = (
                                f"(//input[@type='radio' and @name={_xpath_literal(row_name)} "
                                f"and @value={_xpath_literal(radio_value)}])[1]"
                            )

                        nk = _norm_key(opt_text)
                        if not nk or nk in option_xpath_map:
                            continue
                        option_xpath_map[nk] = xp
                        options.append(opt_text)

                    if len(options) < 2 or not option_xpath_map:
                        continue

                    question = f"{matrix_question} | {row_label}" if matrix_question else row_label
                    group_key = f"table_matrix_radio:name:{row_name}"
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
                            "matrix_question": matrix_question,
                            "matrix_row": row_label,
                            "matrix_columns": col_headers,
                            "table_matrix_radio": True,
                        },
                    )

                    blocks.append(
                        {
                            "question": question,
                            "itype": "radio",
                            "options": options,
                            "max_select": 1,
                            "target_id": target_id,
                            "context": {
                                "kind": "group",
                                "group_key": group_key,
                                "matrix_question": matrix_question,
                                "matrix_row": row_label,
                                "matrix_columns": col_headers,
                                "table_matrix_radio": True,
                            },
                        }
                    )
                except Exception:
                    continue
        except Exception:
            continue

    return blocks


def _extract_intellisurvey_table_matrix_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """IntelliSurvey: matrice table.i-question-table.i-dynamic (lignes x colonnes).

    Gate DOM strict (additif, sans impact autres providers):
    - table.i-question-table.i-dynamic
    - thead avec td.i-header-option (colonnes)
    - tbody avec >=2 tr[data-row-id], chaque ligne ayant des radios .i-rbcb-opt
    """
    frame_chain = list(frame_chain or [])

    try:
        tables = driver.find_elements(By.CSS_SELECTOR, "table.i-question-table.i-dynamic")
    except Exception:
        return []

    blocks: list[dict] = []

    for table in tables[:10]:  # budget anti-boucle
        try:
            try:
                col_nodes = table.find_elements(By.CSS_SELECTOR, "thead td.i-header-option")
            except Exception:
                col_nodes = []

            col_headers: list[str] = []
            for node in col_nodes:
                txt = _norm(node.text or node.get_attribute("innerText") or "")
                if txt:
                    col_headers.append(txt)

            if len(col_headers) < 2:
                continue

            try:
                rows = table.find_elements(By.CSS_SELECTOR, "tbody tr[data-row-id]")
            except Exception:
                rows = []

            if len(rows) < 2:
                continue

            matrix_rows: list[str] = []
            row_names: list[str] = []
            for row in rows:
                try:
                    radios = row.find_elements(By.CSS_SELECTOR, "input.i-rbcb-opt[type='radio'][name]")
                except Exception:
                    radios = []
                if len(radios) < 2:
                    continue

                row_label = ""
                try:
                    qcell = row.find_element(By.CSS_SELECTOR, "td.i-questext")
                    row_label = _norm(driver.execute_script(
                        """
                        const td = arguments[0];
                        if (!td) return '';
                        const clone = td.cloneNode(true);
                        clone.querySelectorAll('input[type="hidden"], script, style').forEach(n => n.remove());
                        return (clone.innerText || clone.textContent || '').trim();
                        """,
                        qcell,
                    ) or "")
                except Exception:
                    row_label = ""

                row_name = _norm_lc(radios[0].get_attribute("name") or "")
                if not row_label or not row_name:
                    continue

                matrix_rows.append(row_label)
                row_names.append(row_name)

            if len(matrix_rows) < 2:
                continue

            matrix_question = _norm(_find_question_text_near_element(driver, table))
            if not matrix_question:
                matrix_question = _norm(table.get_attribute("aria-label") or "")

            group_key = f"intellisurvey_matrix:{_norm_key((table.get_attribute('id') or '')[:80])}:{len(matrix_rows)}x{len(col_headers)}"
            target_id = make_target_id("group", group_key, matrix_question or group_key)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "matrix",
                    "group_key": group_key,
                    "question": matrix_question,
                    "frame_chain": frame_chain,
                    "matrix_question": matrix_question,
                    "matrix_rows": matrix_rows,
                    "matrix_columns": col_headers,
                    # Réutilise la stratégie matrix row/col existante (DOM strict par table + radios)
                    "table_matrix_sge": True,
                    "intellisurvey_matrix": True,
                },
            )

            blocks.append(
                {
                    "question": matrix_question,
                    "itype": "matrix",
                    "options": col_headers,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "matrix_question": matrix_question,
                        "matrix_rows": matrix_rows,
                        "matrix_columns": col_headers,
                        "matrix_row_count": len(matrix_rows),
                        "table_matrix_sge": True,
                        "intellisurvey_matrix": True,
                    },
                }
            )
        except Exception:
            continue

    return blocks


def _extract_encuesta_matrix_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """encuesta.com (Vuetify ee__matrix--*) : extraction d'une matrice rows x columns.

    Gate DOM strict (additif, non provider-wide):
    - .ee__matrix--row
    - .ee__matrix--first-column
    - .ee__matrix--header-cells

    Structure ciblée:
    - ligne d'entêtes: `.ee__matrix--row.hidden-sm-and-down`
    - lignes de réponses: `.ee__matrix--row:not(.hidden-sm-and-down)`
    - libellé ligne: `.ee__matrix--first-column`
    - colonnes: `.ee__matrix--column input[type=radio]`
    """
    frame_chain = list(frame_chain or [])

    try:
        rows = driver.find_elements(By.CSS_SELECTOR, ".ee__matrix--row")
        first_cols = driver.find_elements(By.CSS_SELECTOR, ".ee__matrix--first-column")
        header_cells = driver.find_elements(By.CSS_SELECTOR, ".ee__matrix--header-cells")
    except Exception:
        return []

    if not rows or not first_cols or not header_cells:
        return []

    matrix_row_containers: list[Any] = []
    try:
        matrix_row_containers = driver.find_elements(By.CSS_SELECTOR, ".layout.ee__matrix--row")
    except Exception:
        matrix_row_containers = []

    if not matrix_row_containers:
        return []

    col_headers: list[str] = []
    for row in matrix_row_containers:
        try:
            cls = _norm_lc(row.get_attribute("class") or "")
            if "hidden-sm-and-down" not in cls:
                continue
            for cell in row.find_elements(By.CSS_SELECTOR, ".ee__matrix--header-cells"):
                txt = _norm(cell.text or cell.get_attribute("innerText") or "")
                if txt:
                    col_headers.append(txt)
            if col_headers:
                break
        except Exception:
            continue

    if len(col_headers) < 2:
        return []

    matrix_rows: list[str] = []
    row_xpath_map: dict[str, str] = {}

    for row in matrix_row_containers:
        try:
            cls = _norm_lc(row.get_attribute("class") or "")
            if "hidden-sm-and-down" in cls:
                continue

            first_col_nodes = row.find_elements(By.CSS_SELECTOR, ".ee__matrix--first-column")
            if not first_col_nodes:
                continue

            row_label = _norm(first_col_nodes[0].text or first_col_nodes[0].get_attribute("innerText") or "")
            if not row_label:
                continue

            row_columns = row.find_elements(By.CSS_SELECTOR, ".ee__matrix--column")
            if len(row_columns) < len(col_headers):
                continue

            radio_count = 0
            for col in row_columns[: len(col_headers)]:
                radios = col.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                if radios:
                    radio_count += 1
            if radio_count < len(col_headers):
                continue

            row_xpath = _best_xpath_for_element(driver, row)
            if not row_xpath:
                continue

            row_key = _norm_key(row_label)
            if not row_key or row_key in row_xpath_map:
                continue

            matrix_rows.append(row_label)
            row_xpath_map[row_key] = row_xpath
        except Exception:
            continue

    if len(matrix_rows) < 2:
        return []

    question = ""
    try:
        q_nodes = driver.find_elements(By.CSS_SELECTOR, ".ee__question_title")
        for q in q_nodes:
            qtxt = _norm(q.text or q.get_attribute("innerText") or "")
            if qtxt:
                question = qtxt
                break
    except Exception:
        question = ""

    if not question:
        try:
            question = _norm(_find_question_text_near_element(driver, matrix_row_containers[0]))
        except Exception:
            question = ""

    if not question:
        return []

    group_key = f"encuesta_matrix:{_norm_key(question[:120])}:{len(matrix_rows)}x{len(col_headers)}"
    target_id = make_target_id("group", group_key, question)

    register_target(
        target_id,
        {
            "kind": "group",
            "itype": "matrix",
            "group_key": group_key,
            "question": question,
            "frame_chain": frame_chain,
            "matrix_question": question,
            "matrix_rows": matrix_rows,
            "matrix_columns": col_headers,
            "matrix_row_xpath_map": row_xpath_map,
            "encuesta_matrix": True,
        },
    )

    return [
        {
            "question": question,
            "itype": "matrix",
            "options": matrix_rows,
            "max_select": 1,
            "target_id": target_id,
            "context": {
                "kind": "group",
                "group_key": group_key,
                "matrix_question": question,
                "matrix_rows": matrix_rows,
                "matrix_columns": col_headers,
                "encuesta_matrix": True,
            },
        }
    ]


def _extract_yougov_grid_text_question_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """YouGov grid-text: 1 bloc `text` par ligne de grille (input texte libre).

    Gate DOM strict (additif, non provider-wide):
    - fieldset.question-grid.question-grid-text
    - legend.question-text (question parent)
    - lignes contenant `th.grid-item-text-left` + `td.grid-cell.open-cell input[type=text]`

    Résultat attendu:
    - question = legend + " — " + libellé de ligne
    - itype = text
    - options = []
    - target_id unique par input (name/id/xpath)
    """
    frame_chain = list(frame_chain or [])

    try:
        fieldsets = driver.find_elements(
            By.CSS_SELECTOR,
            "fieldset.question.question-grid.question-grid-text",
        )
    except Exception:
        return []

    if not fieldsets:
        return []

    blocks: list[dict] = []

    for fs in fieldsets[:8]:  # budget anti-explosion
        try:
            legend = ""
            try:
                legend_el = fs.find_element(By.CSS_SELECTOR, "legend.question-text")
                legend = _norm(legend_el.text or legend_el.get_attribute("innerText") or "")
            except Exception:
                legend = ""

            if not legend:
                continue

            try:
                rows = fs.find_elements(By.CSS_SELECTOR, "tbody tr")
            except Exception:
                rows = []

            for row in rows[:40]:  # budget anti-explosion
                try:
                    inputs = row.find_elements(
                        By.CSS_SELECTOR,
                        "td.grid-cell.open-cell input[type='text']",
                    )
                except Exception:
                    inputs = []

                if not inputs:
                    continue

                input_el = inputs[0]

                row_label = ""
                try:
                    row_label_el = row.find_element(By.CSS_SELECTOR, "th.grid-item-text-left")
                    row_label = _norm(row_label_el.text or row_label_el.get_attribute("innerText") or "")
                except Exception:
                    row_label = ""

                question = legend if not row_label else _norm(f"{legend} — {row_label}")
                if not question:
                    continue

                name = (input_el.get_attribute("name") or "").strip()
                el_id = (input_el.get_attribute("id") or "").strip()
                aria_role = (input_el.get_attribute("role") or "").strip() or None
                xpath = _best_xpath_for_element(driver, input_el)

                uniq = name or el_id or xpath
                if not uniq:
                    continue

                target_id = make_target_id("single", f"yougov_grid_text:{uniq}", question)

                register_target(
                    target_id,
                    {
                        "kind": "single",
                        "tag": "input",
                        "name": name,
                        "id": el_id,
                        "role": aria_role,
                        "xpath": xpath,
                        "frame_chain": frame_chain,
                        "yougov_grid_text": True,
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
                            "tag": "input",
                            "name": name,
                            "id": el_id,
                            "role": aria_role,
                            "yougov_grid_text": True,
                        },
                        "min_select": 1,
                    }
                )
        except Exception:
            continue

    return blocks



# ================================================================================
# CMIX - SIMPLE GRID QUESTION BLOCKS
# ================================================================================

def _extract_cmix_simple_grid_question_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """CMIX SIMPLE_GRID : extraction des matrices simples (table.cm-simple-grid__table).

    Structure DOM attendue:
    - thead > th.cm-simple-grid__column-header = options (colonnes)
    - tbody > tr > td.cm-simple-grid__row-header = question (ligne)
    - tbody > tr > td > input[type=radio] = radios groupés par name

    Chaque ligne (tr) génère un question_block avec:
    - question = texte du row-header
    - options = textes des column-headers
    - mapping option -> xpath du radio correspondant (via position)
    """
    frame_chain = list(frame_chain or [])

    # Gate strict: table CMIX SIMPLE_GRID
    try:
        tables = driver.find_elements(By.CSS_SELECTOR, "table.cm-simple-grid__table")
        if not tables:
            return []
    except Exception:
        return []

    blocks: list[dict] = []

    for table in tables[:10]:  # Limite anti-explosion
        try:
            # 1) Extraire les headers de colonnes (= options communes à toutes les lignes)
            col_headers = []
            try:
                ths = table.find_elements(By.CSS_SELECTOR, "thead th.cm-simple-grid__column-header")
                for th in ths:
                    txt = _norm(th.text or th.get_attribute("innerText") or "")
                    if txt:
                        col_headers.append(txt)
            except Exception:
                pass

            if len(col_headers) < 2:
                # Pas assez d'options, skip cette table
                continue

            # 2) Parcourir chaque ligne du tbody
            try:
                rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
            except Exception:
                rows = []

            for row in rows[:25]:  # Limite anti-explosion
                try:
                    subquestion_name = (row.get_attribute("data-subquestionname") or "").strip()
                    has_other_specify_input = False
                    try:
                        has_other_specify_input = bool(
                            row.find_elements(By.CSS_SELECTOR, "input[type='text'].cm-other-specify")
                        )
                    except Exception:
                        has_other_specify_input = False

                    # 2a) Extraire le texte de la question (row-header)
                    question = ""
                    try:
                        row_hdr = row.find_element(By.CSS_SELECTOR, "td.cm-simple-grid__row-header")
                        question = _norm(row_hdr.text or row_hdr.get_attribute("innerText") or "")
                    except Exception:
                        pass

                    if not question:
                        continue

                    # 2b) Extraire les radios de cette ligne
                    try:
                        radios = row.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                    except Exception:
                        radios = []

                    if len(radios) < 2:
                        continue

                    # Vérifier que le nombre de radios correspond aux headers
                    # (tolérance: on prend le min des deux)
                    num_options = min(len(col_headers), len(radios))
                    if num_options < 2:
                        continue

                    # 2c) Construire le mapping option -> xpath
                    options = []
                    option_xpath_map = {}
                    radio_name = None

                    for idx in range(num_options):
                        try:
                            radio = radios[idx]
                            opt_text = col_headers[idx]

                            # Récupérer le name du groupe (tous les radios ont le même)
                            if radio_name is None:
                                radio_name = radio.get_attribute("name") or ""

                            # Construire le XPath vers ce radio via son value
                            val = radio.get_attribute("value") or ""
                            if not val:
                                continue

                            name_lit = _xpath_literal(radio_name)
                            val_lit = _xpath_literal(val)
                            xp = f"(//input[@type='radio' and @name={name_lit} and @value={val_lit}])[1]"

                            nk = _norm_key(opt_text)
                            if nk in option_xpath_map:
                                continue

                            option_xpath_map[nk] = xp
                            options.append(opt_text)
                        except Exception:
                            continue

                    if len(options) < 2 or not option_xpath_map:
                        continue

                    # 2d) Créer le question_block
                    group_key = f"cmix_simple_grid:name:{radio_name}"
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
                            "cmix": True,
                            "cmix_simple_grid": True,
                            "subquestion_name": subquestion_name,
                            "has_other_specify_input": has_other_specify_input,
                        },
                    )

                    blocks.append(
                        {
                            "question": question,
                            "itype": "radio",
                            "options": options,
                            "max_select": 1,
                            "target_id": target_id,
                            "context": {
                                "kind": "group",
                                "group_key": group_key,
                                "cmix": True,
                                "cmix_simple_grid": True,
                                "subquestion_name": subquestion_name,
                                "has_other_specify_input": has_other_specify_input,
                            },
                        }
                    )

                except Exception:
                    continue

        except Exception:
            continue

    return blocks


def _extract_cmix_grid_question_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """CMIX GRID : extraction des matrices table.cm-grid-response-set.

    Gate DOM strict:
    - div.cm-element[data-type='GRID']
    - table.cm-grid-response-set

    Chaque ligne (.cm-grid-row) produit un bloc radio:
    - question = libellé de ligne (cm-grid-column-header-1)
    - options = libellés de colonnes (header Oui/Non/...)
    - target_id = groupe radio de la ligne (name partagé)
    """
    frame_chain = list(frame_chain or [])

    try:
        tables = driver.find_elements(
            By.CSS_SELECTOR,
            "div.cm-element[data-type='GRID'] table.cm-grid-response-set",
        )
        if not tables:
            return []
    except Exception:
        return []

    blocks: list[dict] = []

    def _is_data_option_header_cell(cell) -> bool:
        """CMIX GRID: colonnes options = classes cm-grid-column-N (N>=1), hors colonne header."""
        try:
            cls = (cell.get_attribute("class") or "").strip()
        except Exception:
            cls = ""
        if not cls:
            return False
        if "cm-grid-column-header" in cls:
            return False
        return bool(re.search(r"(?:^|\s)cm-grid-column-\d+(?:\s|$)", cls))

    for table in tables[:10]:  # Limite anti-explosion
        try:
            parent_q = ""
            try:
                container = table.find_element(By.XPATH, "ancestor::div[contains(@class,'cm-element')][1]")
                parent_q = _norm(container.find_element(By.CSS_SELECTOR, "div.cm-qtext").text or "")
            except Exception:
                parent_q = ""

            col_headers: list[str] = []
            try:
                header_cells = table.find_elements(By.CSS_SELECTOR, "tr.cm-grid-row-header td, tr.cm-grid-row-header th")
                if not header_cells:
                    # Variante DOM CMIX: première ligne = entêtes colonnes, sans classes header dédiées.
                    header_cells = table.find_elements(By.CSS_SELECTOR, "tr:first-child td, tr:first-child th")
                for cell in header_cells:
                    if not _is_data_option_header_cell(cell):
                        continue
                    txt = _norm(cell.text or cell.get_attribute("innerText") or "")
                    if txt:
                        col_headers.append(txt)
            except Exception:
                pass

            if len(col_headers) < 2:
                continue

            try:
                rows = table.find_elements(By.CSS_SELECTOR, "tr[data-response-batch]")
                if not rows:
                    rows = table.find_elements(By.CSS_SELECTOR, "tr.cm-grid-row")
            except Exception:
                rows = []

            for row in rows[:30]:  # Limite anti-explosion
                try:
                    row_label = ""
                    try:
                        row_hdr = row.find_element(
                            By.CSS_SELECTOR,
                            "td.cm-grid-column-header-1, th.cm-grid-column-header-1, "
                            "td.cm-grid-column-header, th.cm-grid-column-header",
                        )
                        row_label = _norm(row_hdr.text or row_hdr.get_attribute("innerText") or "")
                    except Exception:
                        pass

                    if not row_label:
                        continue

                    try:
                        radios = row.find_elements(By.CSS_SELECTOR, "input[type='radio'][name][value]")
                    except Exception:
                        radios = []

                    if len(radios) < 2:
                        continue

                    num_options = min(len(col_headers), len(radios))
                    if num_options < 2:
                        continue

                    options: list[str] = []
                    option_xpath_map: dict[str, str] = {}
                    radio_name = ""

                    for idx in range(num_options):
                        radio = radios[idx]
                        opt_text = col_headers[idx]
                        if not radio_name:
                            radio_name = (radio.get_attribute("name") or "").strip()

                        val = (radio.get_attribute("value") or "").strip()
                        if not radio_name or not val:
                            continue

                        xp = (
                            f"(//input[@type='radio' and @name={_xpath_literal(radio_name)} "
                            f"and @value={_xpath_literal(val)}])[1]"
                        )

                        nk = _norm_key(opt_text)
                        if not nk or nk in option_xpath_map:
                            continue
                        option_xpath_map[nk] = xp
                        options.append(opt_text)

                    if len(options) < 2 or not option_xpath_map:
                        continue

                    question = f"{parent_q} : {row_label}" if parent_q else row_label

                    group_key = f"cmix_grid:name:{radio_name}"
                    target_id = make_target_id("group", group_key, row_label)

                    register_target(
                        target_id,
                        {
                            "kind": "group",
                            "itype": "radio",
                            "group_key": group_key,
                            "question": question,
                            "option_xpath_map": option_xpath_map,
                            "frame_chain": frame_chain,
                            "cmix": True,
                            "cmix_grid": True,
                            "matrix_columns": col_headers,
                        },
                    )

                    blocks.append(
                        {
                            "question": question,
                            "itype": "radio",
                            "options": options,
                            "max_select": 1,
                            "target_id": target_id,
                            "context": {
                                "kind": "group",
                                "group_key": group_key,
                                "cmix": True,
                                "cmix_grid": True,
                                "matrix_columns": col_headers,
                            },
                        }
                    )
                except Exception:
                    continue

        except Exception:
            continue

    return blocks



# ================================================================================
# CMIX - RADIO QUESTION BLOCKS
# ================================================================================

def _extract_cmix_radio_question_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """CMIX (survey.cmix.com) : extraction DOM-only des questions radio + numeric.

    Bug visé (capture CMIX): la page affiche des radios (ex: politique de confidentialité)
    mais l'extraction générique peut retourner 0 question_blocks, déclenchant le fallback
    CTA-only et sautant la question.

    Stratégique d'extraction ciblée:
    - activation stricte uniquement si le markup CMIX (.cm-question-wrapper + .cm-radio-label)
    - 1 bloc par groupe radio (name) dans un wrapper
    - mapping option->xpath en privilégiant le label texte (.cm-radio-label) plutot que le label "bouton" (.cm-radio-input)
    - extraction des questions numériques CMIX (`.cm-element[data-type='NUMERIC']` + `input[type='number']`)
    """

    frame_chain = list(frame_chain or [])

    # Gate strict: CMIX wrappers avec labels radio/checkbox
    try:
        if not driver.find_elements(By.CSS_SELECTOR, ".cm-question-wrapper .cm-radio-label, .cm-question-wrapper .cm-checkbox-label"):
            return []
    except Exception:
        return []

    try:
        wrappers = driver.find_elements(By.CSS_SELECTOR, ".cm-question-wrapper")
    except Exception:
        wrappers = []

    if not wrappers:
        return []

    blocks: list[dict] = []

    for w in wrappers[:25]:
        try:
            # Pattern spécifique
            try:
                if not w.is_displayed():
                    continue
            except Exception:
                pass

            # question text (CMIX)
            question = ""
            try:
                qels = w.find_elements(By.CSS_SELECTOR, ".cm-question-text, .cm-qtext")
                if qels:
                    question = _norm(qels[0].text or qels[0].get_attribute("innerText") or "")
            except Exception:
                question = ""

            if not question:
                # Pattern spécifique
                raw = _norm(w.text or w.get_attribute("innerText") or "")
                if raw:
                    question = _norm(raw.splitlines()[0])

            if not question:
                continue

            # inputs radio/checkbox dans le wrapper
            try:
                inputs = w.find_elements(By.CSS_SELECTOR, "input[type='radio'][id][name], input[type='checkbox'][id][name]")
            except Exception:
                inputs = []

            # group par (type, name)
            by_group: dict[tuple[str, str], list[Any]] = {}
            for r in inputs:
                try:
                    if _looks_like_system_field(r):
                        continue
                except Exception:
                    pass

                # Input masqué
                try:
                    rtype = (r.get_attribute("type") or "").strip().lower()
                    rid = (r.get_attribute("id") or "").strip()
                    rname = (r.get_attribute("name") or "").strip()
                    if rtype not in {"radio", "checkbox"} or not rid or not rname:
                        continue
                    label_sel = "cm-radio-label" if rtype == "radio" else "cm-checkbox-label"
                    # label texte (pas le label "cercle")
                    lbls = w.find_elements(By.CSS_SELECTOR, f"label.{label_sel}[for='{rid}']")
                    if not lbls:
                        continue
                    t = _norm(lbls[0].text or lbls[0].get_attribute("innerText") or "")
                    if not t or len(t) < 2:
                        continue
                    by_group.setdefault((rtype, rname), []).append(r)
                except Exception:
                    continue

            for (rtype, rname), els in by_group.items():
                if rtype == "radio" and len(els) < 2:
                    continue

                options: list[str] = []
                option_xpath_map: dict[str, str] = {}

                for r in els:
                    try:
                        label_sel = "cm-radio-label" if rtype == "radio" else "cm-checkbox-label"
                        rid = (r.get_attribute("id") or "").strip()
                        if not rid:
                            continue
                        lbls = w.find_elements(By.CSS_SELECTOR, f"label.{label_sel}[for='{rid}']")
                        if not lbls:
                            continue
                        label = _norm(lbls[0].text or lbls[0].get_attribute("innerText") or "")
                        if not label:
                            continue

                        # Pattern spécifique
                        rid_lit = _xpath_literal(rid)
                        xp = (
                            f"(//label[contains(concat(' ',normalize-space(@class),' '),' {label_sel} ') and @for={rid_lit}])[1]"                        )

                        nk = _norm_key(label)
                        if nk in option_xpath_map:
                            continue

                        option_xpath_map[nk] = xp
                        options.append(label)
                    except Exception:
                        continue

                if not options or not option_xpath_map:
                    continue

                group_key = f"cmix_{rtype}:name:{rname}"
                target_id = make_target_id("group", group_key, question)

                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "itype": "rtype",
                        "group_key": group_key,
                        "question": question,
                        "option_xpath_map": option_xpath_map,
                        "frame_chain": frame_chain,
                        "cmix": True,
                    },
                )

                blocks.append(
                    {
                        "question": question,
                        "itype": rtype,
                        "options": options,
                        "max_select": _compute_max_select(rtype, options),
                        "target_id": target_id,
                        "context": {"kind": "group", "group_key": group_key, "cmix": True},
                    }
                )

            # CMIX NUMERIC : 1 bloc single par input[type=number] visible dans un wrapper NUMERIC.
            try:
                numeric_root = w.find_elements(By.CSS_SELECTOR, ".cm-element[data-type='NUMERIC']")
            except Exception:
                numeric_root = []

            if numeric_root:
                try:
                    numeric_inputs = w.find_elements(By.CSS_SELECTOR, ".cm-element[data-type='NUMERIC'] input[type='number']")
                except Exception:
                    numeric_inputs = []

                for inp in numeric_inputs:
                    try:
                        if _looks_like_system_field(inp):
                            continue
                    except Exception:
                        pass

                    try:
                        if not inp.is_displayed():
                            continue
                    except Exception:
                        pass

                    try:
                        el_id = (inp.get_attribute("id") or "").strip()
                        el_name = (inp.get_attribute("name") or "").strip()
                        if not el_id and not el_name:
                            continue

                        xpath = _best_xpath_for_element(driver, inp)
                        alt_xpaths: list[str] = []
                        if el_name:
                            alt_xpaths.append(f"//input[@name={_xpath_literal(el_name)}]")
                        if el_id:
                            alt_xpaths.append(f"//*[@id={_xpath_literal(el_id)}]")
                        alt_xpaths = [x for x in dict.fromkeys(alt_xpaths) if x and x != xpath][:4]

                        single_key = f"text:{el_id}:{el_name}"
                        target_id = make_target_id("single", single_key, question)

                        register_target(
                            target_id,
                            {
                                "kind": "single",
                                "itype": "text",
                                "question": question,
                                "xpath": xpath,
                                "alt_xpaths": alt_xpaths,
                                "tag": "input",
                                "name": el_name,
                                "id": el_id,
                                "frame_chain": frame_chain,
                                "cmix": True,
                                "cmix_numeric": True,
                            },
                        )

                        blocks.append(
                            {
                                "question": question,
                                "itype": "text",
                                "options": [],
                                "max_select": _compute_max_select("text", []),
                                "target_id": target_id,
                                "context": {
                                    "kind": "single",
                                    "tag": "input",
                                    "name": el_name,
                                    "id": el_id,
                                    "cmix": True,
                                    "cmix_numeric": True,
                                },
                            }
                        )
                    except Exception:
                        continue

        except Exception:
            continue

    return blocks


def _extract_single_consent_checkbox_block(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction ciblée d'un écran consentement checkbox+CTA.

    Scope volontairement minimal:
    - un seul input checkbox dans un conteneur de consentement explicite
      (id/class contenant consent/privacy-policy)
    - présence d'un CTA d'acceptation "accept/start" (id ou texte)

    Objectif: produire un question_block exploitable (1 option) même si
    l'extraction générique échoue sur certains écrans Wicket dynamiques.
    """

    frame_chain = list(frame_chain or [])

    cb = None

    # Pattern historique (Wicket consentContainer) conservé tel quel.
    try:
        explicit_consent_checkboxes = driver.find_elements(
            By.CSS_SELECTOR,
            "#consentContainer25 input[type='checkbox'], "
            "[id*='consentContainer'] input[type='checkbox'], "
            ".river-sampling-privacy-policy input[type='checkbox'], "
            "input[type='checkbox'][id*='consentCheckbox'], "
            "input[type='checkbox'][name*='consentCheckbox'], "
            "input[type='checkbox'][name*='consentContainer']",
        )
    except Exception:
        explicit_consent_checkboxes = []

    if len(explicit_consent_checkboxes) == 1:
        cb = explicit_consent_checkboxes[0]

    # Pattern DOM minimal et additif:
    # - bouton CTA disabled dans un form
    # - un checkbox unique dans ce même form
    # - pas de structure radio/groupe classique autour
    if cb is None:
        try:
            disabled_ctas = driver.find_elements(
                By.CSS_SELECTOR,
                "form button[disabled], "
                "form input[type='submit'][disabled], "
                "form input[type='button'][disabled]",
            )
        except Exception:
            disabled_ctas = []

        for cta in disabled_ctas:
            try:
                form = cta.find_element(By.XPATH, "ancestor::form[1]")
            except Exception:
                continue

            try:
                radios_in_form = form.find_elements(By.CSS_SELECTOR, "input[type='radio']")
            except Exception:
                radios_in_form = []
            if radios_in_form:
                continue

            try:
                form_checkboxes = form.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
            except Exception:
                form_checkboxes = []
            if len(form_checkboxes) != 1:
                continue

            cb = form_checkboxes[0]
            break

    if cb is None:
        return []

    try:
        ctas = driver.find_elements(
            By.CSS_SELECTOR,
            "a[id*='acceptAndTakeSurveyLink'], button[id*='acceptAndTakeSurveyLink'], "
            "a.btn-primary, button.btn-primary",
        )
    except Exception:
        ctas = []

    has_accept_cta = False
    for cta in ctas:
        try:
            txt = _norm_lc(cta.text or cta.get_attribute("innerText") or "")
            cta_id = _norm_lc(cta.get_attribute("id") or "")
            if (
                "acceptandtakesurveylink" in cta_id
                or "accepter et commencer" in txt
                or "accept and start" in txt
                or "accept and begin" in txt
            ):
                has_accept_cta = True
                break
        except Exception:
            continue

    if not has_accept_cta:
        try:
            ctas = driver.find_elements(
                By.CSS_SELECTOR,
                "form button[disabled], form input[type='submit'][disabled], form input[type='button'][disabled]",
            )
        except Exception:
            ctas = []

        has_accept_cta = len(ctas) > 0

    if not has_accept_cta:
        return []

    try:
        cb_id = (cb.get_attribute("id") or "").strip()
        cb_name = (cb.get_attribute("name") or "").strip()
    except Exception:
        cb_id = ""
        cb_name = ""

    label_txt = ""
    if cb_id:
        try:
            lbl = driver.find_element(By.CSS_SELECTOR, f"label[for='{cb_id}']")
            label_txt = _norm(lbl.text or lbl.get_attribute("innerText") or "")
        except Exception:
            label_txt = ""

    if not label_txt:
        try:
            parent_labels = cb.find_elements(By.XPATH, "ancestor::label[1]")
            if parent_labels:
                label_txt = _norm(parent_labels[0].text or parent_labels[0].get_attribute("innerText") or "")
        except Exception:
            label_txt = ""

    if not label_txt:
        return []

    question = ""
    try:
        container = cb.find_element(By.XPATH, "ancestor::*[@id='consentContainer25' or contains(@id,'consentContainer') or contains(@class,'privacy-policy')][1]")
        raw = _norm(container.text or container.get_attribute("innerText") or "")
        if raw:
            question = "Politique de confidentialité / consentement" if "politique de confidentialité" in _norm_lc(raw) else raw
    except Exception:
        question = ""

    if not question:
        try:
            inferred = _norm(_find_question_text_near_element(cb) or "")
            if inferred:
                question = inferred
        except Exception:
            question = ""

    if not question:
        question = "Politique de confidentialité / consentement"

    group_base = cb_name or cb_id
    if not group_base:
        try:
            group_base = _best_xpath_for_element(driver, cb)
        except Exception:
            group_base = ""
    if not group_base:
        return []

    group_key = f"checkbox:name:{_norm_lc(group_base)}"
    target_id = make_target_id("group", group_key, question)

    if cb_id:
        id_lit = _xpath_literal(cb_id)
        option_xpath = f"(//label[@for={id_lit}] | //*[@id={id_lit}])[1]"
    elif cb_name:
        name_lit = _xpath_literal(cb_name)
        option_xpath = f"(//input[@type='checkbox' and @name={name_lit}]/ancestor::label[1] | //input[@type='checkbox' and @name={name_lit}])[1]"
    else:
        option_xpath = _best_xpath_for_element(driver, cb)

    option_xpath_map = {_norm_key(label_txt): option_xpath}

    register_target(
        target_id,
        {
            "kind": "group",
            "itype": "checkbox",
            "group_key": group_key,
            "question": question,
            "option_xpath_map": option_xpath_map,
            "frame_chain": frame_chain,
            "single_consent_checkbox": True,
        },
    )

    return [
        {
            "question": question,
            "itype": "checkbox",
            "options": [label_txt],
            "max_select": _compute_max_select("checkbox", [label_txt]),
            "target_id": target_id,
            "context": {
                "kind": "group",
                "group_key": group_key,
                "single_consent_checkbox": True,
            },
        }
    ]


def _extract_consent_modal_radio_block(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction ciblée d'un écran consentement modal radio + bouton confirmer.

    Scope minimal et déclenché uniquement par critères DOM observables:
    - présence d'un radiogroup de consentement (`.consent-form-radiogroup`)
    - >=2 radios partageant le même name
    - présence d'un bouton de confirmation (`#consent-button-confirm`)
    """

    frame_chain = list(frame_chain or [])

    def _is_dom_visible(el: Any) -> bool:
        """Best-effort visibilité DOM sans hypothèse provider globale."""
        if el is None:
            return False
        try:
            if hasattr(el, "is_displayed") and not el.is_displayed():
                return False
        except Exception:
            return False
        try:
            style = _norm_lc(el.get_attribute("style") or "")
        except Exception:
            style = ""
        if "display:none" in style or "visibility:hidden" in style:
            return False
        try:
            aria_hidden = _norm_lc(el.get_attribute("aria-hidden") or "")
        except Exception:
            aria_hidden = ""
        if aria_hidden == "true":
            return False
        return True

    try:
        modal_nodes = driver.find_elements(By.CSS_SELECTOR, "#modal-container")
        if not modal_nodes:
            return []
        visible_modals = [el for el in modal_nodes if _is_dom_visible(el)]
        if not visible_modals:
            return []

        radiogroups = driver.find_elements(By.CSS_SELECTOR, ".consent-form-radiogroup")
        if not radiogroups:
            return []
        if not any(_is_dom_visible(el) for el in radiogroups):
            return []

        confirm_buttons = driver.find_elements(By.CSS_SELECTOR, "#consent-button-confirm")
        if not confirm_buttons:
            return []
        if not any(_is_dom_visible(el) for el in confirm_buttons):
            return []
    except Exception:
        return []

    try:
        radio_inputs = driver.find_elements(
            By.CSS_SELECTOR,
            ".consent-form-radiogroup input[type='radio'][name]",
        )
    except Exception:
        return []

    if len(radio_inputs) < 2:
        return []

    grouped: dict[str, list[Any]] = {}
    for radio in radio_inputs:
        try:
            name = _norm_lc(radio.get_attribute("name") or "")
        except Exception:
            name = ""
        if not name:
            continue
        grouped.setdefault(name, []).append(radio)

    if not grouped:
        return []

    group_name, radios = max(grouped.items(), key=lambda kv: len(kv[1]))
    if len(radios) < 2:
        return []

    options: list[str] = []
    option_xpath_map: dict[str, str] = {}

    for radio in radios:
        try:
            rid = (radio.get_attribute("id") or "").strip()
            if not rid:
                continue

            label = ""
            try:
                lbl = driver.find_element(By.CSS_SELECTOR, f"label[for='{rid}'] .consent-option-text")
                label = _norm(lbl.text or lbl.get_attribute("innerText") or "")
            except Exception:
                try:
                    lbl = driver.find_element(By.CSS_SELECTOR, f"label[for='{rid}']")
                    label = _norm(lbl.text or lbl.get_attribute("innerText") or "")
                except Exception:
                    try:
                        lbl = radio.find_element(By.XPATH, "ancestor::label[contains(@class,'consent-option-label')][1]")
                        label = _norm(lbl.text or lbl.get_attribute("innerText") or "")
                    except Exception:
                        label = ""

            if not label:
                continue

            key = _norm_key(label)
            if key in option_xpath_map:
                continue

            rid_lit = _xpath_literal(rid)
            label_by_for = f"//label[@for={rid_lit}]"
            label_ancestor = f"//*[@id={rid_lit}]/ancestor::label[contains(@class,'consent-option-label')][1]"
            span_txt = (
                "//span[contains(@class,'consent-option-text') and "
                "normalize-space()=\"JE CONSENS et continue l'enquête\"]"
                if "je consens" in key
                else None
            )
            option_xpath_map[key] = (
                f"({label_by_for} | {label_ancestor}{' | ' + span_txt if span_txt else ''})[1]"
            )
            options.append(label)
        except Exception:
            continue

    if len(options) < 2:
        return []

    question = "Consentement RGPD"
    try:
        error_msg = driver.find_elements(By.CSS_SELECTOR, "#consent-error-message-container")
        if error_msg:
            txt = _norm(error_msg[0].text or error_msg[0].get_attribute("innerText") or "")
            if txt:
                question = txt
    except Exception:
        pass

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
            "consent_modal_radio": True,
        },
    )

    print(f"[CONSENT_MODAL] detected=true options={len(options)}")

    return [
        {
            "question": question,
            "itype": "radio",
            "options": options,
            "max_select": _compute_max_select("radio", options),
            "target_id": target_id,
            "context": {
                "kind": "group",
                "group_key": group_key,
                "consent_modal_radio": True,
            },
        }
    ]


def _extract_ipsos_slider_question_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """IPSOS sliders (bootstrap-slider): extraction DOM-only en blocs exploitables.

    Cas visé: pages IPSOS avec plusieurs questions Likert 1-5 rendues via
    `input.slider-form-field.bs-slider` (input hidden) + labels de ticks visibles.
    L'extraction générique radio/checkbox retourne alors 0 bloc.
    """
    frame_chain = list(frame_chain or [])

    try:
        if not driver.find_elements(By.CSS_SELECTOR, "h3.question-title-frontend"):
            return []
        if not driver.find_elements(By.CSS_SELECTOR, "input.slider-form-field.bs-slider"):
            return []
    except Exception:
        return []

    blocks: list[dict] = []
    seen_group_keys: set[str] = set()

    try:
        question_titles = driver.find_elements(By.CSS_SELECTOR, "h3.question-title-frontend")
    except Exception:
        question_titles = []

    for qh in question_titles[:20]:
        try:
            question = _norm(qh.text or qh.get_attribute("innerText") or "")
            if not question:
                continue

            try:
                wrapper = qh.find_element(By.XPATH, "ancestor::div[1]")
            except Exception:
                wrapper = None
            if not wrapper:
                continue

            try:
                sliders = wrapper.find_elements(By.CSS_SELECTOR, "input.slider-form-field.bs-slider[name]")
            except Exception:
                sliders = []
            if not sliders:
                continue

            slider = sliders[0]
            slider_name = (slider.get_attribute("name") or "").strip()
            if not slider_name:
                continue

            group_key = f"ipsos_slider:name:{slider_name}"
            if group_key in seen_group_keys:
                continue

            ticks_raw = (slider.get_attribute("data-slider-ticks") or "").strip()
            ticks = [t for t in [x.strip() for x in ticks_raw.strip("[]").split(",")] if t]
            options = [t.strip('"\' ') for t in ticks if t.strip('"\' ')]
            if len(options) < 2:
                continue

            option_xpath_map: dict[str, str] = {}
            name_lit = _xpath_literal(slider_name)

            for opt in options:
                try:
                    opt_lit = _xpath_literal(opt)
                    xp = (
                        f"(//input[@name={name_lit}]/preceding::h3[contains(@class,'question-title-frontend')][1]"
                        f"/ancestor::div[1]//*[contains(@class,'slider-tick-label') and normalize-space(.)={opt_lit}])[1]"
                    )
                    option_xpath_map[_norm_key(opt)] = xp
                except Exception:
                    continue

            if len(option_xpath_map) < 2:
                continue

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
                    "ipsos_slider": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key, "ipsos_slider": True},
                }
            )
            seen_group_keys.add(group_key)

        except Exception:
            continue

    return blocks


def _extract_confirmit_slider_grid_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Forsta/Confirmit slider-grid: 1 ligne = 1 bloc radio exploitable.

    Gate DOM strict (provider-agnostic):
    - conteneur `.cf-question--slider-grid`
    - présence de sliders custom `.cf-slider__handle[role='slider']`
    """
    frame_chain = list(frame_chain or [])

    try:
        questions = driver.find_elements(By.CSS_SELECTOR, ".cf-question.cf-question--slider-grid")
    except Exception:
        return []

    if not questions:
        return []

    blocks: list[dict] = []

    for q in questions:
        try:
            try:
                handles = q.find_elements(By.CSS_SELECTOR, ".cf-slider__handle[role='slider']")
            except Exception:
                handles = []
            if not handles:
                continue

            question = ""
            for sel in (".cf-question__text", ".cf-question__title"):
                try:
                    q_el = q.find_element(By.CSS_SELECTOR, sel)
                    txt = _norm(q_el.text or q_el.get_attribute("innerText") or "")
                    if txt:
                        question = txt
                        break
                except Exception:
                    continue

            scale_labels: list[str] = []
            scale_code_to_index: dict[str, int] = {}
            seen_scale: set[str] = set()
            for sel in (
                ".cf-slider-grid-answer--fake-for-panel .cf-slider-grid-answer__scale-label",
                ".cf-slider-grid-answer__scale-label",
            ):
                try:
                    labels = q.find_elements(By.CSS_SELECTOR, sel)
                except Exception:
                    labels = []
                for label in labels:
                    txt = _norm(label.text or label.get_attribute("innerText") or "")
                    key = _norm_key(txt)
                    if not txt or not key or key in seen_scale:
                        continue
                    seen_scale.add(key)
                    scale_labels.append(txt)
                    idx = len(scale_labels)
                    try:
                        lid = (label.get_attribute("id") or "").strip()
                        m = re.search(r"_scale_([^_]+)_text$", lid)
                        if m:
                            scale_code_to_index[(m.group(1) or "").strip().lower()] = idx
                    except Exception:
                        pass
                if len(scale_labels) >= 2:
                    break

            if len(scale_labels) < 2:
                continue

            try:
                rows = q.find_elements(
                    By.CSS_SELECTOR,
                    ".cf-grid-layout__row.cf-slider-grid-answer[id]:not(.cf-slider-grid-answer--fake-for-panel)",
                )
            except Exception:
                rows = []

            for row in rows:
                try:
                    row_id = (row.get_attribute("id") or "").strip()
                    if not row_id:
                        continue

                    try:
                        row_handles = row.find_elements(By.CSS_SELECTOR, ".cf-slider__handle[role='slider']")
                    except Exception:
                        row_handles = []
                    if not row_handles:
                        continue

                    row_text = ""
                    for sel in (".cf-slider-grid-answer__text", ".cf-grid-layout__row-text"):
                        try:
                            row_el = row.find_element(By.CSS_SELECTOR, sel)
                            txt = _norm(row_el.text or row_el.get_attribute("innerText") or "")
                            if txt:
                                row_text = txt
                                break
                        except Exception:
                            continue

                    full_question = _norm(f"{question} {row_text}") if question else row_text
                    if not full_question:
                        full_question = question
                    if not full_question:
                        continue

                    group_key = f"confirmit_slider_grid:row:{row_id}"
                    row_lit = _xpath_literal(row_id)
                    option_xpath_map = {
                        _norm_key(opt): f"//*[@id={row_lit}]//*[contains(@class,'cf-slider-grid-answer__label')][{idx}]"
                        for idx, opt in enumerate(scale_labels, start=1)
                    }

                    target_id = make_target_id("group", group_key, full_question)
                    register_target(
                        target_id,
                        {
                            "kind": "group",
                            "itype": "radio",
                            "group_key": group_key,
                            "question": full_question,
                            "option_xpath_map": option_xpath_map,
                            "slider_grid_row_id": row_id,
                            "slider_grid_scale_labels": list(scale_labels),
                            "slider_grid_code_to_index": dict(scale_code_to_index),
                            "frame_chain": frame_chain,
                            "confirmit_slider_grid": True,
                        },
                    )

                    blocks.append(
                        {
                            "question": full_question,
                            "itype": "radio",
                            "options": list(scale_labels),
                            "max_select": 1,
                            "target_id": target_id,
                            "context": {
                                "kind": "group",
                                "group_key": group_key,
                                "confirmit_slider_grid": True,
                            },
                        }
                    )
                except Exception:
                    continue
        except Exception:
            continue

    return blocks


def _extract_custom_testid_single_select_radio_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Questions radio custom sans <input> natif, pilotées par data-testid.

    Cas visé (Angular custom):
    - question: label[data-testid='common-question-label-text']
    - options: div[data-testid='answer-radio-div-container']
    - libellé option: label[data-testid='answer-radio-label-radiotext']

    Gate strict: n'active l'extracteur que si ces data-testid sont présents.
    """
    frame_chain = list(frame_chain or [])

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div[data-testid='common-question-div-container']")
    except Exception:
        return []

    if not containers:
        return []

    blocks: list[dict] = []

    for idx, container in enumerate(containers, start=1):
        try:
            question = ""
            for qsel in (
                "label[data-testid='common-question-label-text']",
                "[data-testid='common-question-div-text'] label",
            ):
                try:
                    q_els = container.find_elements(By.CSS_SELECTOR, qsel)
                except Exception:
                    q_els = []
                for q_el in q_els:
                    txt = _norm(q_el.text or q_el.get_attribute("innerText") or "")
                    if txt and len(txt) >= 5:
                        question = txt
                        break
                if question:
                    break

            if not question:
                continue

            try:
                option_nodes = container.find_elements(By.CSS_SELECTOR, "div[data-testid='answer-radio-div-container']")
            except Exception:
                option_nodes = []
            if len(option_nodes) < 2:
                continue

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}

            for opt_i, opt in enumerate(option_nodes, start=1):
                try:
                    label_text = ""
                    for lsel in (
                        "label[data-testid='answer-radio-label-radiotext']",
                        "label",
                    ):
                        try:
                            labels = opt.find_elements(By.CSS_SELECTOR, lsel)
                        except Exception:
                            labels = []
                        for lab in labels:
                            cand = _norm(lab.text or lab.get_attribute("innerText") or "")
                            if cand:
                                label_text = cand
                                break
                        if label_text:
                            break

                    if not label_text:
                        continue

                    nk = _norm_key(label_text)
                    if not nk or nk in option_xpath_map:
                        continue

                    xp = _best_xpath_for_element(driver, opt)
                    if not xp:
                        continue

                    option_xpath_map[nk] = xp
                    options.append(label_text)
                except Exception:
                    continue

            if len(options) < 2 or len(option_xpath_map) < 2:
                continue

            container_id = ""
            try:
                q_containers = container.find_elements(By.CSS_SELECTOR, "[id][data-testid*='question-singleselect']")
                if q_containers:
                    container_id = (q_containers[0].get_attribute("id") or "").strip()
            except Exception:
                container_id = ""

            if not container_id:
                container_id = f"idx{idx}_{zlib.crc32(question.encode('utf-8')):x}"

            group_key = f"custom_testid_single_select:radio:{container_id}"
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
                    "custom_testid_single_select": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": _compute_max_select("radio", options),
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "custom_testid_single_select": True,
                    },
                }
            )
        except Exception:
            continue

    return blocks


def _extract_button_choice_radio_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction radio pour options rendues en `button.choice` (sans input natif).

    Gate DOM strict (additif, non provider-wide):
    - `div.question-body-options__inner` contenant >= 2 `div.question-body-options__choice`
    - chaque option contient `button.choice[id]`
    - texte option accessible via `.choice__label`
    - question accessible via `.question-title__title`
    """
    frame_chain = list(frame_chain or [])

    try:
        option_roots = driver.find_elements(By.CSS_SELECTOR, "div.question-body-options__inner")
    except Exception:
        return []

    if not option_roots:
        return []

    question = ""
    try:
        question_nodes = driver.find_elements(By.CSS_SELECTOR, ".question-title__title")
    except Exception:
        question_nodes = []

    for qn in question_nodes:
        try:
            qtxt = _norm(qn.text or qn.get_attribute("innerText") or "")
            if qtxt:
                question = qtxt
                break
        except Exception:
            continue

    if not question:
        return []

    blocks: list[dict] = []

    for root_idx, root in enumerate(option_roots, start=1):
        try:
            choice_wrappers = root.find_elements(By.CSS_SELECTOR, "div.question-body-options__choice")
        except Exception:
            continue

        if len(choice_wrappers) < 2:
            continue

        options: list[str] = []
        option_xpath_map: dict[str, str] = {}
        button_ids: list[str] = []

        for choice in choice_wrappers:
            try:
                btn = choice.find_element(By.CSS_SELECTOR, "button.choice")
                btn_id = (btn.get_attribute("id") or "").strip()
                if not btn_id:
                    options = []
                    option_xpath_map = {}
                    break

                label_txt = ""
                try:
                    labels = btn.find_elements(By.CSS_SELECTOR, ".choice__label")
                except Exception:
                    labels = []

                for lb in labels:
                    cand = _norm(lb.text or lb.get_attribute("innerText") or "")
                    if cand:
                        label_txt = cand
                        break

                if not label_txt:
                    options = []
                    option_xpath_map = {}
                    break

                nk = _norm_key(label_txt)
                if not nk or nk in option_xpath_map:
                    continue

                option_xpath_map[nk] = f"//button[@id={_xpath_literal(btn_id)} and contains(concat(' ', normalize-space(@class), ' '), ' choice ')]"
                options.append(label_txt)
                button_ids.append(btn_id)
            except Exception:
                options = []
                option_xpath_map = {}
                break

        if len(options) < 2 or len(option_xpath_map) < 2:
            continue

        group_sig = "|".join(button_ids[:10])
        group_key = f"button_choice_radio:{root_idx}:{zlib.crc32(group_sig.encode('utf-8')):x}"
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
                "button_choice_radio": True,
                "studystream_auto_advance": True,
            },
        )

        blocks.append(
            {
                "question": question,
                "itype": "radio",
                "options": options,
                "max_select": _compute_max_select("radio", options),
                "target_id": target_id,
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "button_choice_radio": True,
                    "studystream_auto_advance": True,
                },
            }
        )

    return blocks


def _extract_runtime_answerrow_radio_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction des radios custom basées sur des wrappers `.answer[data-aut='Runtime_AnswerRow']`.

    Gate strict (DOM observable):
    - texte question via `[data-aut='Runtime_QuestionTitleAndDescriptionWrapper'] [data-aut='Runtime-TextComponent']`
    - options via `.answer[data-aut='Runtime_AnswerRow']`
    - contrôle radio custom via `.radio_button[data-aut='Runtime_Wrapper']`
    """
    frame_chain = list(frame_chain or [])
    debug = (os.getenv("DOM_CONTEXT_DEBUG", "") or "").strip().lower() in {"1", "true", "yes", "on"}

    try:
        question_nodes = driver.find_elements(
            By.CSS_SELECTOR,
            "[data-aut='Runtime_QuestionTitleAndDescriptionWrapper'] [data-aut='Runtime-TextComponent']",
        )
        answer_rows = driver.find_elements(By.CSS_SELECTOR, ".answer[data-aut='Runtime_AnswerRow']")
    except Exception as e:
        if debug:
            print(f"[DOM_CONTEXT_DEBUG] runtime_answerrow extractor_exception={type(e).__name__}: {e}")
        return []

    if debug:
        print(
            f"[DOM_CONTEXT_DEBUG] runtime_answerrow counts "
            f"question_nodes={len(question_nodes)} answer_rows={len(answer_rows)}"
        )

    if not question_nodes or len(answer_rows) < 2:
        return []

    blocks: list[dict] = []
    grouped_rows: dict[str, list[Any]] = {}

    for row in answer_rows:
        try:
            radio_wrappers = row.find_elements(By.CSS_SELECTOR, ".radio_button[data-aut='Runtime_Wrapper']")
            if not radio_wrappers:
                continue

            question_container = None
            try:
                question_container = row.find_element(
                    By.XPATH,
                    "ancestor::*[@id][starts-with(@id, 'question_') and not(starts-with(@id, 'question_container_'))][1]",
                )
            except Exception:
                question_container = None

            if question_container is None:
                try:
                    question_container = row.find_element(
                        By.XPATH,
                        "ancestor::*[@data-aut='Runtime_QuestionWrapper'][1]",
                    )
                except Exception:
                    question_container = None

            question_container_id = ""
            if question_container is not None:
                question_container_id = (question_container.get_attribute("id") or "").strip()

            if not question_container_id:
                question_container_id = "runtime_question_default"

            grouped_rows.setdefault(question_container_id, []).append(row)
        except Exception:
            continue

    if debug:
        print(f"[DOM_CONTEXT_DEBUG] runtime_answerrow grouped_rows keys={sorted(grouped_rows.keys())}")

    try:
        for qid, rows in grouped_rows.items():
            if len(rows) < 2:
                continue

            question = ""
            try:
                question_container = rows[0].find_element(
                    By.XPATH,
                    "ancestor::*[@id][starts-with(@id, 'question_') and not(starts-with(@id, 'question_container_'))][1]",
                )
            except Exception:
                question_container = None

            if question_container is None:
                try:
                    question_container = rows[0].find_element(
                        By.XPATH,
                        "ancestor::*[@data-aut='Runtime_QuestionWrapper'][1]",
                    )
                except Exception:
                    question_container = None

            if question_container is not None:
                q_nodes = question_container.find_elements(
                    By.CSS_SELECTOR,
                    "[data-aut='Runtime_QuestionTitleAndDescriptionWrapper'] [data-aut='Runtime-TextComponent']",
                )
                for qn in q_nodes:
                    txt = _norm(qn.text or qn.get_attribute("innerText") or "")
                    if txt and len(txt) >= 5:
                        question = txt
                        break

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}

            for row in rows:
                try:
                    text_nodes = row.find_elements(
                        By.CSS_SELECTOR,
                        "[data-aut='Runtime_AnswerText'] [data-aut='Runtime-TextComponent']",
                    )
                    label_text = ""
                    for tn in text_nodes:
                        txt = _norm(tn.text or tn.get_attribute("innerText") or "")
                        if txt:
                            label_text = txt
                            break

                    if not label_text:
                        continue

                    nk = _norm_key(label_text)
                    if not nk or nk in option_xpath_map:
                        continue

                    row_id = (row.get_attribute("id") or "").strip()
                    if row_id:
                        xp = f"//*[@id={_xpath_literal(row_id)}]"
                    else:
                        xp = _best_xpath_for_element(driver, row)
                    if not xp:
                        continue

                    option_xpath_map[nk] = xp
                    options.append(label_text)
                except Exception:
                    continue

            if debug:
                print(
                    f"[DOM_CONTEXT_DEBUG] runtime_answerrow group qid={qid} rows={len(rows)} "
                    f"question_found={bool(question)} options_count={len(options)} "
                    f"xpath_map_count={len(option_xpath_map)}"
                )

            if not question:
                continue

            if len(options) < 2 or len(option_xpath_map) < 2:
                continue

            group_key = f"runtime_answerrow:radio:{qid}"
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
                    "runtime_answerrow_radio": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "runtime_answerrow_radio": True,
                    },
                }
            )
    except Exception as e:
        if debug:
            print(f"[DOM_CONTEXT_DEBUG] runtime_answerrow extractor_exception={type(e).__name__}: {e}")

    return blocks


def _extract_kantar_rowpicker_radio_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction radio pour Kantar rowpicker (`[data-test='main-contain']._rowpicker`).

    Gate DOM strict:
    - conteneur options: `div[id^='container_'] [data-test='main-contain']._rowpicker`
    - question associée: `#qc_<suffixe_container> span.mrQuestionText`
    - options: cartes flex avec `label` texte + overlay cliquable `tabindex='0'`
    """
    frame_chain = list(frame_chain or [])

    try:
        pickers = driver.find_elements(
            By.CSS_SELECTOR,
            "div[id^='container_'] [data-test='main-contain']._rowpicker",
        )
    except Exception:
        return []

    if not pickers:
        return []

    blocks: list[dict] = []

    for picker in pickers:
        try:
            container = picker.find_element(By.XPATH, "ancestor::div[starts-with(@id,'container_')][1]")
            container_id = (container.get_attribute("id") or "").strip()
        except Exception:
            continue

        if not container_id.startswith("container_"):
            continue

        q_suffix = container_id[len("container_"):].strip()
        if not q_suffix:
            continue

        question = ""
        try:
            q_nodes = driver.find_elements(By.CSS_SELECTOR, f"#qc_{q_suffix} span.mrQuestionText")
        except Exception:
            q_nodes = []

        # Variante DOM observée: le conteneur options est `container_<suffixe_court>`
        # (ex: `container_S1`) alors que le texte question est stocké dans
        # `#qc_<questionname_complet>` (ex: `qc_S1BL.S1`).
        # On complète donc la recherche via l'attribut `questionname` du wrapper.
        if not q_nodes:
            try:
                q_nodes = driver.find_elements(
                    By.CSS_SELECTOR,
                    f".questionContainer[questionname$='.{q_suffix}'] span.mrQuestionText",
                )
            except Exception:
                q_nodes = []

        for qn in q_nodes:
            q_txt = _norm(qn.text or qn.get_attribute("innerText") or "")
            if q_txt and len(q_txt) >= 8:
                question = q_txt
                break

        if not question:
            continue

        try:
            cards = picker.find_elements(By.CSS_SELECTOR, "div.__flexgrid_row > div")
        except Exception:
            cards = []

        options: list[str] = []
        option_xpath_map: dict[str, str] = {}

        for card in cards:
            try:
                clickable = card.find_element(By.CSS_SELECTOR, "div[tabindex='0']")
                label_nodes = card.find_elements(By.CSS_SELECTOR, "label span")
            except Exception:
                continue

            label_text = ""
            for ln in label_nodes:
                txt = _norm(ln.text or ln.get_attribute("innerText") or "")
                if txt:
                    label_text = txt
                    break

            if not label_text:
                continue

            nk = _norm_key(label_text)
            if not nk or nk in option_xpath_map:
                continue

            xp = _best_xpath_for_element(driver, clickable)
            if not xp:
                continue

            option_xpath_map[nk] = xp
            options.append(label_text)

        if len(options) < 2 or len(option_xpath_map) < 2:
            continue

        group_key = f"kantar_rowpicker:radio:{q_suffix}"
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
                "kantar_rowpicker_radio": True,
            },
        )

        blocks.append(
            {
                "question": question,
                "itype": "radio",
                "options": options,
                "max_select": 1,
                "target_id": target_id,
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "kantar_rowpicker_radio": True,
                },
            }
        )

    return blocks


def _extract_label_radio_list_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction radio pour listes `label.radio` sans input natif.

    Cas ciblé (Angular custom observé):
    - conteneur question: `div.step1`
    - titre question: `h3.title`
    - options: `ul.option_container label.radio`
    - pas de `input[type=radio]` exploitable dans ce conteneur.
    """
    frame_chain = list(frame_chain or [])

    try:
        step_nodes = driver.find_elements(By.CSS_SELECTOR, "div.step1")
    except Exception:
        return []

    if not step_nodes:
        return []

    blocks: list[dict] = []
    nav_tokens = {"next", "suivant", "continue", "continuer", "submit", "valider", "envoyer", "start"}

    for idx, step in enumerate(step_nodes, start=1):
        try:
            q_nodes = step.find_elements(By.CSS_SELECTOR, "h3.title")
        except Exception:
            q_nodes = []

        question = ""
        for qn in q_nodes:
            txt = _norm(qn.text or qn.get_attribute("innerText") or "")
            if txt and len(txt) >= 5:
                question = txt
                break
        if not question:
            continue

        try:
            native_choices = step.find_elements(
                By.CSS_SELECTOR,
                "input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox']",
            )
        except Exception:
            native_choices = []
        if native_choices:
            continue

        try:
            labels = step.find_elements(By.CSS_SELECTOR, "ul.option_container label.radio")
        except Exception:
            labels = []
        if len(labels) < 2:
            continue

        options: list[str] = []
        option_xpath_map: dict[str, str] = {}

        for lbl in labels:
            try:
                label_text = _norm(lbl.text or lbl.get_attribute("innerText") or "")
                if not label_text:
                    continue
                if _norm_lc(label_text) in nav_tokens:
                    continue

                nk = _norm_key(label_text)
                if not nk or nk in option_xpath_map:
                    continue

                xp = _best_xpath_for_element(driver, lbl)
                if not xp:
                    continue

                option_xpath_map[nk] = xp
                options.append(label_text)
            except Exception:
                continue

        if len(options) < 2 or len(option_xpath_map) < 2:
            continue

        step_id = (step.get_attribute("id") or "").strip()
        if not step_id:
            step_id = f"step{idx}_{zlib.crc32(question.encode('utf-8')):x}"

        group_key = f"label_radio_list:radio:{step_id}"
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
                "label_radio_list": True,
            },
        )

        blocks.append(
            {
                "question": question,
                "itype": "radio",
                "options": options,
                "max_select": _compute_max_select("radio", options),
                "target_id": target_id,
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "label_radio_list": True,
                },
            }
        )
    return blocks


def _extract_qualtrics_choice_structure_radio_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction ciblée des radios Qualtrics `ChoiceStructure` (UL ou TABLE).

    Gate DOM strict (additif, sans hypothèse provider globale):
    - `div.QuestionOuter`
    - `ul.ChoiceStructure` ou `table.ChoiceStructure`
    - `input[type=radio][name^="QR~"]`
    """
    frame_chain = list(frame_chain or [])
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.QuestionOuter")
    except Exception:
        return blocks

    for idx, container in enumerate(containers):
        try:
            radios = container.find_elements(
                By.CSS_SELECTOR,
                (
                    "ul.ChoiceStructure li.Selection input[type='radio'][name^='QR~'], "
                    "table.ChoiceStructure input[type='radio'][name^='QR~']"
                ),
            )
        except Exception:
            radios = []

        if len(radios) < 2:
            continue

        question = ""
        for q_sel in (
            "div.Inner fieldset legend div.QuestionText",
            "fieldset legend div.QuestionText",
            "fieldset legend label.QuestionText",
            "legend .QuestionText",
            "div.QuestionText",
        ):
            try:
                q_nodes = container.find_elements(By.CSS_SELECTOR, q_sel)
            except Exception:
                q_nodes = []
            for qn in q_nodes:
                txt = _norm(qn.text or qn.get_attribute("innerText") or "")
                if txt:
                    question = txt
                    break
            if question:
                break

        if not question:
            continue

        # Layout Qualtrics Bipolar: 1 bloc radio par ligne ChoiceRow, avec pôles gauche/droite.
        # Gate DOM strict pour rester additif et ne pas impacter les autres layouts ChoiceStructure.
        try:
            bipolar_nodes = container.find_elements(By.CSS_SELECTOR, "div.Inner.Bipolar")
        except Exception:
            bipolar_nodes = []
        try:
            bipolar_left_headers = container.find_elements(
                By.CSS_SELECTOR,
                "table.ChoiceStructure th[id^='header~left~']",
            )
        except Exception:
            bipolar_left_headers = []

        if bipolar_nodes and bipolar_left_headers:
            try:
                bipolar_rows = container.find_elements(
                    By.CSS_SELECTOR,
                    "table.ChoiceStructure > tbody > tr.ChoiceRow",
                )
            except Exception:
                bipolar_rows = []

            for row_idx, row in enumerate(bipolar_rows):
                try:
                    row_radios = row.find_elements(By.CSS_SELECTOR, "input[type='radio'][name^='QR~']")
                except Exception:
                    row_radios = []
                if len(row_radios) < 2:
                    continue

                row_group_name = ""
                try:
                    row_group_name = (row_radios[0].get_attribute("name") or "").strip()
                except Exception:
                    row_group_name = ""
                if not row_group_name:
                    continue

                left_txt = ""
                right_txt = ""
                try:
                    left_nodes = row.find_elements(By.CSS_SELECTOR, "th[id^='header~left~']")
                except Exception:
                    left_nodes = []
                for left_node in left_nodes:
                    cand = _norm(left_node.text or left_node.get_attribute("innerText") or "")
                    if cand:
                        left_txt = cand
                        break

                try:
                    right_nodes = row.find_elements(By.CSS_SELECTOR, "th[id^='header~right~']")
                except Exception:
                    right_nodes = []
                for right_node in right_nodes:
                    cand = _norm(right_node.text or right_node.get_attribute("innerText") or "")
                    if cand:
                        right_txt = cand
                        break

                if not left_txt or not right_txt:
                    continue

                left_radio = None
                right_radio = None
                for r in row_radios:
                    try:
                        r_val = (r.get_attribute("value") or "").strip()
                    except Exception:
                        r_val = ""
                    if r_val == "1" and left_radio is None:
                        left_radio = r
                    elif r_val == "2" and right_radio is None:
                        right_radio = r

                if left_radio is None:
                    left_radio = row_radios[0]
                if right_radio is None:
                    right_radio = row_radios[1] if len(row_radios) > 1 else None
                if right_radio is None:
                    continue

                option_xpath_map: dict[str, str] = {}
                options: list[str] = []
                for opt_txt, opt_radio in ((left_txt, left_radio), (right_txt, right_radio)):
                    nk = _norm_key(opt_txt)
                    if not nk or nk in option_xpath_map:
                        continue
                    try:
                        opt_id = (opt_radio.get_attribute("id") or "").strip()
                    except Exception:
                        opt_id = ""
                    if opt_id:
                        xp = f"//*[@id={_xpath_literal(opt_id)}]"
                    else:
                        xp = _best_xpath_for_element(driver, opt_radio)
                    if not xp:
                        continue
                    option_xpath_map[nk] = xp
                    options.append(opt_txt)

                if len(options) < 2 or len(option_xpath_map) < 2:
                    continue

                group_key = f"qualtrics_choice_structure:radio:{row_group_name}"
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
                        "qualtrics_choice_structure_radio": True,
                        "qualtrics_choice_structure_bipolar": True,
                    },
                )

                blocks.append(
                    {
                        "question": question,
                        "itype": "radio",
                        "options": options,
                        "max_select": _compute_max_select("radio", options),
                        "target_id": target_id,
                        "context": {
                            "kind": "group",
                            "group_key": group_key,
                            "qualtrics_choice_structure_radio": True,
                            "qualtrics_choice_structure_bipolar": True,
                            "container_index": idx,
                            "row_index": row_idx,
                        },
                    }
                )

            continue

        group_name = ""
        options: list[str] = []
        option_xpath_map: dict[str, str] = {}

        for radio in radios:
            try:
                radio_name = (radio.get_attribute("name") or "").strip()
                if not radio_name:
                    continue
                if not group_name:
                    group_name = radio_name
                if radio_name != group_name:
                    continue

                radio_id = (radio.get_attribute("id") or "").strip()

                label_text = ""
                if radio_id:
                    for lsel in (
                        f"label.SingleAnswer[for='{radio_id}'] span",
                        f"label[for='{radio_id}'].SingleAnswer span",
                    ):
                        try:
                            lbl_nodes = container.find_elements(By.CSS_SELECTOR, lsel)
                        except Exception:
                            lbl_nodes = []
                        for lbl in lbl_nodes:
                            cand = _norm(lbl.text or lbl.get_attribute("innerText") or "")
                            if cand:
                                label_text = cand
                                break
                        if label_text:
                            break

                if not label_text:
                    try:
                        parent_li = radio.find_element(By.XPATH, "ancestor::li[contains(@class,'Selection')][1]")
                        text_nodes = parent_li.find_elements(By.CSS_SELECTOR, "label.SingleAnswer span")
                    except Exception:
                        text_nodes = []
                    for tn in text_nodes:
                        cand = _norm(tn.text or tn.get_attribute("innerText") or "")
                        if cand:
                            label_text = cand
                            break

                if not label_text:
                    continue

                nk = _norm_key(label_text)
                if not nk or nk in option_xpath_map:
                    continue

                if radio_id:
                    xp = f"//*[@id={_xpath_literal(radio_id)}]"
                else:
                    xp = _best_xpath_for_element(driver, radio)
                if not xp:
                    continue

                options.append(label_text)
                option_xpath_map[nk] = xp
            except Exception:
                continue

        if len(options) < 2 or len(option_xpath_map) < 2 or not group_name:
            continue

        group_key = f"qualtrics_choice_structure:radio:{group_name}"
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
                "qualtrics_choice_structure_radio": True,
            },
        )

        blocks.append(
            {
                "question": question,
                "itype": "radio",
                "options": options,
                "max_select": _compute_max_select("radio", options),
                "target_id": target_id,
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "qualtrics_choice_structure_radio": True,
                    "container_index": idx,
                },
            }
        )

    return blocks


def _extract_qualtrics_choice_structure_checkbox_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction ciblée des checkboxes Qualtrics `ChoiceStructure` (layout MAVR/MAHR).

    Gate DOM strict (additif, sans hypothèse provider globale):
    - `div.QuestionOuter`
    - `ul.ChoiceStructure` ou `table.ChoiceStructure`
    - `input[type=checkbox][name^="QR~"]`
    - labels d'options `label.MultipleAnswer[for='<input_id>']`
    """
    frame_chain = list(frame_chain or [])
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.QuestionOuter")
    except Exception:
        return blocks

    for idx, container in enumerate(containers):
        # Cas matrice checkbox Qualtrics: 1 bloc par ligne `tr.ChoiceRow`.
        # Gate DOM strict pour éviter d'impacter les autres layouts ChoiceStructure.
        try:
            header_cells = container.find_elements(
                By.CSS_SELECTOR,
                "table.ChoiceStructure > thead > tr.Answers > th",
            )
        except Exception:
            header_cells = []

        try:
            matrix_rows = container.find_elements(
                By.CSS_SELECTOR,
                "table.ChoiceStructure > tbody > tr.ChoiceRow",
            )
        except Exception:
            matrix_rows = []

        column_labels: list[str] = []
        if len(header_cells) >= 3:
            for h in header_cells:
                try:
                    h_cls = (h.get_attribute("class") or "").strip().lower()
                except Exception:
                    h_cls = ""
                if "c1" in h_cls:
                    continue
                h_txt = _norm(h.text or h.get_attribute("innerText") or "")
                if h_txt:
                    column_labels.append(h_txt)

        row_checkbox_counts: list[int] = []
        if column_labels and matrix_rows:
            for row in matrix_rows:
                try:
                    row_checks = row.find_elements(By.CSS_SELECTOR, "input[type='checkbox'][name^='QR~']")
                except Exception:
                    row_checks = []
                row_checkbox_counts.append(len(row_checks))

        is_table_matrix = (
            len(column_labels) >= 2
            and len(matrix_rows) >= 1
            and any(c > 1 for c in row_checkbox_counts)
        )

        try:
            checkboxes = container.find_elements(
                By.CSS_SELECTOR,
                "ul.ChoiceStructure li.Selection input[type='checkbox'][name^='QR~'], "
                "table.ChoiceStructure input[type='checkbox'][name^='QR~']",
            )
        except Exception:
            checkboxes = []

        if len(checkboxes) < 2:
            continue

        question = ""
        for q_sel in (
            "div.Inner fieldset legend div.QuestionText",
            "fieldset legend div.QuestionText",
            "div.QuestionText",
        ):
            try:
                q_nodes = container.find_elements(By.CSS_SELECTOR, q_sel)
            except Exception:
                q_nodes = []
            for qn in q_nodes:
                txt = _norm(qn.text or qn.get_attribute("innerText") or "")
                if txt:
                    question = txt
                    break
            if question:
                break

        if not question:
            continue

        if is_table_matrix:
            for row_idx, row in enumerate(matrix_rows):
                try:
                    row_checkboxes = row.find_elements(By.CSS_SELECTOR, "input[type='checkbox'][name^='QR~']")
                except Exception:
                    row_checkboxes = []

                if len(row_checkboxes) < 2:
                    continue

                row_label = ""
                for rsel in (
                    "th.c1 span",
                    "th[scope='row'] span",
                    "th.c1 label span",
                    "th[scope='row'] label span",
                ):
                    try:
                        row_nodes = row.find_elements(By.CSS_SELECTOR, rsel)
                    except Exception:
                        row_nodes = []
                    for rn in row_nodes:
                        cand = _norm(rn.text or rn.get_attribute("innerText") or "")
                        if cand:
                            row_label = cand
                            break
                    if row_label:
                        break

                if not row_label:
                    continue

                row_question = _norm(f"{question} {row_label}")
                if not row_question:
                    continue

                options: list[str] = []
                option_xpath_map: dict[str, str] = {}
                for col_idx, checkbox in enumerate(row_checkboxes):
                    if col_idx >= len(column_labels):
                        break
                    label_text = column_labels[col_idx]
                    checkbox_id = (checkbox.get_attribute("id") or "").strip()
                    if not checkbox_id:
                        continue
                    nk = _norm_key(label_text)
                    if not nk or nk in option_xpath_map:
                        continue
                    option_xpath_map[nk] = f"//*[@id={_xpath_literal(checkbox_id)}]"
                    options.append(label_text)

                if len(options) < 2 or len(option_xpath_map) < 2:
                    continue

                first_name = (row_checkboxes[0].get_attribute("name") or "").strip()
                group_name = first_name or f"row-{row_idx + 1}"
                group_key = f"qualtrics_choice_structure:checkbox:{group_name}"
                target_id = make_target_id("group", group_key, row_question)
                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "itype": "checkbox",
                        "group_key": group_key,
                        "question": row_question,
                        "option_xpath_map": option_xpath_map,
                        "frame_chain": frame_chain,
                        "qualtrics_choice_structure_checkbox": True,
                    },
                )

                blocks.append(
                    {
                        "question": row_question,
                        "itype": "checkbox",
                        "options": options,
                        "max_select": _compute_max_select("checkbox", options),
                        "target_id": target_id,
                        "context": {
                            "kind": "group",
                            "group_key": group_key,
                            "qualtrics_choice_structure_checkbox": True,
                            "container_index": idx,
                            "matrix_row_index": row_idx,
                        },
                    }
                )

            if blocks:
                continue

        group_name = ""
        options: list[str] = []
        option_xpath_map: dict[str, str] = {}

        for checkbox in checkboxes:
            try:
                checkbox_name = (checkbox.get_attribute("name") or "").strip()
                if not checkbox_name:
                    continue

                # En Qualtrics MAVR/MAHR, chaque option a son propre name
                # (`QR~QID13~21`, `QR~QID13~27`, ...). On regroupe par préfixe QID.
                base_name = checkbox_name.rsplit("~", 1)[0] if "~" in checkbox_name else checkbox_name
                if not group_name:
                    group_name = base_name
                if base_name != group_name:
                    continue

                checkbox_id = (checkbox.get_attribute("id") or "").strip()
                if not checkbox_id:
                    continue

                label_text = ""
                for lsel in (
                    f"label.MultipleAnswer[for='{checkbox_id}'] span",
                    f"label[for='{checkbox_id}'].MultipleAnswer span",
                    f"label[for='{checkbox_id}'] span",
                ):
                    try:
                        lbl_nodes = container.find_elements(By.CSS_SELECTOR, lsel)
                    except Exception:
                        lbl_nodes = []
                    for lbl in lbl_nodes:
                        cand = _norm(lbl.text or lbl.get_attribute("innerText") or "")
                        if cand:
                            label_text = cand
                            break
                    if label_text:
                        break

                if not label_text:
                    continue

                nk = _norm_key(label_text)
                if not nk or nk in option_xpath_map:
                    continue

                option_xpath_map[nk] = f"//*[@id={_xpath_literal(checkbox_id)}]"
                options.append(label_text)
            except Exception:
                continue

        if len(options) < 2 or len(option_xpath_map) < 2 or not group_name:
            continue

        group_key = f"qualtrics_choice_structure:checkbox:{group_name}"
        target_id = make_target_id("group", group_key, question)
        register_target(
            target_id,
            {
                "kind": "group",
                "itype": "checkbox",
                "group_key": group_key,
                "question": question,
                "option_xpath_map": option_xpath_map,
                "frame_chain": frame_chain,
                "qualtrics_choice_structure_checkbox": True,
            },
        )

        blocks.append(
            {
                "question": question,
                "itype": "checkbox",
                "options": options,
                "max_select": _compute_max_select("checkbox", options),
                "target_id": target_id,
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "qualtrics_choice_structure_checkbox": True,
                    "container_index": idx,
                },
            }
        )

    return blocks


def _extract_qualtrics_matrix_dropdown_row_blocks(
    driver,
    frame_chain: list[int] | None,
) -> Tuple[list[dict], Set[str], Set[str]]:
    """Extraction ciblée Qualtrics matrix dropdown: 1 bloc par `tr.ChoiceRow`.

    Gate DOM strict (additif):
    - `div.QuestionOuter` contenant `table.ChoiceStructure`
    - lignes `tr.ChoiceRow` avec un `<select>` par ligne
    - libellé ligne dans `th` via `label[for=<select_id>]` ou fallback `th`.

    Retourne:
    - blocks: blocs dropdown indépendants (un par ligne)
    - handled_select_ids: ids de `<select>` déjà transformés
    - handled_select_names: names de `<select>` déjà transformés
    """
    frame_chain = list(frame_chain or [])
    blocks: list[dict] = []
    handled_select_ids: Set[str] = set()
    handled_select_names: Set[str] = set()

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.QuestionOuter")
    except Exception:
        return blocks, handled_select_ids, handled_select_names

    for cidx, container in enumerate(containers):
        try:
            rows = container.find_elements(By.CSS_SELECTOR, "table.ChoiceStructure > tbody > tr.ChoiceRow")
        except Exception:
            rows = []

        if not rows:
            continue

        try:
            container_selects = container.find_elements(By.CSS_SELECTOR, "table.ChoiceStructure tr.ChoiceRow select")
        except Exception:
            container_selects = []

        if len(container_selects) < 2:
            continue

        for ridx, row in enumerate(rows, start=1):
            try:
                row_selects = row.find_elements(By.CSS_SELECTOR, "select")
            except Exception:
                row_selects = []

            if len(row_selects) != 1:
                continue

            sel = row_selects[0]
            sel_id = (sel.get_attribute("id") or "").strip()
            sel_name = (sel.get_attribute("name") or "").strip()
            if not sel_id and not sel_name:
                continue

            row_question = ""
            if sel_id:
                try:
                    q_nodes = row.find_elements(By.CSS_SELECTOR, f"th label[for='{sel_id}']")
                except Exception:
                    q_nodes = []
                for qn in q_nodes:
                    cand = _norm(qn.text or qn.get_attribute("innerText") or "")
                    if cand:
                        row_question = cand
                        break

            if not row_question:
                try:
                    th_nodes = row.find_elements(By.CSS_SELECTOR, "th")
                except Exception:
                    th_nodes = []
                for th in th_nodes:
                    cand = _norm(th.text or th.get_attribute("innerText") or "")
                    if cand:
                        row_question = cand
                        break

            if not row_question:
                continue

            options: list[str] = []
            try:
                option_nodes = sel.find_elements(By.TAG_NAME, "option")
            except Exception:
                option_nodes = []

            for opt in option_nodes:
                try:
                    if opt.get_attribute("disabled"):
                        continue
                    txt = _norm(opt.text or opt.get_attribute("innerText") or "")
                    if txt:
                        options.append(txt)
                except Exception:
                    continue

            options = list(dict.fromkeys(options))
            if len(options) < 2:
                continue

            single_key = f"qualtrics_matrix_dropdown:{sel_id}:{sel_name}"
            target_id = make_target_id("single", single_key, row_question)
            xpath = _best_xpath_for_element(driver, sel)

            alt_xpaths: list[str] = []
            try:
                if sel_name:
                    alt_xpaths.append(f"//select[@name={_xpath_literal(sel_name)}]")
                if sel_id:
                    alt_xpaths.append(f"//*[@id='{sel_id}']")
            except Exception:
                pass
            alt_xpaths = [x for x in dict.fromkeys(alt_xpaths) if x and x != xpath][:4]

            register_target(
                target_id,
                {
                    "kind": "single",
                    "itype": "dropdown",
                    "question": row_question,
                    "xpath": xpath,
                    "alt_xpaths": alt_xpaths,
                    "tag": "select",
                    "name": sel_name,
                    "id": sel_id,
                    "frame_chain": frame_chain,
                },
            )

            blocks.append(
                {
                    "question": row_question,
                    "itype": "dropdown",
                    "options": options,
                    "max_select": _compute_max_select("dropdown", options),
                    "target_id": target_id,
                    "context": {
                        "kind": "single",
                        "tag": "select",
                        "name": sel_name,
                        "id": sel_id,
                        "role": sel.get_attribute("role"),
                        "qualtrics_matrix_dropdown_row": True,
                        "container_index": cidx,
                        "row_index": ridx,
                    },
                }
            )

            if sel_id:
                handled_select_ids.add(sel_id)
            if sel_name:
                handled_select_names.add(sel_name)

    return blocks, handled_select_ids, handled_select_names


def _extract_custom_testid_multi_select_checkbox_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Questions checkbox custom sans <input> natif, pilotées par data-testid.

    Cas visé (Angular custom):
    - question: label[data-testid='common-question-label-text']
    - options: div[data-testid='answer-checkbox-div-container']
    - libellé option: label[data-testid='answer-checkbox-label-checkboxtext']

    Gate strict: n'active l'extracteur que si ces data-testid sont présents.
    """
    frame_chain = list(frame_chain or [])

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div[data-testid='common-question-div-container']")
    except Exception:
        return []

    if not containers:
        return []

    blocks: list[dict] = []

    for idx, container in enumerate(containers, start=1):
        try:
            question = ""
            for qsel in (
                "label[data-testid='common-question-label-text']",
                "[data-testid='common-question-div-text'] label",
            ):
                try:
                    q_els = container.find_elements(By.CSS_SELECTOR, qsel)
                except Exception:
                    q_els = []
                for q_el in q_els:
                    txt = _norm(q_el.text or q_el.get_attribute("innerText") or "")
                    if txt and len(txt) >= 5:
                        question = txt
                        break
                if question:
                    break

            if not question:
                continue

            try:
                option_nodes = container.find_elements(By.CSS_SELECTOR, "div[data-testid='answer-checkbox-div-container']")
            except Exception:
                option_nodes = []
            if len(option_nodes) < 2:
                continue

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}

            for opt in option_nodes:
                try:
                    label_text = ""
                    for lsel in (
                        "label[data-testid='answer-checkbox-label-checkboxtext']",
                        "label",
                    ):
                        try:
                            labels = opt.find_elements(By.CSS_SELECTOR, lsel)
                        except Exception:
                            labels = []
                        for lab in labels:
                            cand = _norm(lab.text or lab.get_attribute("innerText") or "")
                            if cand:
                                label_text = cand
                                break
                        if label_text:
                            break

                    if not label_text:
                        continue

                    nk = _norm_key(label_text)
                    if not nk or nk in option_xpath_map:
                        continue

                    xp = _best_xpath_for_element(driver, opt)
                    if not xp:
                        continue

                    option_xpath_map[nk] = xp
                    options.append(label_text)
                except Exception:
                    continue

            if len(options) < 2 or len(option_xpath_map) < 2:
                continue

            container_id = ""
            try:
                q_containers = container.find_elements(By.CSS_SELECTOR, ".multi-select-container[id]")
                if q_containers:
                    container_id = (q_containers[0].get_attribute("id") or "").strip()
            except Exception:
                container_id = ""

            if not container_id:
                container_id = f"idx{idx}_{zlib.crc32(question.encode('utf-8')):x}"

            group_key = f"custom_testid_multi_select:checkbox:{container_id}"
            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "checkbox",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                    "custom_testid_multi_select": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "checkbox",
                    "options": options,
                    "max_select": _compute_max_select("checkbox", options),
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "custom_testid_multi_select": True,
                    },
                }
            )
        except Exception:
            continue

    return blocks



# ================================================================================
# CLOUDRESEARCH SENTRY - VUE.JS BLOCKS
# ================================================================================

def _extract_cloudresearch_sentry_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """CloudResearch/Sentry : extraction DOM-only des questions ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â  choix unique.

    Plateforme CloudResearch utilise Vue.js avec des divs role="button" comme boutons radio.
    Structure DOM typique:
    - Conteneur: #sentry ou .cr-question-card
    - Question: h1[id*="QuestionLabel"] ou h1.question-prompt
    - Options: .choice-option[role="button"] avec texte dans .cr-ct ou div[class*="answer-choice"]

    Gate strict: n'active l'extracteur que si le pattern CloudResearch est dÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©tectÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©
    (.choice-option[role="button"] + #sentry ou .cr-question-card).
    """

    frame_chain = list(frame_chain or [])

    # Gate strict: CloudResearch/Sentry pattern
    try:
        # Pattern spécifique
        sentry_marker = driver.find_elements(By.CSS_SELECTOR, "#sentry, .cr-question-card")
        choice_btns = driver.find_elements(By.CSS_SELECTOR, ".choice-option[role='button']")
        if not sentry_marker or not choice_btns:
            return []
    except Exception:
        return []

    blocks: list[dict] = []

    try:
        # Extraction de la question
        question = ""

        # Pattern spécifique
        question_selectors = [
            "h1[class*='question-prompt']",
            "h1[id*='QuestionLabel']",
            "h1[id*='questionLabel']",
            "h1.cr-custom-qt",
            ".cr-question-card h1",
            "#mainContent h1",
        ]
        for sel in question_selectors:
            try:
                q_els = driver.find_elements(By.CSS_SELECTOR, sel)
                for q_el in q_els:
                    try:
                        if not q_el.is_displayed():
                            continue
                        t = _norm(q_el.text or q_el.get_attribute("innerText") or "")
                        if t and len(t) >= 5:
                            question = t
                            break
                    except Exception:
                        continue
                if question:
                    break
            except Exception:
                continue

        if not question:
            return []

        # Pattern spécifique
        options: list[str] = []
        option_xpath_map: dict[str, str] = {}

        for btn in choice_btns:
            try:
                # Pattern spécifique
                try:
                    if not btn.is_displayed():
                        continue
                except Exception:
                    pass

                # Extraire le texte de l'option
                # Pattern spécifique
                opt_text = ""

                # 1) Chercher dans .cr-ct (CloudResearch content)
                try:
                    cr_ct = btn.find_elements(By.CSS_SELECTOR, ".cr-ct, [class*='answer-choice']")
                    if cr_ct:
                        opt_text = _norm(cr_ct[0].text or cr_ct[0].get_attribute("innerText") or "")
                except Exception:
                    pass

                # 2) Fallback: texte du bouton sans les SVG
                if not opt_text:
                    try:
                        # Pattern spécifique
                        text_divs = btn.find_elements(By.CSS_SELECTOR, "div:not(:has(svg))")
                        for td in text_divs:
                            t = _norm(td.text or "")
                            if t and len(t) >= 1:
                                opt_text = t
                                break
                    except Exception:
                        pass

                # 3) Dernier recours: texte direct du bouton
                if not opt_text:
                    raw = _norm(btn.text or btn.get_attribute("innerText") or "")
                    if raw:
                        opt_text = raw

                if not opt_text or len(opt_text) < 1:
                    continue

                # Pattern spécifique
                opt_lc = _norm_lc(opt_text)
                if opt_lc in {"next", "suivant", "continue", "continuer", "please select"}:
                    continue

                # XPath stable pour ce bouton
                # Pattern spécifique
                xp = ""
                try:
                    tabidx = (btn.get_attribute("tabindex") or "").strip()
                    if tabidx:
                        xp = f"(//*[contains(@class,'choice-option') and @role='button' and @tabindex='{tabidx}'])[1]"
                except Exception:
                    pass

                # Fallback: XPath absolu
                if not xp:
                    xp = _best_xpath_for_element(driver, btn)

                if not xp:
                    continue

                nk = _norm_key(opt_text)
                if nk in option_xpath_map:
                    continue

                option_xpath_map[nk] = xp
                options.append(opt_text)

            except Exception:
                continue

        if len(options) < 2 or not option_xpath_map:
            return []

        # Pattern spécifique
        group_key = f"cloudresearch_sentry:radio:q:{_norm_key(question[:50])}"
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
                "cloudresearch_sentry": True,
            },
        )

        blocks.append(
            {
                "question": question,
                "itype": "radio",
                "options": options,
                "max_select": 1,
                "target_id": target_id,
                "context": {"kind": "group", "group_key": group_key, "cloudresearch_sentry": True},
            }
        )

    except Exception:
        pass

    return blocks


def _extract_purespectrum_mobile_date_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """PureSpectrum mobile date picker: 2 roues (mois/année) en `ps-select-scroll`.

    Objectif: éviter un résultat vide quand la question date n'expose pas d'<input>/<select>
    natif (UI custom Angular).
    """
    frame_chain = list(frame_chain or [])

    try:
        date_questions = driver.find_elements(By.CSS_SELECTOR, "ps-date-question")
    except Exception:
        return []

    if not date_questions:
        return []

    blocks: list[dict] = []

    for date_q in date_questions:
        try:
            # Gate strict: uniquement la version mobile avec roues.
            columns = date_q.find_elements(By.CSS_SELECTOR, "ps-date-picker-mobile ps-select-scroll")
            if len(columns) < 2:
                continue

            question = ""
            for sel in [".question-title", "[psquestiontitle]", "header [role='heading']"]:
                try:
                    q_els = date_q.find_elements(By.CSS_SELECTOR, sel)
                    for q_el in q_els:
                        txt = _norm(q_el.text or q_el.get_attribute("innerText") or "")
                        if txt and len(txt) >= 3:
                            question = txt
                            break
                    if question:
                        break
                except Exception:
                    continue

            if not question:
                continue

            for col_idx, col in enumerate(columns, start=1):
                options: list[str] = []
                option_xpath_map: dict[str, str] = {}

                try:
                    slides = col.find_elements(By.CSS_SELECTOR, ".select-scroll-slide")
                except Exception:
                    slides = []

                for s in slides:
                    try:
                        txt = _norm(s.text or s.get_attribute("innerText") or "")
                        if not txt:
                            continue
                        nk = _norm_key(txt)
                        if nk in option_xpath_map:
                            continue
                        xp = (
                            f"(//ps-date-question//ps-date-picker-mobile//ps-select-scroll)[{col_idx}]"
                            f"//*[contains(@class,'select-scroll-slide') and normalize-space(.)={_xpath_literal(txt)}][1]"
                        )
                        option_xpath_map[nk] = xp
                        options.append(txt)
                    except Exception:
                        continue

                if len(options) < 2:
                    continue

                # Etiquette colonne: années vs mois (heuristique simple, robuste FR/EN).
                numeric_count = sum(1 for o in options if o.isdigit() and len(o) == 4)
                field_hint = "Année" if numeric_count >= max(2, len(options) // 3) else "Mois"
                question_col = f"{question} ({field_hint})"

                group_key = f"purespectrum_mobile_date:{col_idx}:{_norm_key(question)}"
                target_id = make_target_id("group", group_key, question_col)

                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "itype": "radio",
                        "group_key": group_key,
                        "question": question_col,
                        "option_xpath_map": option_xpath_map,
                        "frame_chain": frame_chain,
                        "purespectrum_mobile_date": True,
                    },
                )

                blocks.append(
                    {
                        "question": question_col,
                        "itype": "radio",
                        "options": options,
                        "max_select": 1,
                        "target_id": target_id,
                        "context": {
                            "kind": "group",
                            "group_key": group_key,
                            "purespectrum_mobile_date": True,
                        },
                    }
                )
        except Exception:
            continue

    return blocks


def _extract_purespectrum_date_dropdown_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """PureSpectrum date picker desktop: `ps-select-dropdown[data-e2e=month|year]`.

    Garde-fous DOM (additif, non provider-based):
      - conteneur `ps-date-question[qualificationid]`
      - présence de dropdowns `data-e2e="month"|"year"`
      - options explicites `button[ngbdropdownitem][data-e2e]`
    """
    frame_chain = list(frame_chain or [])

    try:
        date_questions = driver.find_elements(By.CSS_SELECTOR, "ps-date-question[qualificationid]")
    except Exception:
        return []

    if not date_questions:
        return []

    blocks: list[dict] = []

    for date_q in date_questions:
        try:
            dropdowns = date_q.find_elements(By.CSS_SELECTOR, "ps-select-dropdown[data-e2e='month'], ps-select-dropdown[data-e2e='year']")
        except Exception:
            dropdowns = []

        if not dropdowns:
            continue

        question = ""
        for sel in [".question-title", "[psquestiontitle]", "header [role='heading']"]:
            try:
                q_els = date_q.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                q_els = []
            for q_el in q_els:
                try:
                    txt = _norm(q_el.text or q_el.get_attribute("innerText") or "")
                except Exception:
                    txt = ""
                if txt and len(txt) >= 3:
                    question = txt
                    break
            if question:
                break

        if not question:
            continue

        for dd in dropdowns:
            try:
                dd_kind = _norm_lc(dd.get_attribute("data-e2e") or "")
            except Exception:
                dd_kind = ""
            if dd_kind not in {"month", "year"}:
                continue

            try:
                toggle = dd.find_element(By.CSS_SELECTOR, "button.dropdown-toggle")
                dropdown_toggle_xpath = _best_xpath_for_element(driver, toggle)
            except Exception:
                continue

            option_xpath_map: dict[str, str] = {}
            options: list[str] = []

            try:
                option_btns = dd.find_elements(By.CSS_SELECTOR, "button[ngbdropdownitem][data-e2e]")
            except Exception:
                option_btns = []

            for opt in option_btns:
                try:
                    opt_code = (opt.get_attribute("data-e2e") or "").strip()
                    opt_label = _norm(opt.text or opt.get_attribute("innerText") or "")
                    if not opt_code and not opt_label:
                        continue
                    xp = _best_xpath_for_element(driver, opt)
                    if not xp:
                        continue

                    if opt_label:
                        nk_label = _norm_key(opt_label)
                        if nk_label and nk_label not in option_xpath_map:
                            option_xpath_map[nk_label] = xp
                        if opt_label not in options:
                            options.append(opt_label)

                    if opt_code:
                        nk_code = _norm_key(opt_code)
                        if nk_code and nk_code not in option_xpath_map:
                            option_xpath_map[nk_code] = xp
                except Exception:
                    continue

            if len(options) < 2:
                continue

            field_hint = "Mois" if dd_kind == "month" else "Année"
            question_col = f"{question} ({field_hint})"
            group_key = f"purespectrum_date_dropdown:{dd_kind}:{_norm_key(question)}"
            target_id = make_target_id("group", group_key, question_col)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": question_col,
                    "option_xpath_map": option_xpath_map,
                    "dropdown_toggle_xpath": dropdown_toggle_xpath,
                    "frame_chain": frame_chain,
                    "purespectrum_date_dropdown": True,
                },
            )

            blocks.append(
                {
                    "question": question_col,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "purespectrum_date_dropdown": True,
                    },
                }
            )

    return blocks


def _extract_ps_select_dropdown_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extrait les dropdowns custom `ps-select-dropdown` basés sur ng-bootstrap.

    Garde-fous DOM (additif, non provider-based):
      - présence d'un `ps-date-question`
      - dropdowns `ps-select-dropdown` ayant un trigger `button[ngbdropdowntoggle]`
      - options `button[ngbdropdownitem][role='option']` présentes dans le DOM
    """
    frame_chain = list(frame_chain or [])

    try:
        date_questions = driver.find_elements(By.CSS_SELECTOR, "ps-date-question")
    except Exception:
        return []

    if not date_questions:
        return []

    blocks: list[dict] = []

    for date_q in date_questions:
        try:
            dropdowns = date_q.find_elements(
                By.CSS_SELECTOR,
                "ps-select-dropdown[data-e2e='month'], ps-select-dropdown[data-e2e='year']",
            )
        except Exception:
            dropdowns = []

        if not dropdowns:
            continue

        question = ""
        for sel in [".question-title", "[psquestiontitle]", "header [role='heading']"]:
            try:
                q_els = date_q.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                q_els = []
            for q_el in q_els:
                txt = _norm(q_el.text or q_el.get_attribute("innerText") or "")
                if txt and len(txt) >= 3:
                    question = txt
                    break
            if question:
                break

        if not question:
            continue

        for dd in dropdowns:
            dd_kind = _norm_lc(dd.get_attribute("data-e2e") or "")
            if dd_kind not in {"month", "year"}:
                continue

            try:
                toggle = dd.find_element(By.CSS_SELECTOR, "button[ngbdropdowntoggle]")
                dropdown_toggle_xpath = _best_xpath_for_element(driver, toggle)
            except Exception:
                continue

            try:
                option_btns = dd.find_elements(By.CSS_SELECTOR, "button[ngbdropdownitem][role='option']")
            except Exception:
                option_btns = []

            option_xpath_map: dict[str, str] = {}
            options: list[str] = []

            for opt in option_btns:
                try:
                    opt_label = _norm(opt.text or opt.get_attribute("innerText") or "")
                    if not opt_label:
                        continue

                    opt_xpath = _best_xpath_for_element(driver, opt)
                    if not opt_xpath:
                        continue

                    nk = _norm_key(opt_label)
                    if not nk or nk in option_xpath_map:
                        continue

                    option_xpath_map[nk] = opt_xpath
                    options.append(opt_label)
                except Exception:
                    continue

            if len(options) < 2:
                continue

            scope_hint = f"data-e2e={dd_kind}"
            group_key = f"ps_select_dropdown:{dd_kind}:{_norm_key(question)}"
            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "select",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "dropdown_toggle_xpath": dropdown_toggle_xpath,
                    "frame_chain": frame_chain,
                    "scope_hint": scope_hint,
                    "ps_select_dropdown": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "select",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "scope_hint": scope_hint,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "scope_hint": scope_hint,
                        "ps_select_dropdown": True,
                    },
                }
            )

        if len(blocks) >= 2:
            # Cette structure date est normalement bornée à mois/année.
            break

    return blocks


def _extract_collapsed_section_radio_rows(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait les matrices radio rendues en accordéon via paires:
      - div[data-ref-id='section-header'][role='button']
      - div[data-ref-id='section-content']

    Garde-fous DOM (additif, non provider-based):
      - au moins 3 headers dans un même .section-container
      - chaque header possède un data-id
      - les contenus contiennent des radios name='.../...'
    """
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.section-container")
    except Exception:
        return blocks

    for container in containers:
        try:
            headers = container.find_elements(By.CSS_SELECTOR, "div[data-ref-id='section-header'][role='button'][data-id]")
            if len(headers) < 3:
                continue

            contents = container.find_elements(By.CSS_SELECTOR, "div[data-ref-id='section-content']")
            if len(contents) != len(headers):
                continue

            question = ""
            try:
                q_nodes = container.find_elements(
                    By.XPATH,
                    "ancestor::*[@data-ref-id][1]//*[contains(@class,'question-caption')][1]",
                )
                if q_nodes:
                    question = _norm(q_nodes[0].text or q_nodes[0].get_attribute("innerText") or "")
            except Exception:
                question = ""

            candidate_rows: list[dict[str, Any]] = []
            named_group_hits = 0

            for idx, header in enumerate(headers):
                row_label = _norm(header.text or header.get_attribute("innerText") or "")
                if not row_label:
                    continue

                panel = contents[idx]
                radios = panel.find_elements(By.CSS_SELECTOR, "input[type='radio'][name]")
                if len(radios) < 2:
                    continue

                first_name = (radios[0].get_attribute("name") or "").strip()
                if "/" in first_name:
                    named_group_hits += 1

                option_xpath_map: dict[str, str] = {}
                options: list[str] = []

                for radio in radios:
                    try:
                        rname = (radio.get_attribute("name") or "").strip()
                        rval = (radio.get_attribute("value") or "").strip()
                        if not rname or not rval:
                            continue

                        label_txt = ""
                        try:
                            label = radio.find_element(By.XPATH, "ancestor::label[1]")
                            label_txt = _norm(label.text or label.get_attribute("innerText") or "")
                        except Exception:
                            label_txt = ""
                        if not label_txt:
                            continue

                        key = _norm_key(label_txt)
                        if key in option_xpath_map:
                            continue

                        xp = (
                            f"(//input[@type='radio' and @name={_xpath_literal(rname)} and @value={_xpath_literal(rval)}]"
                            f"/ancestor::label[1] | "
                            f"//input[@type='radio' and @name={_xpath_literal(rname)} and @value={_xpath_literal(rval)}])[1]"
                        )
                        option_xpath_map[key] = xp
                        options.append(label_txt)
                    except Exception:
                        continue

                if len(options) < 2:
                    continue

                header_xpath = _best_xpath_for_element(driver, header)
                group_name = (radios[0].get_attribute("name") or "").strip()
                group_key = f"radio:name:{group_name}"
                row_question = _norm(f"{question} {row_label}" if question else row_label)
                if not row_question:
                    continue

                target_id = make_target_id("group", group_key, row_question)

                payload = {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": row_question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain or [],
                }
                if header_xpath:
                    payload["pre_click_xpaths"] = [header_xpath]

                register_target(target_id, payload)

                candidate_rows.append(
                    {
                        "question": row_question,
                        "itype": "radio",
                        "options": options,
                        "max_select": 1,
                        "target_id": target_id,
                        "context": {
                            "kind": "group",
                            "group_key": group_key,
                        },
                    }
                )

            if len(candidate_rows) >= 3 and named_group_hits >= 2:
                blocks.extend(candidate_rows)
        except Exception:
            continue

    if blocks:
        log_debug("[DOM_SECTION_MATRIX]", f"rows_extracted={len(blocks)}")

    return blocks


def _extract_jqm_lrw_collapsible_radio_rows(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extrait les blocs radio LRW/jQuery Mobile rendus en accordéon.

    Gate DOM strict (additif):
    - `div.collapsible-container.ui-collapsible-set`
    - au moins 2 `div.collapsible-button-group`
    - chaque ligne expose un `input[type='radio'][name]` + labels textuels
    """
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.collapsible-container.ui-collapsible-set")
    except Exception:
        return blocks

    for container in containers:
        try:
            rows = container.find_elements(By.XPATH, "./div[contains(@class,'collapsible-button-group')]")
        except Exception:
            rows = []
        if len(rows) < 2:
            continue

        main_question = ""
        try:
            wrappers = container.find_elements(By.XPATH, "ancestor::*[contains(@class,'content-wrapper')][1]")
            if wrappers:
                q_nodes = wrappers[0].find_elements(
                    By.XPATH,
                    ".//span[contains(@class,'mrQuestionText')][not(ancestor::div[contains(@class,'collapsible-container')])]",
                )
                for node in q_nodes:
                    txt = _norm(node.text or node.get_attribute("innerText") or "")
                    if txt and len(txt) >= 8:
                        main_question = txt
                        break
        except Exception:
            main_question = ""

        candidate_blocks: list[dict] = []
        for row in rows:
            try:
                header = ""
                try:
                    h = row.find_element(
                        By.CSS_SELECTOR,
                        "div.ui-collapsible-heading button.ui-collapsible-heading-toggle span.mrQuestionText",
                    )
                    header = _norm(h.text or h.get_attribute("innerText") or "")
                except Exception:
                    header = ""
                if not header:
                    continue

                try:
                    radios = row.find_elements(By.CSS_SELECTOR, "div.ui-collapsible-content input[type='radio'][name]")
                except Exception:
                    radios = []
                if len(radios) < 2:
                    continue

                group_name = _norm_lc((radios[0].get_attribute("name") or "").strip())
                if not group_name:
                    continue

                options: list[str] = []
                option_xpath_map: dict[str, str] = {}
                for radio in radios:
                    try:
                        radio_name = (radio.get_attribute("name") or "").strip()
                        if _norm_lc(radio_name) != group_name:
                            continue
                        radio_id = (radio.get_attribute("id") or "").strip()
                        value = (radio.get_attribute("value") or "").strip()

                        label_txt = ""
                        if radio_id:
                            try:
                                label = row.find_element(By.CSS_SELECTOR, f"label[for='{radio_id}']")
                                label_txt = _norm(label.text or label.get_attribute("innerText") or "")
                            except Exception:
                                label_txt = ""
                        if not label_txt:
                            continue

                        norm_key = _norm_key(label_txt)
                        if norm_key in option_xpath_map:
                            continue

                        xp = (
                            f"(//input[@type='radio' and @name={_xpath_literal(radio_name)}"
                            + (f" and @value={_xpath_literal(value)}" if value else "")
                            + "]/ancestor::label[1] | "
                            f"//input[@type='radio' and @name={_xpath_literal(radio_name)}"
                            + (f" and @value={_xpath_literal(value)}" if value else "")
                            + "])[1]"
                        )
                        option_xpath_map[norm_key] = xp
                        options.append(label_txt)
                    except Exception:
                        continue

                if len(options) < 2:
                    continue

                full_question = _norm(f"{main_question} {header}" if main_question else header)
                if not full_question:
                    continue

                group_key = f"radio:name:{group_name}"
                target_id = make_target_id("group", group_key, full_question)

                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "itype": "radio",
                        "group_key": group_key,
                        "question": full_question,
                        "option_xpath_map": option_xpath_map,
                        "frame_chain": frame_chain or [],
                    },
                )

                candidate_blocks.append(
                    {
                        "question": full_question,
                        "itype": "radio",
                        "options": options,
                        "max_select": 1,
                        "target_id": target_id,
                        "context": {"kind": "group", "group_key": group_key},
                    }
                )
            except Exception:
                continue

        if len(candidate_blocks) >= 2:
            blocks.extend(candidate_blocks)

    if blocks:
        log_debug("[DOM_JQM_COLLAPSIBLE]", f"rows_extracted={len(blocks)}")

    return blocks


def _extract_jqm_lrw_collapsible_checkbox_rows(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extrait les blocs checkbox LRW/jQuery Mobile rendus en accordéon.

    Gate DOM strict (additif):
    - `div.collapsible-container.ui-collapsible-set`
    - au moins 2 `div.collapsible-button-group`
    - chaque ligne expose un `input[type='checkbox'][name]` + labels textuels
    """
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.collapsible-container.ui-collapsible-set")
    except Exception:
        return blocks

    for container in containers:
        try:
            rows = container.find_elements(By.XPATH, "./div[contains(@class,'collapsible-button-group')]")
        except Exception:
            rows = []
        if len(rows) < 2:
            continue

        candidate_blocks: list[dict] = []
        for row in rows:
            try:
                try:
                    heading_span = row.find_element(
                        By.CSS_SELECTOR,
                        "div.ui-collapsible-heading button.ui-collapsible-heading-toggle span.mrQuestionText",
                    )
                    header = _norm(heading_span.text or heading_span.get_attribute("innerText") or "")
                except Exception:
                    header = ""

                # jQuery Mobile ajoute parfois un texte annexe de statut:
                # "click to expand contents".
                header = _norm(re.sub(r"\bclick to expand contents\b", "", header, flags=re.IGNORECASE))
                if not header:
                    continue

                try:
                    checkboxes = row.find_elements(By.CSS_SELECTOR, "div.ui-collapsible-content input[type='checkbox'][name]")
                except Exception:
                    checkboxes = []
                if len(checkboxes) < 2:
                    continue

                options: list[str] = []
                option_xpath_map: dict[str, str] = {}

                for checkbox in checkboxes:
                    try:
                        c_name = (checkbox.get_attribute("name") or "").strip()
                        c_id = (checkbox.get_attribute("id") or "").strip()
                        c_value = (checkbox.get_attribute("value") or "").strip()
                        if not c_name or not c_id:
                            continue

                        try:
                            label = row.find_element(By.CSS_SELECTOR, f"label[for='{c_id}']")
                            label_txt = _norm(label.text or label.get_attribute("innerText") or "")
                        except Exception:
                            label_txt = ""
                        if not label_txt:
                            continue

                        norm_key = _norm_key(label_txt)
                        if norm_key in option_xpath_map:
                            continue

                        xp = (
                            f"(//input[@type='checkbox' and @name={_xpath_literal(c_name)}"
                            + (f" and @value={_xpath_literal(c_value)}" if c_value else "")
                            + "]/ancestor::label[1] | "
                            f"//input[@type='checkbox' and @name={_xpath_literal(c_name)}"
                            + (f" and @value={_xpath_literal(c_value)}" if c_value else "")
                            + "])[1]"
                        )
                        option_xpath_map[norm_key] = xp
                        options.append(label_txt)
                    except Exception:
                        continue

                if len(options) < 2:
                    continue

                group_key = f"checkbox:jqm_collapsible:{_norm_key(header)}"
                target_id = make_target_id("group", group_key, header)

                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "itype": "checkbox",
                        "group_key": group_key,
                        "question": header,
                        "option_xpath_map": option_xpath_map,
                        "frame_chain": frame_chain or [],
                    },
                )

                candidate_blocks.append(
                    {
                        "question": header,
                        "itype": "checkbox",
                        "options": options,
                        "max_select": _compute_max_select("checkbox", options, header),
                        "target_id": target_id,
                        "context": {"kind": "group", "group_key": group_key},
                    }
                )
            except Exception:
                continue

        if len(candidate_blocks) >= 2:
            blocks.extend(candidate_blocks)

    if blocks:
        log_debug("[DOM_JQM_COLLAPSIBLE]", f"checkbox_rows_extracted={len(blocks)}")

    return blocks


def _extract_decipher_clickable_ranking_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Decipher clickable ranking text tool (`#customToolArea` + `.customItem`).

    Gate DOM strict:
    - `div#customToolArea div#itemArea .customItem` (>=2)
    - au moins un `.customRank` (UI de ranking)

    Le champ "Autre - préciser" (input texte inline) reste auxiliaire et ne doit
    pas empêcher l'extraction des options visibles.
    """
    frame_chain = list(frame_chain or [])

    try:
        item_area = driver.find_elements(By.CSS_SELECTOR, "#customToolArea #itemArea")
    except Exception:
        item_area = []
    if not item_area:
        return []

    container = item_area[0]

    try:
        rank_nodes = container.find_elements(By.CSS_SELECTOR, ".customItem .customRank")
    except Exception:
        rank_nodes = []
    if not rank_nodes:
        return []

    try:
        items = container.find_elements(By.CSS_SELECTOR, ".customItem")
    except Exception:
        items = []
    if len(items) < 2:
        return []

    question = ""
    for sel in ("#question_text_Q4", "h1.question-text", "h1", "h2"):
        try:
            q_candidates = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            q_candidates = []
        for q in q_candidates:
            txt = _norm(q.text or q.get_attribute("innerText") or "")
            if txt and len(txt) >= 8:
                question = txt
                break
        if question:
            break
    if not question:
        return []

    options: list[str] = []
    option_xpath_map: dict[str, str] = {}
    for item in items:
        try:
            label_text = ""
            statement_id = ""
            try:
                statement = item.find_element(By.CSS_SELECTOR, ".customStatement")
                statement_id = (statement.get_attribute("id") or "").strip()
                label_text = _norm(statement.text or statement.get_attribute("innerText") or "")
            except Exception:
                pass

            if not label_text:
                continue

            # Nettoyage cas "Autre - préciser <input>"
            label_text = _norm(re.sub(r"\s+", " ", label_text))
            if not label_text:
                continue

            nk = _norm_key(label_text)
            if not nk or nk in option_xpath_map:
                continue

            xp = ""
            if statement_id:
                xp = f"//*[@id={_xpath_literal(statement_id)}]"
            if not xp:
                try:
                    xp = _best_xpath_for_element(driver, item) or ""
                except Exception:
                    xp = ""
            if not xp:
                continue

            options.append(label_text)
            option_xpath_map[nk] = xp
        except Exception:
            continue

    if len(options) < 2 or len(option_xpath_map) < 2:
        return []

    max_select = 3
    try:
        script_text = driver.execute_script(
            """
            const scripts = Array.from(document.querySelectorAll('script'));
            for (const s of scripts) {
              const t = (s.textContent || '');
              if (/maxNrAnswer\s*:\s*\d+/i.test(t)) return t;
            }
            return '';
            """
        )
        m = re.search(r"maxNrAnswer\s*:\s*(\d+)", str(script_text or ""), flags=re.IGNORECASE)
        if m:
            max_select = max(1, int(m.group(1)))
    except Exception:
        pass

    group_key = f"decipher_clickable_ranking:{_norm_key(question)}"
    target_id = make_target_id("group", group_key, question)

    register_target(
        target_id,
        {
            "kind": "group",
            "itype": "checkbox",
            "group_key": group_key,
            "question": question,
            "option_xpath_map": option_xpath_map,
            "frame_chain": frame_chain,
            "decipher_clickable_ranking": True,
        },
    )

    return [
        {
            "question": question,
            "itype": "checkbox",
            "options": options,
            "max_select": max_select,
            "target_id": target_id,
            "context": {
                "kind": "group",
                "group_key": group_key,
                "decipher_clickable_ranking": True,
            },
        }
    ]
