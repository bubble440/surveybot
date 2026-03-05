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
from typing import List, Dict, Any
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

            for th in ths[1:]:
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
    """CMIX (survey.cmix.com) : extraction DOM-only des questions radio.

    Bug visé (capture CMIX): la page affiche des radios (ex: politique de confidentialité)
    mais l'extraction générique peut retourner 0 question_blocks, déclenchant le fallback
    CTA-only et sautant la question.

    Stratégique d'extraction ciblée:
    - activation stricte uniquement si le markup CMIX (.cm-question-wrapper + .cm-radio-label)
    - 1 bloc par groupe radio (name) dans un wrapper
    - mapping option->xpath en privilégiant le label texte (.cm-radio-label) plutot que le label "bouton" (.cm-radio-input)
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

            if not inputs:
                continue

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

    try:
        checkboxes = driver.find_elements(
            By.CSS_SELECTOR,
            "#consentContainer25 input[type='checkbox'], "
            "[id*='consentContainer'] input[type='checkbox'], "
            ".river-sampling-privacy-policy input[type='checkbox'], "
            "input[type='checkbox'][id*='consentCheckbox'], "
            "input[type='checkbox'][name*='consentCheckbox'], "
            "input[type='checkbox'][name*='consentContainer']",
        )
    except Exception:
        return []

    if len(checkboxes) != 1:
        return []

    cb = checkboxes[0]

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
        question = "Politique de confidentialité / consentement"

    group_base = cb_name or cb_id
    if not group_base:
        return []

    group_key = f"checkbox:name:{_norm_lc(group_base)}"
    target_id = make_target_id("group", group_key, question)

    if cb_id:
        id_lit = _xpath_literal(cb_id)
        option_xpath = f"(//label[@for={id_lit}] | //*[@id={id_lit}])[1]"
    else:
        name_lit = _xpath_literal(cb_name)
        option_xpath = f"(//input[@type='checkbox' and @name={name_lit}]/ancestor::label[1] | //input[@type='checkbox' and @name={name_lit}])[1]"

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
