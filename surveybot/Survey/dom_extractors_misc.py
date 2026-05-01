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
import json, os, re, time, zlib
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

    Problème : les <input type=radio> des panels repliés ne sont pas "visibles" (height=0, visibility:hidden)
    => notre extraction générique (qui filtre sur visibilité) ne sort que la/les lignes déjà ouvertes.

    Stratégie DOM-only, prédictible:
    - détecter les panels mobile-matrix-question
    - créer 1 bloc radio par ligne (header = libellé de la ligne)
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
        Angular Material: le contenu (radios) peut être rendu via *ngIf uniquement quand le panel est ouvert.
        On ouvre le panel (1 fois) puis on attend brièvement que les radios apparaissent.
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
            question = f"{global_q} — {row_label}" if global_q else row_label
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

    Problème:
    - les options ne sont pas des <input type=checkbox>, donc l'extraction générique (radios/checkbox) ne voit rien.
    - le seul <input> présent est souvent l'option "Autre (veuillez préciser)" => on extrait une fausse question.

    Stratégie DOM-only, stricte et non-invasive:
    - ne s'active que si on détecte un <app-survey-page> ET des <mat-selection-list> sous appQuestionContainer
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
# ASKIA — RESPONSIVE TABLE CHECKBOX MATRIX (adc-responsiveTable)
# ================================================================================

def _extract_askia_responsive_table_checkbox_rows(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction DOM-only des matrices Askia ResponsiveTable checkbox.

    Gate DOM strict et additif :
    - form[name="FormAskia"]
    - div.adc-responsiveTable > table avec thead th.responsesitems
    - tbody tr.askiarow[data-id] avec un libellé de ligne et >=2 checkboxes

    Produit 1 bloc checkbox par ligne de matrice. Ne dépend pas du provider :
    l'activation se fait uniquement sur la structure DOM observable du widget.
    """
    frame_chain = list(frame_chain or [])

    try:
        if not driver.find_elements(By.CSS_SELECTOR, "form[name='FormAskia']"):
            return []
    except Exception:
        return []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.adc-responsiveTable")
    except Exception:
        return []

    if not containers:
        return []

    blocks: list[dict] = []

    for cidx, container in enumerate(containers[:10], start=1):
        try:
            try:
                table = container.find_element(By.CSS_SELECTOR, "table")
            except Exception:
                continue

            try:
                header_nodes = table.find_elements(By.CSS_SELECTOR, "thead th.responsesitems")
            except Exception:
                header_nodes = []

            col_headers: list[str] = []
            for h in header_nodes:
                try:
                    txt = _norm(h.text or h.get_attribute("innerText") or h.get_attribute("textContent") or "")
                except Exception:
                    txt = ""
                if txt and txt not in col_headers:
                    col_headers.append(txt)

            if len(col_headers) < 2:
                continue

            try:
                rows = table.find_elements(By.CSS_SELECTOR, "tbody tr.askiarow[data-id]")
            except Exception:
                rows = []

            if len(rows) < 2:
                continue

            matrix_question = ""
            try:
                q_nodes = driver.find_elements(
                    By.CSS_SELECTOR,
                    "td.askia-question-label, td[class*='askia-caption'], td[class*='askia-question-label']",
                )
                for qn in q_nodes:
                    txt = _norm(qn.text or qn.get_attribute("innerText") or "")
                    if txt:
                        # Le span d'instruction (#indic) est dans le même td : garder le titre seul.
                        matrix_question = _norm(txt.splitlines()[0])
                        break
            except Exception:
                matrix_question = ""

            for ridx, row in enumerate(rows[:40], start=1):
                try:
                    row_id = (row.get_attribute("data-id") or "").strip()

                    row_label = ""
                    try:
                        label_nodes = row.find_elements(By.CSS_SELECTOR, "td.respLabel span.items, td.respLabel")
                    except Exception:
                        label_nodes = []
                    for node in label_nodes:
                        txt = _norm(node.text or node.get_attribute("innerText") or "")
                        if txt:
                            row_label = txt
                            break
                    if not row_label:
                        continue

                    try:
                        boxes = row.find_elements(By.CSS_SELECTOR, "td.response input[type='checkbox'][name]")
                    except Exception:
                        boxes = []
                    if len(boxes) < 2:
                        continue

                    options: list[str] = []
                    option_xpath_map: dict[str, str] = {}
                    num_options = min(len(col_headers), len(boxes))
                    if num_options < 2:
                        continue

                    for idx in range(num_options):
                        opt_text = col_headers[idx]
                        box = boxes[idx]
                        box_id = (box.get_attribute("id") or "").strip()
                        box_name = (box.get_attribute("name") or "").strip()
                        box_value = (box.get_attribute("value") or "").strip()

                        if box_id:
                            xp = f"(//*[@id={_xpath_literal(box_id)}])[1]"
                        elif box_name and box_value:
                            xp = (
                                f"(//input[@type='checkbox' and @name={_xpath_literal(box_name)} "
                                f"and @value={_xpath_literal(box_value)}])[1]"
                            )
                        else:
                            xp = _best_xpath_for_element(driver, box)

                        if not xp:
                            continue

                        nk = _norm_key(opt_text)
                        if not nk or nk in option_xpath_map:
                            continue
                        option_xpath_map[nk] = xp
                        options.append(opt_text)

                    if len(options) < 2 or not option_xpath_map:
                        continue

                    question = f"{matrix_question} {row_label}" if matrix_question else row_label
                    group_key = f"askia_responsive_table_checkbox:{row_id or cidx}:{ridx}"
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
                            "matrix_question": matrix_question,
                            "matrix_row": row_label,
                            "matrix_columns": col_headers,
                            "askia_responsive_table_checkbox": True,
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
                                "matrix_question": matrix_question,
                                "matrix_row": row_label,
                                "matrix_columns": col_headers,
                                "askia_responsive_table_checkbox": True,
                            },
                        }
                    )
                except Exception:
                    continue
        except Exception:
            continue

    if blocks:
        log_debug("[DOM_ASKIA_RESP_TABLE]", f"blocks={len(blocks)}")

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
            if not matrix_question:
                try:
                    lbl = driver.find_element(
                        By.CSS_SELECTOR,
                        "td[class*='askia-question-label'], td[class*='askia-caption']",
                    )
                    matrix_question = _norm(lbl.text or lbl.get_attribute("innerText") or "")
                except Exception:
                    pass

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

    # Pattern privacy-policy-wrap (Tobii/sticky.ai iframe consent)
    # Garde-fou: div.privacy-policy-wrap unique + checkbox unique, sans form requis.
    if cb is None:
        try:
            ppw_checkboxes = driver.find_elements(
                By.CSS_SELECTOR, "div.privacy-policy-wrap input[type='checkbox']"
            )
        except Exception:
            ppw_checkboxes = []
        if len(ppw_checkboxes) == 1:
            cb = ppw_checkboxes[0]

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

    # Pattern privacy-policy-wrap: bouton disabled sans <form> requis.
    if not has_accept_cta:
        try:
            ppw = driver.find_elements(By.CSS_SELECTOR, "div.privacy-policy-wrap")
            if ppw:
                disabled_btns = driver.find_elements(
                    By.CSS_SELECTOR,
                    "button[disabled], input[type='submit'][disabled], input[type='button'][disabled]",
                )
                has_accept_cta = len(disabled_btns) > 0
        except Exception:
            pass

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

    # Fallback label pour privacy-policy-wrap: texte adjacent (SPA sans label[for]).
    if not label_txt:
        try:
            ppw = driver.find_elements(By.CSS_SELECTOR, "div.privacy-policy-wrap")
            if ppw:
                for xpath in ("following-sibling::*[1]", "parent::*/following-sibling::*[1]"):
                    try:
                        el = cb.find_element(By.XPATH, xpath)
                        txt = _norm(el.text or el.get_attribute("innerText") or "")
                        if txt:
                            label_txt = txt
                            break
                    except Exception:
                        continue
        except Exception:
            pass

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


def _extract_confirmit_wix_fieldset_radio_block(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction radio Confirmit/Wix natif (fieldset[id^="fieldset_"] + confirmit-table).

    Cas ciblé : pages Toluna layout /wix/2/ où les options radio sont rendues dans
    fieldset[id^="fieldset_"] > table.confirmit-table. Les inputs sont CSS
    position:absolute;top:-9000px — non cliquables via Selenium standard.
    Le clic doit cibler le <a href="javascript:void(0)"> dans la même <td> que l'input.

    Gate DOM (triple) :
    - fieldset[id^="fieldset_"] présent
    - table.confirmit-table dans ce fieldset
    - au moins 2 input[type="radio"] partageant le même name dans ce fieldset

    Exclusions strictes :
    - Ne touche pas _extract_consent_modal_radio_block (#modal-container + .consent-form-radiogroup)
    - Ne touche pas _extract_single_consent_checkbox_block (checkboxes)
    - Ne touche pas les layouts Forsta/Confirmit modernes (div.cf-question)
    """
    frame_chain = list(frame_chain or [])

    try:
        fieldsets = driver.find_elements(By.CSS_SELECTOR, "fieldset[id^='fieldset_']")
    except Exception:
        return []
    if not fieldsets:
        return []

    blocks: list[dict] = []

    for fieldset in fieldsets:
        try:
            if not fieldset.find_elements(By.CSS_SELECTOR, "table.confirmit-table"):
                continue
            radios = fieldset.find_elements(By.CSS_SELECTOR, "input[type='radio']")
            if len(radios) < 2:
                continue
        except Exception:
            continue

        # Vérifie que tous les radios partagent un même name unique
        try:
            names: set[str] = set()
            for r in radios:
                n = (r.get_attribute("name") or "").strip()
                if n:
                    names.add(n)
            if len(names) != 1:
                continue
            group_name = next(iter(names))
        except Exception:
            continue

        # Question depuis div[id="{group_name}_text"]
        question = ""
        try:
            q_els = driver.find_elements(By.CSS_SELECTOR, f"div[id='{group_name}_text']")
            if q_els:
                question = _norm(q_els[0].text or q_els[0].get_attribute("innerText") or "")
        except Exception:
            pass
        if not question:
            try:
                q_els = driver.find_elements(By.CSS_SELECTOR, "div[id$='_text'].question_text_ng")
                if q_els:
                    question = _norm(q_els[0].text or q_els[0].get_attribute("innerText") or "")
            except Exception:
                pass
        if not question:
            question = group_name

        # Construit options et option_xpath_map (clic sur <a> de la même <td>)
        options: list[str] = []
        option_xpath_map: dict[str, str] = {}

        for radio in radios:
            try:
                rid = (radio.get_attribute("id") or "").strip()
                if not rid:
                    continue
                label = ""
                try:
                    lbl = driver.find_element(By.CSS_SELECTOR, f"label[for='{rid}']")
                    label = _norm(lbl.text or lbl.get_attribute("innerText") or "")
                except Exception:
                    pass
                if not label:
                    continue
                key = _norm_key(label)
                if key in option_xpath_map:
                    continue
                rid_lit = _xpath_literal(rid)
                option_xpath_map[key] = f"//input[@id={rid_lit}]/ancestor::td[1]//a[1]"
                options.append(label)
            except Exception:
                continue

        if len(options) < 2:
            continue

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
                "confirmit_wix_fieldset_radio": True,
            },
        )

        log_debug("[CONFIRMIT_WIX_FIELDSET]", f"detected group_name={group_name} options={len(options)}")

        blocks.append({
            "question": question,
            "itype": "radio",
            "options": options,
            "max_select": _compute_max_select("radio", options),
            "target_id": target_id,
            "context": {
                "kind": "group",
                "group_key": group_key,
                "confirmit_wix_fieldset_radio": True,
            },
        })

    return blocks


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
    """Extraction des radios/checkboxes custom basées sur des wrappers `.answer[data-aut='Runtime_AnswerRow']`.

    Gate strict (DOM observable):
    - texte question via `[data-aut='Runtime_QuestionTitleAndDescriptionWrapper'] [data-aut='Runtime-TextComponent']`
    - options via `.answer[data-aut='Runtime_AnswerRow']` dans `div.choice_question`
    - contrôle custom via `.radio_button[data-aut='Runtime_Wrapper']` (radio)
      ou `.check_box[data-aut='Runtime_Wrapper']` (checkbox/multi-choix)
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
    # Maps question_container_id → list of (row, has_checkbox)
    grouped_rows: dict[str, list[tuple[Any, bool]]] = {}

    for row in answer_rows:
        try:
            # Scope guard: la row doit être dans un div.choice_question
            try:
                row.find_element(By.XPATH, "ancestor::div[contains(@class,'choice_question')][1]")
            except Exception:
                continue

            has_radio = bool(row.find_elements(By.CSS_SELECTOR, ".radio_button[data-aut='Runtime_Wrapper']"))
            has_checkbox = bool(row.find_elements(By.CSS_SELECTOR, ".check_box[data-aut='Runtime_Wrapper']"))
            if not has_radio and not has_checkbox:
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

            grouped_rows.setdefault(question_container_id, []).append((row, has_checkbox))
        except Exception:
            continue

    if debug:
        print(f"[DOM_CONTEXT_DEBUG] runtime_answerrow grouped_rows keys={sorted(grouped_rows.keys())}")

    try:
        for qid, row_tuples in grouped_rows.items():
            if len(row_tuples) < 2:
                continue

            rows = [r for r, _ in row_tuples]
            has_any_checkbox = any(cb for _, cb in row_tuples)
            itype_for_group = "checkbox" if has_any_checkbox else "radio"

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

            group_key = f"runtime_answerrow:{itype_for_group}:{qid}"
            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": itype_for_group,
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
                    "itype": itype_for_group,
                    "options": options,
                    "max_select": 1 if itype_for_group == "radio" else len(options),
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


def _extract_runtime_dropdown_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extrait les questions dropdown/date/texte du runtime Toluna/QuickSurveys.

    Gate strict (DOM observable):
    - au moins une question `div.choice_question.display_drop_down` avec
      `[data-testid='MultiValueSelectWrapper'] input[role='combobox']` dans
      `#question_container_<id>` associé.

    Extrait ensuite sur la même page :
    - dropdown (display_drop_down + MultiValueSelectWrapper) → itype='select'
    - date (display_date + .date_selector + MultiValueSelectWrapper) → itype='select'
    - texte libre (open_ended_question + textarea dans container) → itype='textarea'
    """
    frame_chain = list(frame_chain or [])

    # Gate : au moins un display_drop_down avec combobox React Select dans son container
    try:
        dropdown_questions = driver.find_elements(
            By.CSS_SELECTOR, "div.choice_question.display_drop_down"
        )
    except Exception:
        return []

    if not dropdown_questions:
        return []

    has_combobox = False
    for dq in dropdown_questions[:3]:
        try:
            qid_raw = (dq.get_attribute("id") or "").strip()
            if not qid_raw or not qid_raw.startswith("question_"):
                continue
            qnum = qid_raw[len("question_"):]
            container = driver.find_element(By.ID, f"question_container_{qnum}")
            if container.find_elements(
                By.CSS_SELECTOR, "[data-testid='MultiValueSelectWrapper']"
            ):
                has_combobox = True
                break
        except Exception:
            continue

    if not has_combobox:
        return []

    blocks: list[dict] = []
    _TITLE_SEL = (
        "[data-aut='Runtime_QuestionTitleAndDescriptionWrapper'] "
        "[data-aut='Runtime-TextComponent']"
    )

    # --- 1. Dropdown questions (display_drop_down + MultiValueSelectWrapper) ---
    for dq in dropdown_questions:
        try:
            qid_raw = (dq.get_attribute("id") or "").strip()
            if not qid_raw or not qid_raw.startswith("question_"):
                continue
            qnum = qid_raw[len("question_"):]

            question = ""
            for qn in dq.find_elements(By.CSS_SELECTOR, _TITLE_SEL):
                txt = _norm(qn.text or qn.get_attribute("innerText") or "")
                if txt and len(txt) >= 2:
                    question = txt
                    break
            if not question:
                continue

            try:
                container = driver.find_element(By.ID, f"question_container_{qnum}")
            except Exception:
                continue

            wrappers_dd = container.find_elements(
                By.CSS_SELECTOR,
                "[data-testid='MultiValueSelectWrapper']",
            )
            if not wrappers_dd or not wrappers_dd[0].find_elements(
                By.CSS_SELECTOR, "input[role='combobox']"
            ):
                continue

            # Ouvrir le menu React Select pour lire les options (portail dynamique).
            options_list: list[str] = []
            try:
                wrapper_dd = wrappers_dd[0]
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", wrapper_dd)
                wrapper_dd.click()
                menu_el = None
                for _ in range(8):
                    try:
                        menus = driver.find_elements(By.CSS_SELECTOR, "[class*='-menu']")
                        visible = [m for m in menus if m.is_displayed()]
                        if visible:
                            menu_el = visible[-1]
                            break
                    except Exception:
                        pass
                    time.sleep(0.1)
                if menu_el:
                    for opt_el in menu_el.find_elements(By.CSS_SELECTOR, "[class*='-option']"):
                        try:
                            t = _norm(opt_el.text or opt_el.get_attribute("innerText") or "")
                            if t:
                                options_list.append(t)
                        except Exception:
                            continue
                    log_debug("[DOM_CONTEXT_DEBUG]", f"runtime_dropdown qid={qid_raw} options={len(options_list)}")
                else:
                    log_debug("[DOM_CONTEXT_DEBUG]", f"runtime_dropdown qid={qid_raw} menu non ouvert")
                # Fermer le menu
                try:
                    combobox = wrapper_dd.find_element(By.CSS_SELECTOR, "input[role='combobox']")
                    from selenium.webdriver.common.keys import Keys as _Keys
                    combobox.send_keys(_Keys.ESCAPE)
                except Exception:
                    try:
                        wrapper_dd.click()
                    except Exception:
                        pass
            except Exception as _e:
                log_debug("[DOM_CONTEXT_DEBUG]", f"runtime_dropdown qid={qid_raw} option_read_error={type(_e).__name__}")

            group_key = f"runtime_dropdown:{qid_raw}"
            # Utiliser group_key (contient l'ID question stable) au lieu du texte de question
            # pour que le target_id soit identique entre le scan initial et les rescans inter-actions.
            target_id = make_target_id("group", group_key, group_key)
            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "select",
                    "group_key": group_key,
                    "question": question,
                    "container_id": f"question_container_{qnum}",
                    "frame_chain": frame_chain,
                    "runtime_dropdown": True,
                },
            )
            blocks.append(
                {
                    "question": question,
                    "itype": "select",
                    "options": options_list,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "runtime_dropdown": True,
                    },
                    "min_select": 1,
                }
            )
        except Exception:
            continue

    # --- 2. Date questions (display_date + date_selector + MultiValueSelectWrapper) ---
    try:
        date_questions = driver.find_elements(
            By.CSS_SELECTOR, "div.open_ended_question.display_date"
        )
        for dq in date_questions:
            try:
                qid_raw = (dq.get_attribute("id") or "").strip()
                if not qid_raw or not qid_raw.startswith("question_"):
                    continue
                qnum = qid_raw[len("question_"):]

                question = ""
                for qn in dq.find_elements(By.CSS_SELECTOR, _TITLE_SEL):
                    txt = _norm(qn.text or qn.get_attribute("innerText") or "")
                    if txt and len(txt) >= 2:
                        question = txt
                        break
                if not question:
                    continue

                try:
                    container = driver.find_element(By.ID, f"question_container_{qnum}")
                except Exception:
                    continue

                wrappers = container.find_elements(
                    By.CSS_SELECTOR,
                    ".date_selector [data-testid='MultiValueSelectWrapper']",
                )
                if len(wrappers) < 2:
                    continue

                n = len(wrappers)
                parts = ["month", "day", "year"][:n]
                group_key = f"runtime_dropdown:{qid_raw}"
                target_id = make_target_id("group", group_key, group_key)
                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "itype": "select",
                        "group_key": group_key,
                        "question": question,
                        "container_id": f"question_container_{qnum}",
                        "frame_chain": frame_chain,
                        "runtime_dropdown": True,
                        "runtime_dropdown_parts": parts,
                    },
                )
                blocks.append(
                    {
                        "question": question,
                        "itype": "select",
                        "options": [],
                        "max_select": n,
                        "target_id": target_id,
                        "context": {
                            "kind": "group",
                            "group_key": group_key,
                            "runtime_dropdown": True,
                            "runtime_dropdown_parts": parts,
                        },
                        "min_select": n,
                    }
                )
            except Exception:
                continue
    except Exception:
        pass

    # --- 3. Texte libre (open_ended_question + textarea dans container) ---
    try:
        oe_questions = driver.find_elements(
            By.CSS_SELECTOR, "div.open_ended_question:not(.display_date)"
        )
        for dq in oe_questions:
            try:
                qid_raw = (dq.get_attribute("id") or "").strip()
                if not qid_raw or not qid_raw.startswith("question_"):
                    continue
                qnum = qid_raw[len("question_"):]

                question = ""
                for qn in dq.find_elements(By.CSS_SELECTOR, _TITLE_SEL):
                    txt = _norm(qn.text or qn.get_attribute("innerText") or "")
                    if txt and len(txt) >= 2:
                        question = txt
                        break
                if not question:
                    continue

                try:
                    container = driver.find_element(By.ID, f"question_container_{qnum}")
                except Exception:
                    continue

                if not container.find_elements(By.CSS_SELECTOR, "textarea"):
                    continue

                field_key = f"runtime_text:{qid_raw}"
                target_id = make_target_id("field", field_key, field_key)
                register_target(
                    target_id,
                    {
                        "kind": "field",
                        "itype": "textarea",
                        "field_key": field_key,
                        "question": question,
                        "container_id": f"question_container_{qnum}",
                        "frame_chain": frame_chain,
                        "runtime_text": True,
                    },
                )
                blocks.append(
                    {
                        "question": question,
                        "itype": "textarea",
                        "options": [],
                        "max_select": 1,
                        "target_id": target_id,
                        "context": {
                            "kind": "field",
                            "field_key": field_key,
                            "runtime_text": True,
                        },
                        "min_select": 1,
                    }
                )
            except Exception:
                continue
    except Exception:
        pass

    if is_debug():
        log_debug("[RUNTIME_DD]", f"extracted {len(blocks)} dropdown/date/text blocks")

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


def _extract_kantar_rowrank_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction ranking pour Kantar rowrank (metaType=rowrank, mrIWeb).

    Gate DOM strict:
    - div[id^='container_'] [data-test='main-contain']._rowrank
    - guard: input[type='text'].mrEdit[name*='Qslice'] dans questionContainer correspondant

    Les cartes visuelles sont cliquées dans l'ordre voulu (1er clic = rang 1, etc.).
    max_select est lu depuis CustomProps.row$capvalue dans le script SEJson.
    """
    frame_chain = list(frame_chain or [])

    try:
        rankers = driver.find_elements(
            By.CSS_SELECTOR,
            "div[id^='container_'] [data-test='main-contain']._rowrank",
        )
    except Exception:
        return []

    if not rankers:
        return []

    blocks: list[dict] = []

    for ranker in rankers:
        try:
            container = ranker.find_element(By.XPATH, "ancestor::div[starts-with(@id,'container_')][1]")
            container_id = (container.get_attribute("id") or "").strip()
        except Exception:
            continue

        if not container_id.startswith("container_"):
            continue

        q_suffix = container_id[len("container_"):].strip()
        if not q_suffix:
            continue

        # Guard: Qslice text inputs must be present in the hidden questionContainer
        try:
            qslice_inputs = driver.find_elements(
                By.CSS_SELECTOR,
                f".questionContainer[questionname^='{q_suffix}'] input[type='text'].mrEdit[name*='Qslice']",
            )
        except Exception:
            qslice_inputs = []

        if not qslice_inputs:
            continue

        # Extract question text from qcContainer
        question = ""
        try:
            q_nodes = driver.find_elements(By.CSS_SELECTOR, f"#qc_{q_suffix} span.mrQuestionText")
        except Exception:
            q_nodes = []

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

        # Build label→mrEdit map from the hidden mrQuestionTable rows.
        # Each tr has: td.mrGridCategoryText > span.mrQuestionText (label)
        #              td > input.mrEdit[name*='Qslice'] (carries rowid).
        # This is the authoritative mapping — order-independent.
        label_to_mrinput: dict[str, object] = {}
        try:
            rows = driver.find_elements(
                By.CSS_SELECTOR,
                f".questionContainer[questionname^='{q_suffix}'] table.mrQuestionTable tr",
            )
            for row in rows:
                try:
                    lbl_node = row.find_element(By.CSS_SELECTOR, "td.mrGridCategoryText span.mrQuestionText")
                    mr_inp = row.find_element(By.CSS_SELECTOR, "input[type='text'].mrEdit[name*='Qslice']")
                except Exception:
                    continue
                lbl = _norm(lbl_node.text or lbl_node.get_attribute("innerText") or "")
                if lbl:
                    label_to_mrinput[_norm_key(lbl)] = mr_inp
        except Exception:
            pass

        # Extract options from visual flex cards
        try:
            cards = ranker.find_elements(By.CSS_SELECTOR, "div.__flexgrid_row > div")
        except Exception:
            cards = []

        options: list[str] = []
        option_xpath_map: dict[str, str] = {}

        for card in cards:
            try:
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

            # Match by label to the mrEdit that carries rowid (order-independent).
            mr_inp = label_to_mrinput.get(nk)
            if mr_inp is None:
                continue
            xp = _best_xpath_for_element(driver, mr_inp)
            if not xp:
                continue

            option_xpath_map[nk] = xp
            options.append(label_text)

        if len(options) < 2 or len(option_xpath_map) < 2:
            continue

        # Read max_select and captype from SEJson CustomProps row$capvalue / row$captype
        max_select = 3
        cap_hard = False
        try:
            scripts = driver.find_elements(By.CSS_SELECTOR, 'script.SEJson[type="application/json"]')
            for script in (scripts or []):
                raw = script.get_attribute("textContent") or script.get_attribute("innerHTML") or ""
                raw = re.sub(r"<!--\s*", "", raw)
                raw = re.sub(r"\s*//-->", "", raw)
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                for q in (data.get("qJSON") or []):
                    props = q.get("CustomProps") or {}
                    cap = str(props.get("row$capvalue") or "").strip()
                    if cap.isdigit():
                        max_select = max(1, int(cap))
                    captype = str(props.get("row$captype") or "").strip().lower()
                    if captype == "hard":
                        cap_hard = True
                    if cap.isdigit():
                        break
        except Exception:
            pass

        group_key = f"kantar_rowrank:checkbox:{q_suffix}"
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
                "kantar_rowrank": True,
            },
        )

        context: dict = {
            "kind": "group",
            "group_key": group_key,
            "kantar_rowrank": True,
        }
        if cap_hard:
            context["cap_hard"] = True

        blocks.append(
            {
                "question": question,
                "itype": "checkbox",
                "options": options,
                "max_select": max_select,
                "target_id": target_id,
                "context": context,
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

        # Layout Qualtrics Likert/SingleAnswer: grille Yes/No rendue comme table.ChoiceStructure
        # où chaque tr.ChoiceRow porte un name distinct (ex: QR~QID3615~27).
        # Gate DOM strict: div.Inner avec classe Likert ou SingleAnswer + tr.ChoiceRow multi-name.
        try:
            likert_nodes = container.find_elements(
                By.CSS_SELECTOR, "div.Inner.Likert, div.Inner.SingleAnswer"
            )
        except Exception:
            likert_nodes = []

        if likert_nodes:
            try:
                choice_rows = container.find_elements(
                    By.CSS_SELECTOR, "table.ChoiceStructure > tbody > tr.ChoiceRow"
                )
            except Exception:
                choice_rows = []

            # Confirm multi-name: at least 2 rows with distinct names
            row_names = []
            for _cr in choice_rows:
                try:
                    _rr = _cr.find_elements(By.CSS_SELECTOR, "input[type='radio'][name^='QR~']")
                    if _rr:
                        _n = (_rr[0].get_attribute("name") or "").strip()
                        if _n:
                            row_names.append(_n)
                except Exception:
                    pass

            if len(set(row_names)) >= 2:
                # Read column headers (Yes / No) from thead
                col_headers: list[str] = []
                try:
                    thead_ths = container.find_elements(
                        By.CSS_SELECTOR,
                        "table.ChoiceStructure thead tr.Answers th.Selection span.LabelWrapper span",
                    )
                except Exception:
                    thead_ths = []
                for th_node in thead_ths:
                    hdr = _norm(th_node.text or th_node.get_attribute("innerText") or "")
                    if hdr:
                        col_headers.append(hdr)

                for row_idx, row in enumerate(choice_rows):
                    try:
                        row_radios = row.find_elements(
                            By.CSS_SELECTOR, "input[type='radio'][name^='QR~']"
                        )
                    except Exception:
                        row_radios = []
                    if len(row_radios) < 2:
                        continue

                    try:
                        row_group_name = (row_radios[0].get_attribute("name") or "").strip()
                    except Exception:
                        row_group_name = ""
                    if not row_group_name:
                        continue

                    # Statement text from th.c1 label span
                    statement = ""
                    for stmt_sel in (
                        "th.c1 span.LabelWrapper div.table-cell label span",
                        "th.c1 label span",
                        "th.c1 span",
                    ):
                        try:
                            stmt_nodes = row.find_elements(By.CSS_SELECTOR, stmt_sel)
                        except Exception:
                            stmt_nodes = []
                        for sn in stmt_nodes:
                            cand = _norm(sn.text or sn.get_attribute("innerText") or "")
                            if cand:
                                statement = cand
                                break
                        if statement:
                            break
                    if not statement:
                        continue

                    row_question = f"{question} {statement}" if question else statement

                    # Map col_headers to radios by column position
                    effective_headers = col_headers if col_headers else [
                        _norm(r.get_attribute("value") or "") for r in row_radios
                    ]
                    # Build option_xpath_map aligned by position
                    row_options: list[str] = []
                    row_xpath_map: dict[str, str] = {}
                    for col_i, col_hdr in enumerate(effective_headers):
                        if col_i >= len(row_radios):
                            break
                        nk = _norm_key(col_hdr)
                        if not nk or nk in row_xpath_map:
                            continue
                        opt_radio = row_radios[col_i]
                        try:
                            opt_id = (opt_radio.get_attribute("id") or "").strip()
                        except Exception:
                            opt_id = ""
                        xp = (
                            f"//*[@id={_xpath_literal(opt_id)}]"
                            if opt_id
                            else _best_xpath_for_element(driver, opt_radio)
                        )
                        if not xp:
                            continue
                        row_options.append(col_hdr)
                        row_xpath_map[nk] = xp

                    if len(row_options) < 2:
                        continue

                    row_group_key = f"radio:name:{row_group_name.lower()}"
                    row_target_id = make_target_id("group", row_group_key, row_question)
                    register_target(
                        row_target_id,
                        {
                            "kind": "group",
                            "itype": "radio",
                            "group_key": row_group_key,
                            "question": row_question,
                            "option_xpath_map": row_xpath_map,
                            "frame_chain": frame_chain,
                            "qualtrics_choice_structure_radio": True,
                            "qualtrics_likert_grid": True,
                        },
                    )
                    blocks.append(
                        {
                            "question": row_question,
                            "itype": "radio",
                            "options": row_options,
                            "max_select": _compute_max_select("radio", row_options),
                            "target_id": row_target_id,
                            "context": {
                                "kind": "group",
                                "group_key": row_group_key,
                            },
                            "min_select": 1,
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


def _extract_qualtrics_dl_select_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction ciblée des dropdowns Qualtrics layout DL (1 <select> unique par question).

    Gate DOM strict (additif) :
    - div.QuestionOuter contenant div.Inner.DL
    - select.ChoiceStructure[name^='QR~'] unique dans le conteneur
    - label.QuestionText ou legend label.QuestionText comme texte de question

    Ne couvre PAS les matrix dropdowns (plusieurs selects par conteneur) —
    ceux-ci restent gérés par _extract_qualtrics_matrix_dropdown_row_blocks.
    """
    frame_chain = list(frame_chain or [])
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.QuestionOuter")
    except Exception:
        return blocks

    for idx, container in enumerate(containers):
        # Gate strict : div.Inner.DL doit être présent dans ce conteneur
        try:
            dl_inner = container.find_elements(By.CSS_SELECTOR, "div.Inner.DL")
        except Exception:
            dl_inner = []
        if not dl_inner:
            continue

        # Un seul <select> Qualtrics attendu (sinon c'est une matrix → autre extracteur)
        try:
            selects = container.find_elements(
                By.CSS_SELECTOR,
                "select.ChoiceStructure[name^='QR~']",
            )
        except Exception:
            selects = []
        if len(selects) != 1:
            continue

        sel = selects[0]
        sel_id = (sel.get_attribute("id") or "").strip()
        sel_name = (sel.get_attribute("name") or "").strip()
        if not sel_id and not sel_name:
            continue

        # Texte de la question : legend > label.QuestionText ou label.QuestionText
        question = ""
        for q_sel in (
            "fieldset legend label.QuestionText",
            "legend label.QuestionText",
            "label.QuestionText",
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

        # Options (on ignore la première option vierge aria-label="Vierge")
        options: list[str] = []
        try:
            option_nodes = sel.find_elements(By.TAG_NAME, "option")
        except Exception:
            option_nodes = []

        for opt in option_nodes:
            try:
                if opt.get_attribute("disabled"):
                    continue
                aria = (opt.get_attribute("aria-label") or "").strip().lower()
                if aria in ("vierge", "blank", ""):
                    val = (opt.get_attribute("value") or "").strip()
                    if not val or "null" in val.lower():
                        continue
                txt = _norm(opt.text or opt.get_attribute("innerText") or "")
                if txt:
                    options.append(txt)
            except Exception:
                continue

        options = list(dict.fromkeys(options))
        if not options:
            continue

        single_key = f"qualtrics_dl_select:{sel_id}:{sel_name}"
        target_id = make_target_id("single", single_key, question)
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
                "itype": "select",
                "question": question,
                "xpath": xpath,
                "alt_xpaths": alt_xpaths,
                "tag": "select",
                "name": sel_name,
                "id": sel_id,
                "frame_chain": frame_chain,
                "qualtrics_dl_select": True,
            },
        )

        blocks.append(
            {
                "question": question,
                "itype": "select",
                "options": options,
                "max_select": 1,
                "min_select": 1,
                "target_id": target_id,
                "context": {
                    "kind": "single",
                    "tag": "select",
                    "name": sel_name,
                    "id": sel_id,
                    "qualtrics_dl_select": True,
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
            "fieldset legend label.QuestionText",
            "legend .QuestionText",
            "div.QuestionText",
            "label.QuestionText",
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
    """CloudResearch/Sentry : extraction DOM-only des questions à choix unique.

    Plateforme CloudResearch utilise Vue.js avec des divs role="button" comme boutons radio.
    Structure DOM typique:
    - Conteneur: #sentry ou .cr-question-card
    - Question: h1[id*="QuestionLabel"] ou h1.question-prompt
    - Options: .choice-option[role="button"] avec texte dans .cr-ct ou div[class*="answer-choice"]

    Gate strict: n'active l'extracteur que si le pattern CloudResearch est détecté
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
            r"""
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


def _extract_toluna_runtime_ranking_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Ranking séquentiel par clic (Toluna Runtime, display_clicking_order).

    Gate DOM strict:
    - div[id^='question_'].ranking_question.display_clicking_order
    - options via div.answer[data-aut='Runtime_RankingItemWrapper']
    - aucun .answer[data-aut='Runtime_AnswerRow'] (évite overlap avec l'extracteur radio/checkbox)
    """
    frame_chain = list(frame_chain or [])

    try:
        ranking_containers = driver.find_elements(
            By.CSS_SELECTOR,
            "div[id^='question_'].ranking_question.display_clicking_order",
        )
    except Exception:
        return []

    if not ranking_containers:
        return []

    # Guard: si des AnswerRow sont présents, l'extracteur radio/checkbox couvre déjà la page.
    try:
        if driver.find_elements(By.CSS_SELECTOR, ".answer[data-aut='Runtime_AnswerRow']"):
            return []
    except Exception:
        pass

    blocks: list[dict] = []

    for container in ranking_containers:
        try:
            qid = (container.get_attribute("id") or "").strip()

            q_nodes = container.find_elements(
                By.CSS_SELECTOR,
                "[data-aut='Runtime_QuestionTitleAndDescriptionWrapper'] [data-aut='Runtime-TextComponent']",
            )
            question = ""
            for qn in q_nodes:
                txt = _norm(qn.text or qn.get_attribute("innerText") or "")
                if txt and len(txt) >= 5:
                    question = txt
                    break

            if not question:
                continue

            option_nodes = container.find_elements(
                By.CSS_SELECTOR,
                "div.answer[data-aut='Runtime_RankingItemWrapper']",
            )
            if len(option_nodes) < 2:
                continue

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}

            for node in option_nodes:
                try:
                    text_nodes = node.find_elements(
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
                    node_id = (node.get_attribute("id") or "").strip()
                    xp = f"//*[@id={_xpath_literal(node_id)}]" if node_id else _best_xpath_for_element(driver, node)
                    if not xp:
                        continue
                    option_xpath_map[nk] = xp
                    options.append(label_text)
                except Exception:
                    continue

            if len(options) < 2:
                continue

            max_select = len(options)
            m = re.search(r"les\s+(\d+)\s+éléments?", question, re.IGNORECASE)
            if m:
                max_select = max(1, int(m.group(1)))

            group_key = f"toluna_runtime_ranking:{_norm_key(question)}"
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
                    "toluna_runtime_ranking": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "checkbox",
                    "options": options,
                    "max_select": max_select,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "toluna_runtime_ranking": True,
                    },
                }
            )
        except Exception:
            continue

    return blocks


def _extract_savanta_jqm_carousel_block(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extrait le bloc courant d'un carousel jQuery Mobile / Slick (Savanta).

    Gate DOM strict (additif) :
    - ``fieldset.carousel`` contenant un ``.slick-initialized.slick-slider``
    - ``fieldset.carousel-buttons`` avec au moins 2 ``button.ui-btn-hidden``

    Extraction :
    - Question : légende du fieldset.carousel (hors ``span.inst``) + label de l'item ``.slick-current``
    - Options   : texte des ``button.ui-btn-hidden`` dans ``fieldset.carousel-buttons``
    - XPath cibles : ``div.ui-btn`` parent visible de chaque bouton hidden

    Payload extras (pour action_dispatcher) :
    - ``savanta_jqm_carousel: True``
    - ``jqm_carousel_current_data_index: int`` (data-index du slide courant dans carousel-values)
    """
    blocks: list[dict] = []

    # --- Gate 1 : fieldset.carousel avec slick-slider interne ---
    try:
        carousel_fieldsets = driver.find_elements(
            By.CSS_SELECTOR, "fieldset.carousel"
        )
    except Exception:
        return blocks

    if not carousel_fieldsets:
        return blocks

    carousel_fs = None
    for fs in carousel_fieldsets:
        try:
            if fs.find_elements(By.CSS_SELECTOR, ".slick-initialized.slick-slider"):
                carousel_fs = fs
                break
        except Exception:
            continue

    if carousel_fs is None:
        return blocks

    # --- Gate 2 : fieldset.carousel-buttons avec boutons ---
    try:
        btn_fieldset = driver.find_element(By.CSS_SELECTOR, "fieldset.carousel-buttons")
    except Exception:
        return blocks

    try:
        hidden_btns = btn_fieldset.find_elements(
            By.CSS_SELECTOR, "button.ui-btn-hidden"
        )
    except Exception:
        hidden_btns = []

    if len(hidden_btns) < 2:
        return blocks

    # --- Extraction de la question ---
    # 1) Légende du fieldset.carousel (texte direct, sans le span.inst)
    legend_text = ""
    try:
        legend = carousel_fs.find_element(By.CSS_SELECTOR, "legend")
        legend_text = driver.execute_script(
            """
            const legend = arguments[0];
            if (!legend) return '';
            const clone = legend.cloneNode(true);
            for (const s of clone.querySelectorAll('span.inst, span.instruction, .instruction')) {
                s.remove();
            }
            return clone.textContent.replace(/\\s+/g, ' ').trim();
            """,
            legend,
        )
    except Exception:
        legend_text = ""

    legend_text = _norm(legend_text or "")

    # 2) Label de l'item courant (.slick-current)
    current_label = ""
    try:
        current_slide = carousel_fs.find_element(By.CSS_SELECTOR, ".slick-current")
        try:
            ce = current_slide.find_element(By.CSS_SELECTOR, ".carousel-element")
            tit = (ce.get_attribute("title") or "").strip()
            if tit:
                current_label = tit
        except Exception:
            pass
        if not current_label:
            try:
                iw = current_slide.find_element(By.CSS_SELECTOR, ".image-wrapper")
                current_label = _norm(iw.text or iw.get_attribute("innerText") or "")
            except Exception:
                pass
        if not current_label:
            current_label = _norm(current_slide.text or "")
    except Exception:
        current_label = ""

    if not legend_text and not current_label:
        return blocks

    question = _norm(
        f"{legend_text} \u2013 {current_label}"
        if (legend_text and current_label)
        else (legend_text or current_label)
    )
    if not question:
        return blocks

    # --- data-index du slide courant (pour validation post-clic) ---
    jqm_current_data_index: int | None = None
    try:
        current_slide = carousel_fs.find_element(By.CSS_SELECTOR, ".slick-current")
        di = (current_slide.get_attribute("data-index") or "").strip()
        if di.lstrip("-").isdigit():
            jqm_current_data_index = int(di)
    except Exception:
        pass

    # --- Options et XPath sur div.ui-btn (le wrapper visible cliquable) ---
    options: list[str] = []
    option_xpath_map: dict[str, str] = {}

    for btn in hidden_btns:
        try:
            lbl = _norm(btn.text or btn.get_attribute("innerText") or btn.get_attribute("value") or "")
            if not lbl or len(lbl) < 2:
                continue

            # Remonter au div.ui-btn parent (wrapper cliquable JQM)
            try:
                ui_btn_div = driver.execute_script(
                    """
                    let el = arguments[0];
                    for (let i = 0; i < 5 && el; i++, el = el.parentElement) {
                        if (el.tagName && el.tagName.toLowerCase() !== 'button') {
                            if ((el.className || '').includes('ui-btn')) return el;
                        }
                    }
                    return null;
                    """,
                    btn,
                )
            except Exception:
                ui_btn_div = None

            target_el = ui_btn_div if ui_btn_div is not None else btn
            xp = _best_xpath_for_element(driver, target_el)
            if not xp:
                continue

            nk = _norm_key(lbl)
            if nk not in option_xpath_map:
                options.append(lbl)
                option_xpath_map[nk] = xp
        except Exception:
            continue

    if len(options) < 2:
        return blocks

    group_key = f"savanta_jqm_carousel:{_norm_key(question)}"
    target_id = make_target_id("group", group_key, question)

    register_target(
        target_id,
        {
            "kind": "group",
            "itype": "radio",
            "group_key": group_key,
            "question": question,
            "option_xpath_map": option_xpath_map,
            "frame_chain": frame_chain or [],
            "savanta_jqm_carousel": True,
            "jqm_carousel_current_data_index": jqm_current_data_index,
        },
    )

    log_debug(
        "[DOM_SAVANTA_JQM_CAROUSEL]",
        f"question={question!r} options={options} current_data_index={jqm_current_data_index}",
    )

    return [
        {
            "question": question,
            "itype": "radio",
            "options": options,
            "max_select": 1,
            "min_select": 1,
            "target_id": target_id,
            "context": {
                "kind": "group",
                "group_key": group_key,
                "savanta_jqm_carousel": True,
            },
        }
    ]


# ================================================================================
# QUESTMINDSHARE CHATBOT - OPTIONS data-testid
# ================================================================================

def _extract_questmindshare_chatbot_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """QuestMindshare chatbot React/Next.js : extraction des blocs choix multiples.

    Gate strict : présence de div[data-testid^="option-"] avec tabindex="0".
    Si absent → retourne [] sans toucher au reste.

    Structure ciblée :
    - Options : div[data-testid="option-N"] (N entier) avec tabindex="0"
    - Question : dernier div[data-testid="message-text"] visible
    - Instructions : div[data-testid="instructions"] (si présent)
    - CTA : button[data-testid="confirm-selection"]
    """
    frame_chain = list(frame_chain or [])

    # --- Gate strict ---
    try:
        gate_els = driver.find_elements(
            By.CSS_SELECTOR, "div[data-testid^='option-'][tabindex='0']"
        )
    except Exception:
        return []

    if not gate_els:
        return []

    # --- Collecte des options dans l'ordre (option-0, option-1, ...) ---
    options: list[str] = []
    option_xpath_map: dict[str, str] = {}

    idx = 0
    while idx < 50:  # budget max 50 options
        sel = f"div[data-testid='option-{idx}'][tabindex='0']"
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            break
        if not els:
            break
        el = els[0]
        try:
            label = _norm(el.text or el.get_attribute("innerText") or "")
        except Exception:
            label = ""
        if label:
            nk = _norm_key(label)
            if nk and nk not in option_xpath_map:
                xp = _best_xpath_for_element(driver, el)
                if not xp:
                    xp = f"//div[@data-testid='option-{idx}'][@tabindex='0']"
                option_xpath_map[nk] = xp
                options.append(label)
        idx += 1

    if len(options) < 2:
        return []

    # --- Question text : dernier message-text visible ---
    question = ""
    try:
        msg_els = driver.find_elements(By.CSS_SELECTOR, "div[data-testid='message-text']")
        for el in reversed(msg_els):
            try:
                txt = _norm(el.text or el.get_attribute("innerText") or "")
            except Exception:
                txt = ""
            if txt and len(txt) >= 5:
                question = txt
                break
    except Exception:
        pass

    if not question:
        return []

    # --- Instructions (si présentes, les ajouter à la question) ---
    try:
        instr_els = driver.find_elements(By.CSS_SELECTOR, "div[data-testid='instructions']")
        for el in instr_els:
            try:
                instr = _norm(el.text or el.get_attribute("innerText") or "")
            except Exception:
                instr = ""
            if instr:
                question = f"{question} {instr}"
                break
    except Exception:
        pass

    # --- CTA xpath (référence pour l'input_handler) ---
    cta_xpath = ""
    try:
        cta_els = driver.find_elements(By.CSS_SELECTOR, "button[data-testid='confirm-selection']")
        if cta_els:
            cta_xpath = _best_xpath_for_element(driver, cta_els[0])
            if not cta_xpath:
                cta_xpath = "//button[@data-testid='confirm-selection']"
    except Exception:
        pass

    # --- Bloc ---
    group_key = f"questmindshare:checkbox:idx0_{zlib.crc32(question.encode('utf-8')):x}"
    target_id = make_target_id("group", group_key, question)

    payload: dict = {
        "kind": "group",
        "itype": "checkbox",
        "group_key": group_key,
        "question": question,
        "option_xpath_map": option_xpath_map,
        "frame_chain": frame_chain,
        "questmindshare": True,
    }
    if cta_xpath:
        payload["cta_xpath"] = cta_xpath

    register_target(target_id, payload)

    log_debug(
        "[DOM_QUESTMINDSHARE]",
        f"question={question!r} options={options} cta_xpath={cta_xpath!r}",
    )

    return [
        {
            "question": question,
            "itype": "checkbox",
            "options": options,
            "max_select": _compute_max_select("checkbox", options, question),
            "min_select": 1,
            "target_id": target_id,
            "context": {
                "kind": "group",
                "group_key": group_key,
                "questmindshare": True,
            },
        }
    ]


def _extract_confirmit_cf_desktop_grid_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Forsta/Confirmit CF desktop radio grid: 1 ligne = 1 bloc radio.

    Gate DOM strict (provider-agnostic):
    - présence de table.cf-table-layout
    - présence de div.cf-radio[role='radio'] dans tbody de cette table

    Extrait les libellés de colonnes depuis thead div.cf-desktop-grid__scale-text,
    et le libellé de ligne depuis l'élément référencé par aria-labelledby du tr.
    """
    frame_chain = list(frame_chain or [])

    try:
        tables = driver.find_elements(By.CSS_SELECTOR, "table.cf-table-layout")
    except Exception:
        return []
    if not tables:
        return []

    # Gate: au moins une table contient des div.cf-radio[role='radio'] dans tbody
    gate_ok = False
    for t in tables:
        try:
            if t.find_elements(By.CSS_SELECTOR, "tbody div.cf-radio[role='radio']"):
                gate_ok = True
                break
        except Exception:
            continue
    if not gate_ok:
        return []

    blocks: list[dict] = []

    for table in tables[:10]:  # budget anti-explosion
        try:
            # Colonnes: libellés dans thead div.cf-desktop-grid__scale-text
            scale_labels: list[str] = []
            try:
                scale_divs = table.find_elements(
                    By.CSS_SELECTOR, "thead div.cf-desktop-grid__scale-text"
                )
            except Exception:
                scale_divs = []

            for div in scale_divs:
                txt = _norm(div.text or div.get_attribute("innerText") or "")
                if txt and txt not in scale_labels:
                    scale_labels.append(txt)

            if len(scale_labels) < 2:
                continue

            # Titre global de la grille: remonter au div.cf-question ancêtre
            grid_title = ""
            try:
                q_container = table.find_element(
                    By.XPATH, "ancestor::div[contains(@class,'cf-question')]"
                )
                q_text_el = q_container.find_element(
                    By.CSS_SELECTOR, "div.cf-question__text"
                )
                grid_title = _norm(q_text_el.get_attribute("textContent") or "")
            except Exception:
                pass

            # Lignes: tr[role='radiogroup'] dans tbody
            try:
                rows = table.find_elements(By.CSS_SELECTOR, "tbody tr[role='radiogroup']")
            except Exception:
                rows = []
            if not rows:
                continue

            for row in rows[:30]:  # budget anti-explosion
                try:
                    row_id = (row.get_attribute("id") or "").strip()
                    if not row_id:
                        continue

                    # Question depuis aria-labelledby du tr
                    row_label = ""
                    labelledby = (row.get_attribute("aria-labelledby") or "").strip()
                    if labelledby:
                        for ref_id in labelledby.split():
                            try:
                                node = driver.find_element(By.ID, ref_id)
                                txt = _norm(node.get_attribute("textContent") or node.text or "")
                                if txt:
                                    row_label = txt
                                    break
                            except Exception:
                                continue
                    if not row_label:
                        continue
                    question = f"{grid_title} - {row_label}" if grid_title else row_label

                    # Radios dans la ligne
                    try:
                        radio_divs = row.find_elements(By.CSS_SELECTOR, "div.cf-radio[role='radio']")
                    except Exception:
                        radio_divs = []
                    if len(radio_divs) != len(scale_labels):
                        continue

                    row_id_lc = row_id.lower()
                    row_cls = _norm_lc(row.get_attribute("class") or "")
                    group_key = f"radio:name:dom:{labelledby}|{row_id_lc}|{row_cls}"

                    # option_xpath_map: clé normalisée -> xpath du div radio
                    option_xpath_map: dict[str, str] = {}
                    for opt, radio_div in zip(scale_labels, radio_divs):
                        try:
                            ctrl_id = (radio_div.get_attribute("id") or "").strip()
                            if ctrl_id:
                                option_xpath_map[_norm_key(opt)] = f"//*[@id={_xpath_literal(ctrl_id)}]"
                            else:
                                xp = _best_xpath_for_element(radio_div)
                                if xp:
                                    option_xpath_map[_norm_key(opt)] = xp
                        except Exception:
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
                            "confirmit_cf_desktop_grid": True,
                        },
                    )

                    blocks.append(
                        {
                            "question": question,
                            "itype": "radio",
                            "options": list(scale_labels),
                            "max_select": 1,
                            "min_select": 1,
                            "target_id": target_id,
                            "context": {
                                "kind": "group",
                                "group_key": group_key,
                            },
                        }
                    )
                    log_debug(
                        "[DOM_CONFIRMIT_CF_GRID]",
                        f"row={row_id!r} question={question!r} options={scale_labels}",
                    )
                except Exception:
                    continue
        except Exception:
            continue

    return blocks


# ================================================================================
# FORSTA/CONFIRMIT - BIPOLAR BUTTON RATING SCALE (cf-question--answer-buttons-grid)
# ================================================================================

def _extract_confirmit_cf_bipolar_button_grid_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Forsta/Confirmit CF bipolar button rating scale: cf-question--answer-buttons-grid.

    Gate DOM (distinct from classic matrix):
    - table.cf-table-layout avec tbody div.cf-button-answer__button[role='radio']
    - pas de div.cf-radio (variante bipolaire, sans thead de colonnes partagées)

    Par div.cf-question--answer-buttons-grid (en ordre document) :
    - question : cf-question__text, avec carry-forward si vide (blocs liés visuellement)
    - par tr[role='radiogroup'] dans le contenu desktop :
      - pôle gauche : premier ID aria-labelledby → cf-desktop-grid__answer-text
      - labels numériques : innerText de div.cf-button-answer__text (exclut spans display:none)
      - pôle droit : deuxième ID aria-labelledby → cf-desktop-grid__right-text
      - options = [pôle_gauche] + [labels numériques] + [pôle_droit]
    """
    frame_chain = list(frame_chain or [])

    try:
        tables = driver.find_elements(By.CSS_SELECTOR, "table.cf-table-layout")
    except Exception:
        return []
    if not tables:
        return []

    # Gate : variante bipolaire détectée par cf-button-answer__button (pas cf-radio)
    gate_ok = False
    for t in tables:
        try:
            if t.find_elements(By.CSS_SELECTOR, "tbody div.cf-button-answer__button[role='radio']"):
                gate_ok = True
                break
        except Exception:
            continue
    if not gate_ok:
        return []

    blocks: list[dict] = []
    last_title = ""

    try:
        cf_questions = driver.find_elements(
            By.CSS_SELECTOR, "div.cf-question--answer-buttons-grid"
        )
    except Exception:
        return []

    for q_div in cf_questions[:20]:  # budget anti-explosion
        try:
            # Question avec carry-forward du dernier titre non-vide
            try:
                q_text_el = q_div.find_element(By.CSS_SELECTOR, "div.cf-question__text")
                title = _norm(q_text_el.get_attribute("textContent") or "")
            except Exception:
                title = ""
            if title:
                last_title = title
            current_title = last_title

            # Lignes desktop uniquement (le mobile est exclu)
            try:
                rows = q_div.find_elements(
                    By.CSS_SELECTOR,
                    "div.cf-question__content--desktop tr[role='radiogroup']",
                )
            except Exception:
                rows = []

            for row in rows[:5]:  # typiquement 1 ligne par cf-question
                try:
                    row_id = (row.get_attribute("id") or "").strip()
                    if not row_id:
                        continue

                    labelledby = (row.get_attribute("aria-labelledby") or "").strip()
                    label_ids = labelledby.split() if labelledby else []

                    # Pôle gauche (premier ID aria-labelledby)
                    left_pole = ""
                    if label_ids:
                        try:
                            el = driver.find_element(By.ID, label_ids[0])
                            left_pole = _norm(el.get_attribute("textContent") or el.text or "")
                        except Exception:
                            pass

                    # Pôle droit (deuxième ID aria-labelledby)
                    right_pole = ""
                    if len(label_ids) >= 2:
                        try:
                            el = driver.find_element(By.ID, label_ids[1])
                            right_pole = _norm(el.get_attribute("textContent") or el.text or "")
                        except Exception:
                            pass

                    # Boutons radio (cf-button-answer__button), labels via innerText (exclut spans display:none)
                    try:
                        btn_divs = row.find_elements(
                            By.CSS_SELECTOR, "div.cf-button-answer__button[role='radio']"
                        )
                    except Exception:
                        btn_divs = []
                    if not btn_divs:
                        continue

                    numeric_labels: list[str] = []
                    for btn in btn_divs:
                        try:
                            txt_el = btn.find_element(By.CSS_SELECTOR, "div.cf-button-answer__text")
                            label = _norm(txt_el.get_attribute("innerText") or "")
                            if label:
                                numeric_labels.append(label)
                        except Exception:
                            continue
                    if not numeric_labels:
                        continue

                    # options = labels numériques seuls (cliquables) ; pôles exclus
                    options: list[str] = list(numeric_labels)

                    row_id_lc = row_id.lower()
                    row_cls = _norm_lc(row.get_attribute("class") or "")
                    group_key = f"radio:name:dom:{labelledby}|{row_id_lc}|{row_cls}"

                    # option_xpath_map : uniquement les boutons numériques cliquables
                    option_xpath_map: dict[str, str] = {}
                    for lbl, btn in zip(numeric_labels, btn_divs):
                        try:
                            ctrl_id = (btn.get_attribute("id") or "").strip()
                            if ctrl_id:
                                option_xpath_map[_norm_key(lbl)] = f"//*[@id={_xpath_literal(ctrl_id)}]"
                            else:
                                xp = _best_xpath_for_element(btn)
                                if xp:
                                    option_xpath_map[_norm_key(lbl)] = xp
                        except Exception:
                            continue

                    poles: list[str] = [p for p in [left_pole, right_pole] if p]

                    target_id = make_target_id("group", group_key, current_title)
                    register_target(
                        target_id,
                        {
                            "kind": "group",
                            "itype": "radio",
                            "group_key": group_key,
                            "question": current_title,
                            "option_xpath_map": option_xpath_map,
                            "poles": poles,
                            "frame_chain": frame_chain,
                            "confirmit_cf_bipolar_grid": True,
                        },
                    )

                    blocks.append(
                        {
                            "question": current_title,
                            "itype": "radio",
                            "options": options,
                            "poles": poles,
                            "max_select": 1,
                            "min_select": 1,
                            "target_id": target_id,
                            "context": {
                                "kind": "group",
                                "group_key": group_key,
                            },
                        }
                    )
                except Exception:
                    continue
        except Exception:
            continue

    if blocks:
        distinct_titles = list(dict.fromkeys(b["question"] for b in blocks))
        log_debug(
            "[DOM_CONFIRMIT_CF_BIPOLAR]",
            f"{len(blocks)} blocs extraits, questions={distinct_titles}",
        )
    return blocks


# ================================================================================
# FORSTA/CONFIRMIT - HORIZONTAL RATING SCALE SINGLE (cf-hrs-single)
# ================================================================================

def _extract_confirmit_cf_hrs_single_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Forsta/Confirmit Horizontal Rating Scale Single: 1 div.cf-hrs-single = 1 bloc radio.

    Gate DOM strict:
    - présence de div.cf-hrs-single[role='radiogroup']
    - présence de div.cf-horizontal-rating-item[role='radio'] dans ce conteneur

    Extrait la question depuis les éléments référencés par aria-labelledby du radiogroup.
    Extrait les options depuis l'aria-label (ou innerText) de chaque
    div.cf-horizontal-rating-item[role='radio'].
    """
    frame_chain = list(frame_chain or [])

    try:
        radiogroups = driver.find_elements(
            By.CSS_SELECTOR, "div.cf-hrs-single[role='radiogroup']"
        )
    except Exception:
        return []
    if not radiogroups:
        return []

    # Gate: au moins un radiogroup contient des items radio
    gate_ok = False
    for rg in radiogroups:
        try:
            if rg.find_elements(By.CSS_SELECTOR, "div.cf-horizontal-rating-item[role='radio']"):
                gate_ok = True
                break
        except Exception:
            continue
    if not gate_ok:
        return []

    blocks: list[dict] = []

    for rg in radiogroups[:20]:  # budget anti-explosion
        try:
            labelledby = (rg.get_attribute("aria-labelledby") or "").strip()

            # --- Gate carousel : cf-hrs-single enfant de cf-carousel__content-item ---
            carousel_item_id = None
            try:
                carousel_content_item = rg.find_element(
                    By.XPATH,
                    "ancestor::div[contains(concat(' ',normalize-space(@class),' '),"
                    "' cf-carousel__content-item ')][1]"
                )
                carousel_item_id = (carousel_content_item.get_attribute("id") or "").strip()
            except Exception:
                pass

            if carousel_item_id:
                # --- Mode carousel : seulement le card courant (--current) ---
                carousel_classes = (carousel_content_item.get_attribute("class") or "")
                if "cf-carousel__content-item--current" not in carousel_classes:
                    continue  # card non-courant : ignoré cette itération

                # Position dans le carousel (index 0-based, total, is_last)
                carousel_index = 0
                carousel_total = 1
                try:
                    carousel_content_root = carousel_content_item.find_element(
                        By.XPATH, "parent::div"
                    )
                    all_items = carousel_content_root.find_elements(
                        By.CSS_SELECTOR, "div.cf-carousel__content-item"
                    )
                    carousel_total = len(all_items) if all_items else 1
                    for idx, ci in enumerate(all_items):
                        if (ci.get_attribute("id") or "").strip() == carousel_item_id:
                            carousel_index = idx
                            break
                except Exception:
                    pass
                is_last_carousel_item = (carousel_index == carousel_total - 1)

                # item_id = e.g. "Q3_1" (strip suffix "_carousel_content")
                item_id = carousel_item_id.replace("_carousel_content", "")
                question = ""
                try:
                    span = driver.find_element(By.ID, f"{item_id}_text")
                    question = _norm(span.text or span.get_attribute("innerText") or "")
                except Exception:
                    pass
                if not question:
                    continue

                # Préfixe : texte de la question globale (div.cf-question__text)
                try:
                    ancestor = rg.find_element(
                        By.XPATH,
                        "ancestor::div[contains(concat(' ',normalize-space(@class),' '),"
                        "' cf-question ')][1]"
                    )
                    q_text_el = ancestor.find_element(By.CSS_SELECTOR, "div.cf-question__text")
                    parent_q = _norm(q_text_el.get_attribute("textContent") or q_text_el.text or "")
                    if parent_q:
                        question = f"{parent_q} – {question}"
                except Exception:
                    pass

                # Options : innerText uniquement (aria-label contient le texte de ligne en préfixe)
                try:
                    item_divs = rg.find_elements(
                        By.CSS_SELECTOR, "div.cf-horizontal-rating-item[role='radio']"
                    )
                except Exception:
                    item_divs = []
                if not item_divs:
                    continue

                options: list[str] = []
                option_xpath_map: dict[str, str] = {}
                for item in item_divs[:30]:
                    try:
                        text = _norm(item.text or item.get_attribute("innerText") or "")
                        if not text:
                            continue
                        if text not in options:
                            options.append(text)
                        ctrl_id = (item.get_attribute("id") or "").strip()
                        if ctrl_id:
                            option_xpath_map[_norm_key(text)] = f"//*[@id={_xpath_literal(ctrl_id)}]"
                        else:
                            xp = _best_xpath_for_element(item)
                            if xp:
                                option_xpath_map[_norm_key(text)] = xp
                    except Exception:
                        continue

                if not options:
                    continue

                group_key = f"radio:name:dom:{labelledby}|cf-hrs-single|{item_id}"
                _carousel_ctx = {
                    "confirmit_cf_hrs_single_carousel": True,
                    "carousel_index": carousel_index,
                    "carousel_total": carousel_total,
                    "is_last_carousel_item": is_last_carousel_item,
                }

            else:
                # --- Mode standalone (hors carousel) : comportement inchangé ---
                question = ""
                if labelledby:
                    for ref_id in labelledby.split():
                        try:
                            node = driver.find_element(By.ID, ref_id)
                            txt = _norm(node.text or node.get_attribute("innerText") or "")
                            if txt:
                                question = txt
                                break
                        except Exception:
                            continue
                if not question:
                    continue

                # Prepend matrix/grid parent question text from ancestor div.cf-question__text
                try:
                    ancestor = rg.find_element(
                        By.XPATH,
                        "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' cf-question ')][1]"
                    )
                    q_text_el = ancestor.find_element(By.CSS_SELECTOR, "div.cf-question__text")
                    parent_q = _norm(q_text_el.get_attribute("textContent") or q_text_el.text or "")
                    if parent_q:
                        question = f"{parent_q} – {question}"
                except Exception:
                    pass

                try:
                    item_divs = rg.find_elements(
                        By.CSS_SELECTOR, "div.cf-horizontal-rating-item[role='radio']"
                    )
                except Exception:
                    item_divs = []
                if not item_divs:
                    continue

                options: list[str] = []
                option_xpath_map: dict[str, str] = {}
                for item in item_divs[:30]:  # budget anti-explosion
                    try:
                        aria_label = _norm(item.get_attribute("aria-label") or "")
                        text = aria_label or _norm(item.text or item.get_attribute("innerText") or "")
                        if not text:
                            continue
                        if text not in options:
                            options.append(text)
                        ctrl_id = (item.get_attribute("id") or "").strip()
                        if ctrl_id:
                            option_xpath_map[_norm_key(text)] = f"//*[@id={_xpath_literal(ctrl_id)}]"
                        else:
                            xp = _best_xpath_for_element(item)
                            if xp:
                                option_xpath_map[_norm_key(text)] = xp
                    except Exception:
                        continue

                if not options:
                    continue

                group_key = f"radio:name:dom:{labelledby}|cf-hrs-single"
                _carousel_ctx = {}
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
                    "confirmit_cf_hrs_single": True,
                    **_carousel_ctx,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "min_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        **_carousel_ctx,
                    },
                }
            )
            log_debug(
                "[DOM_CONFIRMIT_CF_HRS_SINGLE]",
                f"labelledby={labelledby!r} question={question!r} options={options} carousel_ctx={_carousel_ctx}",
            )
        except Exception:
            continue

    return blocks


# ================================================================================
# GROUPCALIBER / IPSOS BOOTSTRAP – RATING ROWS (data-question_type="5")
# ================================================================================

_CALIBER_RADIO_NAME_RE = re.compile(r"^\d+_\d+$")


def _extract_groupcaliber_rating_row_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    GroupCaliber/IPSOS Bootstrap – matrices rating rendues en lignes Bootstrap.

    Pattern DOM ciblé (gate strict) :
    - h6[data-question_type="5"] dans un .card-header (intitulé global de la page)
    - Pour chaque entité : div.row.bg-light contenant :
        - div.col-md-3 > b  : nom de la marque/entité
        - N × label > input[type="radio"][name="\\d+_\\d+"] : options de rating

    Produit 1 bloc radio par div.row.bg-light (1 par entité).
    """
    frame_chain = list(frame_chain or [])

    # Gate 1: h6[data-question_type="5"] must be present
    try:
        headers = driver.find_elements(By.CSS_SELECTOR, "h6[data-question_type='5']")
    except Exception:
        return []
    if not headers:
        return []

    # Gate 2: at least one div.row.bg-light must be present
    try:
        rows = driver.find_elements(By.CSS_SELECTOR, "div.row.bg-light")
    except Exception:
        return []
    if not rows:
        return []

    blocks: list[dict] = []

    for row in rows[:50]:  # budget anti-explosion
        try:
            # Brand name from div.col-md-3 > b
            brand_name = ""
            try:
                b_els = row.find_elements(By.CSS_SELECTOR, "div.col-md-3 b")
                if b_els:
                    brand_name = _norm(b_els[0].text or b_els[0].get_attribute("innerText") or "")
            except Exception:
                pass

            if not brand_name:
                continue

            # Find all radios in this row
            try:
                radios = row.find_elements(By.CSS_SELECTOR, "input[type='radio']")
            except Exception:
                radios = []

            if not radios:
                continue

            # Determine radio group name (all radios in a row share the same name)
            radio_name = ""
            for r in radios:
                try:
                    n = (r.get_attribute("name") or "").strip()
                    if _CALIBER_RADIO_NAME_RE.match(n):
                        radio_name = n
                        break
                except Exception:
                    continue

            if not radio_name:
                continue

            # Extract options and build option_xpath_map
            options: list[str] = []
            option_xpath_map: dict[str, str] = {}
            name_lit = _xpath_literal(radio_name)

            for radio in radios[:30]:  # budget anti-explosion
                try:
                    rname = (radio.get_attribute("name") or "").strip()
                    if rname != radio_name:
                        continue

                    # Label text: the radio is wrapped in <label>
                    label_txt = ""
                    label_raw = ""  # NFC form for XPath literal (DOM-compatible)
                    try:
                        label_el = radio.find_element(By.XPATH, "ancestor::label[1]")
                        raw = (label_el.text or label_el.get_attribute("innerText") or "")
                        # Preserve NFC form for XPath (NFKD form breaks contains() on accented chars)
                        label_raw = re.sub(r"\s+", " ", raw).strip()
                        label_txt = _norm(label_raw)  # NFKD for dict key (matches v_norm in apply)
                    except Exception:
                        pass

                    if not label_txt:
                        continue

                    nk = _norm_key(label_txt)
                    if nk in option_xpath_map:
                        continue

                    # Use NFC label_raw for XPath literal so contains() matches the DOM
                    label_lit = _xpath_literal(label_raw or label_txt)
                    xpath = (
                        f"(//label[.//input[@type='radio' and @name={name_lit}]"
                        f" and contains(normalize-space(), {label_lit})])[1]"
                    )
                    option_xpath_map[nk] = xpath
                    options.append(label_txt)
                except Exception:
                    continue

            if len(options) < 2:
                continue

            group_key = f"radio:name:{radio_name}"
            target_id = make_target_id("group", group_key, brand_name)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": brand_name,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                    "groupcaliber_rating": True,
                },
            )

            blocks.append(
                {
                    "question": brand_name,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "min_select": 1,
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )

            log_debug(
                "[DOM_CALIBER_RATING]",
                f"brand={brand_name!r} group_key={group_key!r} options={len(options)}",
            )

        except Exception:
            continue

    return blocks


# ================================================================================
# FORSTA/CONFIRMIT - CF-CAROUSEL (div.cf-carousel + div.cf-carousel__content-item)
# ================================================================================

def _extract_confirmit_cf_carousel_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Forsta/Confirmit CF carousel: 1 bloc radio par item du carousel.

    Gate DOM strict:
    - présence de div.cf-carousel
    - présence de div.cf-carousel__content-item contenant div.cf-answer-button

    Pour chaque item:
    - question = texte de div.cf-question__text + texte du span d'affirmation propre à l'item
    - options = textes des div.cf-answer-button > div.cf-answer-button__text
    - target_id = id du premier div.cf-answer-button de l'item (ex: qatt0_1_1)
    - pre_click_xpaths = [paging button de l'item] pour naviguer avant de cliquer
    """
    frame_chain = list(frame_chain or [])

    # Gate 1: div.cf-carousel présent
    try:
        carousels = driver.find_elements(By.CSS_SELECTOR, "div.cf-carousel")
    except Exception:
        return []
    if not carousels:
        return []

    # Gate 2: au moins un item contient des boutons de réponse
    # Deux variantes connues : div.cf-answer-button (ancienne) et div.cf-button-answer (nouvelle)
    gate_ok = False
    for c in carousels:
        try:
            has_old = bool(c.find_elements(By.CSS_SELECTOR, "div.cf-carousel__content-item div.cf-answer-button"))
            has_new = bool(c.find_elements(By.CSS_SELECTOR, "div.cf-carousel__content-item div.cf-button-answer"))
            if has_old or has_new:
                gate_ok = True
                break
        except Exception:
            continue
    if not gate_ok:
        return []

    # Texte de question principal (div.cf-question__text)
    main_question = ""
    try:
        q_els = driver.find_elements(By.CSS_SELECTOR, "div.cf-question__text")
        for q_el in q_els[:3]:
            txt = _norm(q_el.text or q_el.get_attribute("innerText") or "")
            if txt:
                main_question = txt
                break
    except Exception:
        pass

    # Image partagée par tous les items (div.cf-question__text > img)
    carousel_image_url = ""
    try:
        img_els = driver.find_elements(By.CSS_SELECTOR, "div.cf-question__text img")
        for img_el in img_els[:3]:
            src = (img_el.get_attribute("src") or "").strip()
            if src and src.startswith("http"):
                carousel_image_url = src
                break
    except Exception:
        pass

    blocks: list[dict] = []

    for carousel in carousels[:5]:  # budget anti-explosion
        try:
            items = carousel.find_elements(
                By.CSS_SELECTOR, "div.cf-carousel__content-item"
            )
        except Exception:
            continue

        for idx, item in enumerate(items[:50], start=1):  # budget anti-explosion
            try:
                # Dériver item_id depuis l'id de l'élément (ex: qatt0_1_carousel_content -> qatt0_1)
                raw_id = (item.get_attribute("id") or "").strip()
                item_id = raw_id.replace("_carousel_content", "") if raw_id.endswith("_carousel_content") else raw_id
                if not item_id:
                    continue

                # Texte d'affirmation propre à l'item (span#{item_id}_text)
                affirmation = ""
                try:
                    span = driver.find_element(By.ID, f"{item_id}_text")
                    affirmation = _norm(span.get_attribute("textContent") or "")
                except Exception:
                    pass

                question = f"{main_question} {affirmation}".strip() if affirmation else main_question
                if not question:
                    continue

                # Boutons de réponse dans cet item — deux variantes DOM :
                # ancienne : div.cf-answer-button > div.cf-answer-button__text
                # nouvelle : div.cf-button-answer > div.cf-button.cf-button-answer__button[role="radio"]
                #            > div.cf-button-answer__text  (cliquable : id = btn_id + "_control")
                try:
                    btn_divs = item.find_elements(By.CSS_SELECTOR, "div.cf-answer-button")
                    use_new_variant = False
                    if not btn_divs:
                        btn_divs = item.find_elements(By.CSS_SELECTOR, "div.cf-button-answer")
                        use_new_variant = True
                except Exception:
                    btn_divs = []
                    use_new_variant = False
                if not btn_divs:
                    continue

                options: list[str] = []
                option_xpath_map: dict[str, str] = {}
                first_btn_id = ""

                for btn in btn_divs[:20]:  # budget anti-explosion
                    try:
                        btn_id = (btn.get_attribute("id") or "").strip()
                        opt_text = ""
                        if use_new_variant:
                            # Texte depuis div.cf-button-answer__text
                            try:
                                txt_el = btn.find_element(By.CSS_SELECTOR, "div.cf-button-answer__text")
                                opt_text = _norm(txt_el.get_attribute("textContent") or "")
                            except Exception:
                                pass
                            # Élément cliquable : div avec role="radio", id = btn_id + "_control"
                            click_id = f"{btn_id}_control" if btn_id else ""
                        else:
                            # Texte depuis div.cf-answer-button__text (variante ancienne)
                            try:
                                txt_el = btn.find_element(By.CSS_SELECTOR, "div.cf-answer-button__text")
                                opt_text = _norm(txt_el.get_attribute("textContent") or "")
                            except Exception:
                                pass
                            click_id = btn_id
                        if not opt_text:
                            opt_text = _norm(btn.get_attribute("textContent") or "")
                        if not opt_text:
                            continue

                        if not first_btn_id and btn_id:
                            first_btn_id = btn_id

                        nk = _norm_key(opt_text)
                        if nk not in option_xpath_map:
                            options.append(opt_text)
                            if click_id:
                                option_xpath_map[nk] = f"//*[@id={_xpath_literal(click_id)}]"
                            else:
                                xp = _best_xpath_for_element(btn)
                                if xp:
                                    option_xpath_map[nk] = xp
                    except Exception:
                        continue

                if not options or not option_xpath_map:
                    continue

                # target_id = id du premier bouton de l'item
                target_id = first_btn_id if first_btn_id else make_target_id(
                    "cf_carousel_item", f"{item_id}", question
                )

                # pre_click_xpath: cliquer le paging button pour naviguer vers cet item
                paging_id = f"{item_id}_carousel_paging"
                pre_click_xpaths = [f"//*[@id={_xpath_literal(paging_id)}]"]

                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "itype": "radio",
                        "group_key": f"cf_carousel_item:{item_id}",
                        "question": question,
                        "option_xpath_map": option_xpath_map,
                        "pre_click_xpaths": pre_click_xpaths,
                        "frame_chain": frame_chain,
                        "cf_carousel_item": True,
                        "carousel_item_id": item_id,
                        "item_index": idx,
                    },
                )

                block = {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "min_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "cf_carousel_item",
                        "carousel_item_id": item_id,
                        "item_index": idx,
                    },
                }
                if carousel_image_url:
                    block["image_url"] = carousel_image_url
                blocks.append(block)

                log_debug(
                    "[DOM_CONFIRMIT_CF_CAROUSEL]",
                    f"item_id={item_id!r} idx={idx} question={question!r} options={options}",
                )
            except Exception:
                continue

    return blocks


# ================================================================================
# RPS-SELECT — ANGULAR CUSTOM DROPDOWN (Toluna / SurveyRouter screener)
# ================================================================================

def _extract_rps_select_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extrait les dropdowns custom Angular `rps-select` (Toluna/SurveyRouter screener).

    Gardes-fous DOM stricts (additifs, non provider-based) :
      - présence d'au moins un élément `rps-select` visible
      - chaque `rps-select` contient un `div.rps-select[data-selector]` non vide
      - présence de `div.selection` cliquable
      - au moins deux `div.option-item` dans le composant (même dans ng-hide)

    Note DOM : `data-selector` est sur le `div.rps-select` interne, pas sur `rps-select`.
    Les options sont dans `div.options.ng-hide` et ne sont pas individuellement visibles
    avant ouverture — on les lit via innerText sans vérification de visibilité.
    """
    frame_chain = list(frame_chain or [])

    # Gate rapide : présence du tag personnalisé
    try:
        rps_selects = driver.find_elements(By.CSS_SELECTOR, "rps-select")
    except Exception:
        return []

    if not rps_selects:
        return []

    # Gate strict : au moins un wrapper interne porte data-selector + option-items
    try:
        gate_wrappers = driver.find_elements(By.CSS_SELECTOR, "div.rps-select[data-selector]")
    except Exception:
        gate_wrappers = []

    has_gate = False
    for w in gate_wrappers:
        try:
            items = w.find_elements(By.CSS_SELECTOR, "div.option-item")
            if len(items) >= 2:
                has_gate = True
                break
        except Exception:
            continue
    if not has_gate:
        return []

    blocks: list[dict] = []

    for outer in rps_selects:
        try:
            # Vérifier que l'élément rps-select est dans le DOM et visible
            try:
                if not outer.is_displayed():
                    continue
            except Exception:
                continue

            # Trouver le wrapper interne portant data-selector
            try:
                wrapper = outer.find_element(By.CSS_SELECTOR, "div.rps-select[data-selector]")
            except Exception:
                continue

            data_selector = (wrapper.get_attribute("data-selector") or "").strip()
            if not data_selector:
                continue

            # Readonly check (attribut sur wrapper interne)
            if (wrapper.get_attribute("data-is-readonly") or "").strip().lower() == "true":
                continue

            # Gate : div.selection présent
            try:
                wrapper.find_element(By.CSS_SELECTOR, "div.selection")
            except Exception:
                continue

            # Lire les options (dans div.options, potentiellement ng-hide) via JS innerText
            try:
                option_items = wrapper.find_elements(By.CSS_SELECTOR, "div.option-item")
            except Exception:
                option_items = []

            options: list[str] = []
            for item in option_items:
                try:
                    txt = _norm(
                        driver.execute_script("return arguments[0].innerText || '';", item)
                        or item.get_attribute("innerText")
                        or ""
                    )
                    if txt:
                        options.append(txt)
                except Exception:
                    continue

            if len(options) < 2:
                continue

            # Question : label.select-label
            question = ""
            try:
                lbl = wrapper.find_element(By.CSS_SELECTOR, "label.select-label")
                question = _norm(
                    driver.execute_script("return arguments[0].innerText || '';", lbl)
                    or lbl.get_attribute("innerText")
                    or ""
                )
            except Exception:
                pass
            if not question:
                question = f"Question {data_selector}"

            # XPaths ancrés sur div[@data-selector] (plus stable que rps-select)
            ds_lit = _xpath_literal(data_selector)
            selection_xpath = (
                f"//div[@data-selector={ds_lit}]"
                f"//div[contains(@class,'selection')]"
            )

            option_xpath_map: dict[str, str] = {}
            for opt_txt in options:
                k = _norm_lc(opt_txt)
                if not k or k in option_xpath_map:
                    continue
                opt_lit = _xpath_literal(opt_txt)
                xp = (
                    f"//div[@data-selector={ds_lit}]"
                    f"//div[contains(@class,'options')]"
                    f"//div[contains(@class,'option-item') and normalize-space(.)={opt_lit}]"
                )
                option_xpath_map[k] = xp

            if len(option_xpath_map) < 2:
                continue

            group_key = f"rps_select:{data_selector}"
            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "select_rps",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "selection_xpath": selection_xpath,
                    "frame_chain": frame_chain,
                    "rps_select": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "select_rps",
                    "options": options,
                    "max_select": 1,
                    "min_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                    },
                }
            )

            log_debug(
                "[DOM_RPS_SELECT]",
                f"data_selector={data_selector!r} question={question!r} options={options}",
            )

        except Exception as e:
            log_debug("[DOM_RPS_SELECT]", f"extract error: {type(e).__name__}: {e}")
            continue

    return blocks


# ================================================================================
# SSI / CONFIRMIT NATIVE GRID
# ================================================================================

def _extract_ssi_confirmit_native_grid_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    SSI/Confirmit native radio grid: div.question.grid > table.inner_table.

    Gate DOM (strict):
    - div.question.grid
    - table.inner_table tr.column_header_row td[role="columnheader"] (>= 2 colonnes)
    - tr[role="radiogroup"] td[role="rowheader"]
    - td.input_cell.clickable avec div.graphical_select[role="radio"] + input[type="radio"]

    Chaque ligne (tr[role="radiogroup"]) génère un block avec:
    - question = texte h3 de .header2 + " — " + libellé de ligne
    - options = textes des en-têtes de colonnes
    - option_xpath_map = { norm(option) -> xpath du div.graphical_select }
    """
    frame_chain = list(frame_chain or [])

    try:
        grids = driver.find_elements(By.CSS_SELECTOR, "div.question.grid")
        if not grids:
            return []
    except Exception:
        return []

    blocks: list[dict] = []

    for grid_div in grids[:5]:
        try:
            col_header_cells = grid_div.find_elements(
                By.CSS_SELECTOR,
                "table.inner_table tr.column_header_row td[role='columnheader']",
            )
            if len(col_header_cells) < 2:
                continue

            col_headers: list[str] = []
            for th in col_header_cells:
                txt = _norm(th.text or "")
                if txt:
                    col_headers.append(txt)
            if len(col_headers) < 2:
                continue

            global_q = ""
            try:
                for sel in (".header2 h3", ".header2 p", "h3"):
                    nodes = grid_div.find_elements(By.CSS_SELECTOR, sel)
                    for node in nodes:
                        t = _norm(node.text or "")
                        if t and len(t) > 5:
                            global_q = t
                            break
                    if global_q:
                        break
            except Exception:
                pass

            rows = grid_div.find_elements(
                By.CSS_SELECTOR, "table.inner_table tr[role='radiogroup']"
            )
            if not rows:
                continue

            for row in rows[:30]:
                try:
                    row_label = ""
                    try:
                        rh = row.find_element(By.CSS_SELECTOR, "td[role='rowheader']")
                        row_label = _norm(rh.text or "")
                    except Exception:
                        pass
                    if not row_label:
                        continue

                    question = f"{global_q} — {row_label}" if global_q else row_label

                    input_cells = row.find_elements(By.CSS_SELECTOR, "td.input_cell.clickable")
                    if len(input_cells) < 2:
                        continue

                    radio_name = ""
                    try:
                        first_input = input_cells[0].find_element(
                            By.CSS_SELECTOR, "input[type='radio']"
                        )
                        radio_name = _norm_lc(first_input.get_attribute("name") or "")
                    except Exception:
                        pass
                    if not radio_name:
                        continue

                    options: list[str] = []
                    option_xpath_map: dict[str, str] = {}
                    num_opts = min(len(col_headers), len(input_cells))

                    for idx in range(num_opts):
                        try:
                            opt_text = col_headers[idx]
                            cell = input_cells[idx]

                            click_el = None
                            try:
                                click_el = cell.find_element(
                                    By.CSS_SELECTOR, "div.graphical_select[role='radio']"
                                )
                            except Exception:
                                click_el = cell

                            xp = _best_xpath_for_element(driver, click_el)
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
                        continue

                    group_key = f"radio:name:{radio_name}"
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
                            "ssi_confirmit_native_grid": True,
                        },
                    )

                    blocks.append(
                        {
                            "question": question,
                            "itype": "radio",
                            "options": options,
                            "max_select": 1,
                            "min_select": 1,
                            "target_id": target_id,
                            "context": {
                                "kind": "group",
                                "group_key": group_key,
                            },
                        }
                    )

                    log_debug(
                        "[SSI_GRID]",
                        f"row={row_label!r} name={radio_name!r} options={options}",
                    )

                except Exception:
                    continue

        except Exception:
            continue

    return blocks


def _extract_gfk_accordion_radio_rows(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait les matrices radio GfK mrIWeb rendues en accordéon Angular (ng-app="GfKMD").

    Gate DOM strict (additif):
      - div.acc_ct contenant au moins 2 div.acc-element[question-number][statement-number]
      - chaque acc-element contient des input.mrSingle[type='radio'] dans div.acc-answers
    """
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.acc_ct")
    except Exception:
        return blocks

    if not containers:
        return blocks

    for container in containers:
        try:
            acc_elements = container.find_elements(
                By.CSS_SELECTOR,
                "div.acc-element[question-number][statement-number]",
            )
            if len(acc_elements) < 2:
                continue

            # Gate: at least one mrSingle radio must exist inside
            gate = container.find_elements(
                By.CSS_SELECTOR, "div.acc-element input.mrSingle[type='radio']"
            )
            if not gate:
                continue

            # Main question text (span.mrQuestionText with id starting "qt")
            main_question = ""
            try:
                q_nodes = driver.find_elements(
                    By.CSS_SELECTOR, "span.mrQuestionText[id^='qt']"
                )
                if q_nodes:
                    main_question = _norm(
                        q_nodes[0].text or q_nodes[0].get_attribute("innerText") or ""
                    )
            except Exception:
                main_question = ""

            candidate_rows: list[dict] = []

            for acc_el in acc_elements:
                try:
                    stmt_nodes = acc_el.find_elements(
                        By.CSS_SELECTOR, "div.statement-text span.mrQuestionText"
                    )
                    if not stmt_nodes:
                        continue
                    row_label = _norm(
                        stmt_nodes[0].text or stmt_nodes[0].get_attribute("innerText") or ""
                    )
                    if not row_label:
                        continue

                    q_num = acc_el.get_attribute("question-number") or "0"
                    s_num = acc_el.get_attribute("statement-number") or "0"

                    ans_items = acc_el.find_elements(
                        By.CSS_SELECTOR, "div.acc-answers div.acc-ans-item"
                    )
                    if len(ans_items) < 2:
                        continue

                    options: list[str] = []
                    option_xpath_map: dict[str, str] = {}

                    for item_idx, item in enumerate(ans_items):
                        try:
                            label_nodes = item.find_elements(
                                By.CSS_SELECTOR, "span.mrQuestionText"
                            )
                            if label_nodes:
                                label_txt = _norm(
                                    label_nodes[0].text
                                    or label_nodes[0].get_attribute("innerText")
                                    or ""
                                )
                            else:
                                label_txt = _norm(
                                    item.text or item.get_attribute("innerText") or ""
                                )
                            if not label_txt:
                                continue

                            key = _norm_key(label_txt)
                            if key in option_xpath_map:
                                continue

                            # Click the acc-ans-item div (Angular ng-click handler).
                            # Anchor via radio input id inside the item for maximum stability.
                            radio_in_item = item.find_elements(
                                By.CSS_SELECTOR, "input.mrSingle[type='radio']"
                            )
                            if radio_in_item:
                                rid = (radio_in_item[0].get_attribute("id") or "").strip()
                                if rid:
                                    xp = (
                                        f"//input[@id={_xpath_literal(rid)}]"
                                        f"/ancestor::div[contains(concat(' ',normalize-space(@class),' '),"
                                        f"' acc-ans-item ')][1]"
                                    )
                                else:
                                    xp = (
                                        f"//div[contains(concat(' ',normalize-space(@class),' '),' acc-element ')]"
                                        f"[@question-number='{q_num}'][@statement-number='{s_num}']"
                                        f"//div[contains(concat(' ',normalize-space(@class),' '),' acc-answers ')]"
                                        f"//div[contains(concat(' ',normalize-space(@class),' '),' acc-ans-item ')]"
                                        f"[{item_idx + 1}]"
                                    )
                            else:
                                continue

                            option_xpath_map[key] = xp
                            options.append(label_txt)
                        except Exception:
                            continue

                    if len(options) < 2:
                        continue

                    # pre_click: expand button — only needed if section is currently collapsed
                    expand_xp = None
                    acc_classes = (acc_el.get_attribute("class") or "").lower()
                    is_expanded = "border_blue" in acc_classes
                    if not is_expanded:
                        try:
                            btn = acc_el.find_element(By.CSS_SELECTOR, "button.acc_top_button")
                            expand_xp = _best_xpath_for_element(driver, btn)
                        except Exception:
                            expand_xp = None

                    radio_name = ""
                    try:
                        first_radio = acc_el.find_element(
                            By.CSS_SELECTOR, "input.mrSingle[type='radio']"
                        )
                        radio_name = (first_radio.get_attribute("name") or "").strip()
                    except Exception:
                        pass

                    group_key = (
                        f"gfk_acc:{radio_name}" if radio_name else f"gfk_acc:{q_num}:{s_num}"
                    )
                    row_question = (
                        f"{main_question} \u2014 {row_label}" if main_question else row_label
                    )
                    target_id = make_target_id("group", group_key, row_question)

                    payload: dict = {
                        "kind": "group",
                        "itype": "radio",
                        "group_key": group_key,
                        "question": row_question,
                        "option_xpath_map": option_xpath_map,
                        "frame_chain": frame_chain or [],
                        "gfk_accordion": True,
                    }
                    if expand_xp:
                        payload["pre_click_xpaths"] = [expand_xp]

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
                                "gfk_accordion": True,
                            },
                        }
                    )
                except Exception:
                    continue

            if candidate_rows:
                blocks.extend(candidate_rows)
        except Exception:
            continue

    if blocks:
        log_debug("[DOM_GFK_ACCORDION]", f"rows_extracted={len(blocks)}")

    return blocks


# ================================================================================
# ASKIA STATEMENTLIST - widget propriétaire AskiaExt (adc-statementList)
# ================================================================================

def _extract_askia_statement_list_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait la question courante du widget Askia StatementList (div.adc-statementList).

    Structure DOM cible :
      div[class*='adc-statementList']
        div.statement > span.statement_text[data-id]   ← items rotatifs (1 visible)
        div.responseItem[data-value][data-id]           ← options cliquables

    Gate DOM strict (additif, ne casse pas les autres extracteurs) :
      - présence d'un div[class*='adc-statementList']
      - ET au moins un div.responseItem[data-value] dans ce conteneur
      - ET au moins un span.statement_text[data-id] dans ce conteneur

    Produit UN bloc pour le statement actuellement visible (style != display:none).
    L'extracteur est appelé à chaque analyse DOM → le bot traite un statement à la fois.
    """
    blocks: list[dict] = []

    # Gate 1 : conteneur adc-statementList présent
    try:
        containers = driver.find_elements(
            By.CSS_SELECTOR, "div[class*='adc-statementList']"
        )
    except Exception:
        return blocks

    if not containers:
        return blocks

    for container in containers:
        try:
            # Gate 2 : au moins un responseItem[data-value]
            response_items = container.find_elements(
                By.CSS_SELECTOR, "div.responseItem[data-value]"
            )
            if not response_items:
                continue

            # Gate 3 : au moins un statement_text[data-id]
            all_statements = container.find_elements(
                By.CSS_SELECTOR, "span.statement_text[data-id]"
            )
            if not all_statements:
                continue

            # Trouver le statement actuellement visible (style vide ou pas display:none)
            visible_statement = None
            for stmt in all_statements:
                try:
                    style = (stmt.get_attribute("style") or "").lower()
                    if "display:none" not in style.replace(" ", "") and "display: none" not in style:
                        visible_statement = stmt
                        break
                except Exception:
                    continue

            if visible_statement is None:
                # Fallback : premier statement
                visible_statement = all_statements[0]

            current_stmt_text = _norm(
                visible_statement.text
                or visible_statement.get_attribute("innerText")
                or ""
            )
            if not current_stmt_text:
                continue

            # Texte de la question globale : td[class*='askia-caption'] ou td.askia-question-label
            global_question = ""
            try:
                q_nodes = driver.find_elements(
                    By.CSS_SELECTOR,
                    "td.askia-question-label, td[class*='askia-caption']",
                )
                for qn in q_nodes:
                    raw = _norm(qn.text or qn.get_attribute("innerText") or "")
                    if raw:
                        global_question = raw
                        break
            except Exception:
                global_question = ""

            question = (
                f"{global_question} — {current_stmt_text}"
                if global_question
                else current_stmt_text
            )

            # Construire option_xpath_map : option_text → xpath du div.responseItem
            container_id = (container.get_attribute("id") or "").strip()
            options: list[str] = []
            option_xpath_map: dict[str, str] = {}

            for item in response_items:
                try:
                    data_id = (item.get_attribute("data-id") or "").strip()
                    span_nodes = item.find_elements(By.CSS_SELECTOR, "span.response_text")
                    label_txt = ""
                    if span_nodes:
                        label_txt = _norm(
                            span_nodes[0].text
                            or span_nodes[0].get_attribute("innerText")
                            or ""
                        )
                    if not label_txt:
                        label_txt = _norm(item.text or item.get_attribute("innerText") or "")
                    if not label_txt:
                        continue

                    key = _norm_key(label_txt)
                    if key in option_xpath_map:
                        continue

                    # XPath le plus stable : conteneur par id + responseItem par data-id
                    if container_id and data_id:
                        xp = (
                            f"//*[@id={_xpath_literal(container_id)}]"
                            f"//div[contains(concat(' ',normalize-space(@class),' '),' responseItem ')]"
                            f"[@data-id={_xpath_literal(data_id)}]"
                        )
                    elif data_id:
                        xp = (
                            f"//div[contains(concat(' ',normalize-space(@class),' '),' adc-statementList ')]"
                            f"//div[contains(concat(' ',normalize-space(@class),' '),' responseItem ')]"
                            f"[@data-id={_xpath_literal(data_id)}]"
                        )
                    else:
                        xp = _best_xpath_for_element(driver, item)

                    if not xp:
                        continue

                    option_xpath_map[key] = xp
                    options.append(label_txt)
                except Exception:
                    continue

            if len(options) < 2:
                continue

            current_data_id = (visible_statement.get_attribute("data-id") or "").strip()
            group_key = (
                f"askia_stmtlist:{container_id}:stmt{current_data_id}"
                if container_id
                else f"askia_stmtlist:stmt{current_data_id}"
            )

            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "checkbox",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": list(frame_chain or []),
                    "askia_statement_list": True,
                    "container_id": container_id,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "checkbox",
                    "options": options,
                    "max_select": len(options),
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "askia_statement_list": True,
                    },
                }
            )

        except Exception:
            continue

    if blocks:
        log_debug("[DOM_ASKIA_STMT]", f"blocks_extracted={len(blocks)}")

    return blocks

# ================================================================================
# QUALTRICS - CHAMP TEXTE LIBRE (layout SL / type TE)
# ================================================================================

def _extract_qualtrics_sl_text_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction ciblée des champs texte libre Qualtrics layout SL (type TE).

    Gate DOM strict (additif) :
    - div.QuestionOuter contenant div.Inner.SL
    - input[type="TEXT"][name^="QR~"] unique dans le conteneur

    Ne couvre PAS les radios, checkboxes, dropdowns ou matrix —
    ceux-ci restent gérés par leurs extracteurs respectifs.
    """
    frame_chain = list(frame_chain or [])
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.QuestionOuter")
    except Exception:
        return blocks

    for idx, container in enumerate(containers):
        # Gate strict : div.Inner.SL doit être présent
        try:
            sl_inner = container.find_elements(By.CSS_SELECTOR, "div.Inner.SL")
        except Exception:
            sl_inner = []
        if not sl_inner:
            continue

        # Un seul input[type="TEXT"][name^="QR~"] attendu
        try:
            inputs = container.find_elements(
                By.CSS_SELECTOR,
                "input[type='TEXT'][name^='QR~']",
            )
        except Exception:
            inputs = []
        if len(inputs) != 1:
            continue

        inp = inputs[0]
        inp_id = (inp.get_attribute("id") or "").strip()
        inp_name = (inp.get_attribute("name") or "").strip()
        if not inp_id and not inp_name:
            continue

        # Texte de la question : legend > label.QuestionText ou label.QuestionText
        question = ""
        for q_sel in (
            "fieldset legend label.QuestionText",
            "legend label.QuestionText",
            "label.QuestionText",
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

        single_key = f"qualtrics_sl_text:{inp_id}:{inp_name}"
        target_id = make_target_id("single", single_key, question)
        xpath = _best_xpath_for_element(driver, inp)

        alt_xpaths: list[str] = []
        try:
            if inp_name:
                alt_xpaths.append(f"//input[@name={_xpath_literal(inp_name)}]")
            if inp_id:
                alt_xpaths.append(f"//*[@id='{inp_id}']")
        except Exception:
            pass
        alt_xpaths = [x for x in dict.fromkeys(alt_xpaths) if x and x != xpath][:4]

        register_target(
            target_id,
            {
                "kind": "single",
                "itype": "text",
                "question": question,
                "xpath": xpath,
                "alt_xpaths": alt_xpaths,
                "tag": "input",
                "name": inp_name,
                "id": inp_id,
                "frame_chain": frame_chain,
                "qualtrics_sl_text": True,
            },
        )

        blocks.append(
            {
                "question": question,
                "itype": "text",
                "options": [],
                "max_select": 1,
                "min_select": 1,
                "target_id": target_id,
                "context": {
                    "kind": "single",
                    "tag": "input",
                    "name": inp_name,
                    "id": inp_id,
                    "qualtrics_sl_text": True,
                    "container_index": idx,
                },
            }
        )

    log_debug("[DOM_QUALTRICS_SL_TEXT]", f"blocks_extracted={len(blocks)}")
    return blocks


# ================================================================================
# FORSTA/CONFIRMIT - SINGLE-CHOICE VERTICAL LIST (cf-question--single + cf-list)
# ================================================================================

def _extract_confirmit_cf_single_choice_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Forsta/Confirmit single-choice question: div[role='radio'] in a vertical cf-list.

    Gate DOM strict (additif) :
    - présence de div.cf-question--single
    - au moins un div.cf-list contenant div.cf-radio[role='radio'] à l'intérieur
    - le conteneur ne doit PAS être dans une table.cf-table-layout
      (pattern grid couvert par _extract_confirmit_cf_desktop_grid_blocks)

    Structure ciblée (exemple id Q100) :
      div.cf-question--single#Q100
        div.cf-question__text#Q100_text          ← texte de la question
        div.cf-list
          div.cf-list__item
            div.cf-radio-answer
              div.cf-radio[role='radio']#Q100_1_control   ← cible du clic
              div.cf-radio-answer__text#Q100_1_text       ← texte de l'option
    """
    frame_chain = list(frame_chain or [])

    # Gate 1: au moins un div.cf-question--single présent
    try:
        q_containers = driver.find_elements(By.CSS_SELECTOR, "div.cf-question--single")
    except Exception:
        return []
    if not q_containers:
        return []

    # Gate 2: au moins un contient div.cf-list > div.cf-radio[role='radio']
    gate_ok = False
    for qc in q_containers:
        try:
            if qc.find_elements(By.CSS_SELECTOR, "div.cf-list div.cf-radio[role='radio']"):
                gate_ok = True
                break
        except Exception:
            continue
    if not gate_ok:
        return []

    blocks: list[dict] = []

    for qc in q_containers[:20]:  # budget anti-explosion
        try:
            # Exclure les containers imbriqués dans une table.cf-table-layout (grids)
            try:
                in_table = qc.find_elements(
                    By.XPATH,
                    "ancestor::table[contains(concat(' ',normalize-space(@class),' '),' cf-table-layout ')]"
                )
                if in_table:
                    continue
            except Exception:
                pass

            # Texte de la question depuis div.cf-question__text
            question = ""
            try:
                q_text_el = qc.find_element(By.CSS_SELECTOR, "div.cf-question__text")
                question = _norm(
                    q_text_el.get_attribute("textContent") or q_text_el.text or ""
                )
            except Exception:
                pass
            if not question:
                continue

            # Options depuis les items de la liste verticale
            try:
                list_items = qc.find_elements(
                    By.CSS_SELECTOR, "div.cf-list div.cf-list__item"
                )
            except Exception:
                list_items = []
            if not list_items:
                continue

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}

            for item in list_items[:40]:  # budget anti-explosion
                try:
                    # Texte de l'option depuis div.cf-radio-answer__text
                    opt_text = ""
                    try:
                        txt_el = item.find_element(By.CSS_SELECTOR, "div.cf-radio-answer__text")
                        opt_text = _norm(
                            txt_el.get_attribute("textContent") or txt_el.text or ""
                        )
                    except Exception:
                        pass
                    if not opt_text:
                        # Fallback : texte brut du cf-radio-answer
                        try:
                            ra = item.find_element(By.CSS_SELECTOR, "div.cf-radio-answer")
                            opt_text = _norm(ra.get_attribute("textContent") or ra.text or "")
                        except Exception:
                            pass
                    if not opt_text:
                        continue

                    # Cible du clic : div.cf-radio[role='radio']
                    ctrl_id = ""
                    try:
                        ctrl = item.find_element(By.CSS_SELECTOR, "div.cf-radio[role='radio']")
                        ctrl_id = (ctrl.get_attribute("id") or "").strip()
                    except Exception:
                        pass

                    nk = _norm_key(opt_text)
                    if nk in option_xpath_map:
                        continue
                    options.append(opt_text)
                    if ctrl_id:
                        option_xpath_map[nk] = f"//*[@id={_xpath_literal(ctrl_id)}]"
                    else:
                        try:
                            xp = _best_xpath_for_element(ctrl)
                            if xp:
                                option_xpath_map[nk] = xp
                        except Exception:
                            pass
                except Exception:
                    continue

            if len(options) < 2 or not option_xpath_map:
                continue

            q_id = (qc.get_attribute("id") or "").strip()
            group_key = f"radio:cf-single:{q_id}" if q_id else f"radio:cf-single:{question[:40]}"
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
                    "confirmit_cf_single": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "min_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                    },
                }
            )
            log_debug(
                "[DOM_CONFIRMIT_CF_SINGLE]",
                f"q_id={q_id!r} question={question!r} options={options}",
            )
        except Exception:
            continue

    return blocks


# ================================================================================
# FORSTA/CONFIRMIT — NUMERIC-LIST (cf-question--numeric-list + input[type=number])
# ================================================================================

def _extract_confirmit_cf_numeric_list_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Forsta/Confirmit Wix : question numérique (âge, nombre…) dans cf-question--numeric-list.

    Gate DOM strict (additif) :
    - au moins un div.cf-question--numeric-list présent
    - contient un input[type="number"]

    Structure ciblée :
      div.cf-question--numeric-list#AGE
        div.cf-question__text#AGE_text         ← texte de la question
        div.cf-grid-layout
          div.cf-numeric-list-answer
            input[type="number"]#AGE_1_input   ← cible de saisie
    """
    frame_chain = list(frame_chain or [])
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.cf-question--numeric-list")
    except Exception:
        return blocks
    if not containers:
        return blocks

    for qc in containers[:20]:
        try:
            question = ""
            try:
                q_el = qc.find_element(By.CSS_SELECTOR, "div.cf-question__text")
                question = _norm(q_el.get_attribute("textContent") or q_el.text or "")
            except Exception:
                pass
            if not question:
                continue

            q_id = (qc.get_attribute("id") or "").strip()

            # Detect multi-sum constraint: auto-sum row present ⟹ répartition totale=100
            multi_sum_total: int | None = None
            try:
                if qc.find_elements(By.CSS_SELECTOR, "div.cf-numeric-list-auto-sum"):
                    multi_sum_total = 100
            except Exception:
                pass

            # Build a list of (row_label, input_element) pairs — one per answer row.
            # Fallback to a single entry using inputs[0] when no cf-numeric-list-answer wrappers exist.
            row_pairs: list[tuple[str, object]] = []
            try:
                answer_rows = qc.find_elements(By.CSS_SELECTOR, "div.cf-numeric-list-answer")
            except Exception:
                answer_rows = []

            if answer_rows:
                for row in answer_rows[:50]:
                    try:
                        row_label = ""
                        try:
                            label_el = row.find_element(By.CSS_SELECTOR, "div.cf-numeric-list-answer__text")
                            row_label = _norm(label_el.get_attribute("textContent") or label_el.text or "")
                        except Exception:
                            pass
                        inp = row.find_element(By.CSS_SELECTOR, "input[type='number']")
                        row_pairs.append((row_label, inp))
                    except Exception:
                        continue
            else:
                # Fallback: single input without row wrapper (e.g. age question variant)
                try:
                    raw_inputs = qc.find_elements(By.CSS_SELECTOR, "input[type='number']")
                    if raw_inputs:
                        row_pairs.append(("", raw_inputs[0]))
                except Exception:
                    pass

            if not row_pairs:
                continue

            for row_label, inp in row_pairs:
                try:
                    inp_id = (inp.get_attribute("id") or "").strip()
                    inp_name = (inp.get_attribute("name") or "").strip()
                    if not inp_id and not inp_name:
                        continue

                    block_question = row_label if row_label else question

                    single_key = f"confirmit_cf_numeric:{q_id}:{inp_id}"
                    target_id = make_target_id("single", single_key, block_question)

                    try:
                        xpath = _best_xpath_for_element(driver, inp)
                    except Exception:
                        xpath = f"//*[@id='{inp_id}']" if inp_id else f"//input[@name='{inp_name}']"

                    alt_xpaths: list[str] = []
                    if inp_id:
                        alt_xpaths.append(f"//*[@id='{inp_id}']")
                    if inp_name:
                        alt_xpaths.append(f"//input[@name={_xpath_literal(inp_name)}]")
                    alt_xpaths = [x for x in dict.fromkeys(alt_xpaths) if x and x != xpath][:4]

                    registry_payload: dict = {
                        "kind": "single",
                        "itype": "number",
                        "question": block_question,
                        "xpath": xpath,
                        "alt_xpaths": alt_xpaths,
                        "tag": "input",
                        "name": inp_name,
                        "id": inp_id,
                        "frame_chain": frame_chain,
                        "confirmit_cf_numeric_list": True,
                    }
                    block_ctx: dict = {
                        "kind": "single",
                        "tag": "input",
                        "name": inp_name,
                        "id": inp_id,
                        "confirmit_cf_numeric_list": True,
                    }
                    if multi_sum_total is not None:
                        registry_payload["multi_sum_total"] = multi_sum_total
                        registry_payload["group_id"] = q_id
                        registry_payload["group_question"] = question
                        block_ctx["multi_sum_total"] = multi_sum_total
                        block_ctx["group_id"] = q_id
                        block_ctx["group_question"] = question

                    register_target(target_id, registry_payload)

                    blocks.append(
                        {
                            "question": block_question,
                            "itype": "number",
                            "options": [],
                            "max_select": 1,
                            "min_select": 1,
                            "target_id": target_id,
                            "context": block_ctx,
                        }
                    )
                    log_debug(
                        "[DOM_CONFIRMIT_CF_NUMERIC]",
                        f"q_id={q_id!r} inp_id={inp_id!r} row_label={row_label!r}",
                    )
                except Exception:
                    continue
        except Exception:
            continue

    if blocks:
        log_info("[DOM_CONFIRMIT_CF_NUMERIC]", f"blocks_extracted={len(blocks)}")
    return blocks


# ================================================================================
# FORSTA/CONFIRMIT — OPEN-LIST (cf-question--open-list + input[type=text])
# ================================================================================

def _extract_confirmit_cf_open_list_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Forsta/Confirmit Wix : champ texte libre dans cf-question--open-list (code postal…).

    Gate DOM strict (additif) :
    - au moins un div.cf-question--open-list présent
    - contient un input[type="text"]

    Point piégeux : div.cf-question__text peut être vide (ex. CP).
    Dans ce cas, le libellé se trouve dans le bloc cf-question--info qui précède
    immédiatement dans le DOM. On le récupère via JS previousElementSibling.

    Structure ciblée :
      div.cf-question--info#i9622
        div.cf-question__text#i9622_text  ← libellé si CP_text est vide
      div.cf-question--open-list#CP
        div.cf-question__text#CP_text     ← peut être vide
        div.cf-grid-layout
          div.cf-open-list-answer
            input[type="text"]#CP_1_input ← cible de saisie
    """
    frame_chain = list(frame_chain or [])
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.cf-question--open-list")
    except Exception:
        return blocks
    if not containers:
        return blocks

    for qc in containers[:20]:
        try:
            inputs = qc.find_elements(By.CSS_SELECTOR, "input[type='text']")
            if not inputs:
                continue
            inp = inputs[0]
            inp_id = (inp.get_attribute("id") or "").strip()
            inp_name = (inp.get_attribute("name") or "").strip()
            if not inp_id and not inp_name:
                continue

            # Texte de la question : priorité au cf-question__text interne
            question = ""
            try:
                q_el = qc.find_element(By.CSS_SELECTOR, "div.cf-question__text")
                question = _norm(q_el.get_attribute("textContent") or q_el.text or "")
            except Exception:
                pass

            # Fallback : libellé dans le frère cf-question--info précédent
            if not question:
                try:
                    question = driver.execute_script(
                        """
                        var el = arguments[0];
                        var prev = el.previousElementSibling;
                        while (prev) {
                            if (prev.classList.contains('cf-question--info')) {
                                var t = prev.querySelector('.cf-question__text');
                                if (t) return (t.textContent || t.innerText || '').trim();
                            }
                            prev = prev.previousElementSibling;
                        }
                        return '';
                        """,
                        qc,
                    ) or ""
                    question = _norm(question)
                except Exception:
                    pass

            if not question:
                continue

            q_id = (qc.get_attribute("id") or "").strip()
            single_key = f"confirmit_cf_open_list:{q_id}:{inp_id}"
            target_id = make_target_id("single", single_key, question)

            try:
                xpath = _best_xpath_for_element(driver, inp)
            except Exception:
                xpath = f"//*[@id='{inp_id}']" if inp_id else f"//input[@name='{inp_name}']"

            alt_xpaths: list[str] = []
            if inp_id:
                alt_xpaths.append(f"//*[@id='{inp_id}']")
            if inp_name:
                alt_xpaths.append(f"//input[@name={_xpath_literal(inp_name)}]")
            alt_xpaths = [x for x in dict.fromkeys(alt_xpaths) if x and x != xpath][:4]

            register_target(
                target_id,
                {
                    "kind": "single",
                    "itype": "text",
                    "question": question,
                    "xpath": xpath,
                    "alt_xpaths": alt_xpaths,
                    "tag": "input",
                    "name": inp_name,
                    "id": inp_id,
                    "frame_chain": frame_chain,
                    "confirmit_cf_open_list": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "text",
                    "options": [],
                    "max_select": 1,
                    "min_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "single",
                        "tag": "input",
                        "name": inp_name,
                        "id": inp_id,
                        "confirmit_cf_open_list": True,
                    },
                }
            )
            log_debug(
                "[DOM_CONFIRMIT_CF_OPEN_LIST]",
                f"q_id={q_id!r} inp_id={inp_id!r} question={question!r}",
            )
        except Exception:
            continue

    if blocks:
        log_info("[DOM_CONFIRMIT_CF_OPEN_LIST]", f"blocks_extracted={len(blocks)}")
    return blocks


# ================================================================================
# ASKIA — QUESTION RADIO / NPS myresponse* (td cliquables + input radio masqué)
# ================================================================================

def _extract_askia_myresponse_radio_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction radio pour pages Askia AskiaExt à structure myresponse*.
 
    Gate DOM strict (additif, non provider-wide) :
    - form[name="FormAskia"] présent dans le DOM
    - au moins 2 td[class*="myresponse"] contenant chacune un input[type="radio"]
 
    Structure ciblée :
      <td class="myresponseNC1 askia-response askia-question-222">
        <input type="radio" name="U222" value="4896" style="display:none">
        <span id="cpt222_4896">1</span>
      </td>
 
    Le clic doit cibler le <td> cliquable (handler jQuery bindé dessus),
    pas le <input> masqué. Le texte de l'option est extrait du <span id="cptQ_V">.
 
    La question est extraite depuis td[class*="askia-question-label"]
    ou td[class*="askia-caption"].
 
    Produit exactement 1 bloc radio par groupe name (= 1 question par page
    dans le pattern Askia standard).
    """
    frame_chain = list(frame_chain or [])
 
    # Gate 1 : form Askia présent
    try:
        if not driver.find_elements(By.CSS_SELECTOR, "form[name='FormAskia']"):
            return []
    except Exception:
        return []
 
    # Gate 2 : au moins 2 td myresponse avec input radio
    try:
        sample = driver.find_elements(
            By.CSS_SELECTOR,
            "td[class*='myresponse'] input[type='radio']",
        )
    except Exception:
        return []
 
    if len(sample) < 2:
        return []
 
    # Récupérer tous les td cliquables (myresponse*) portant un input radio
    try:
        response_tds = driver.find_elements(
            By.CSS_SELECTOR,
            "td[class*='myresponse']",
        )
    except Exception:
        return []
 
    # Regrouper par name de l'input radio contenu dans le td
    grouped: dict[str, list] = {}  # name -> list of td elements
    for td in response_tds:
        try:
            radios = td.find_elements(By.CSS_SELECTOR, "input[type='radio'][name]")
            if not radios:
                continue
            name = (radios[0].get_attribute("name") or "").strip()
            if not name:
                continue
            grouped.setdefault(name, []).append(td)
        except Exception:
            continue
 
    if not grouped:
        return []
 
    # Texte de question global : td askia-question-label ou askia-caption
    question = ""
    try:
        q_nodes = driver.find_elements(
            By.CSS_SELECTOR,
            "td.askia-question-label, td[class*='askia-caption'], td[class*='askia-question-label']",
        )
        for qn in q_nodes:
            txt = _norm(qn.text or qn.get_attribute("innerText") or "")
            if txt and len(txt) >= 3:
                question = txt
                break
    except Exception:
        question = ""
 
    blocks: list[dict] = []
 
    for group_name, tds in grouped.items():
        if len(tds) < 2:
            continue
 
        options: list[str] = []
        option_xpath_map: dict[str, str] = {}
 
        for td in tds:
            try:
                # Extraire la valeur et le libellé de l'option
                radios = td.find_elements(By.CSS_SELECTOR, "input[type='radio']")
                if not radios:
                    continue
                radio = radios[0]
                value = (radio.get_attribute("value") or "").strip()
                radio_id = (radio.get_attribute("id") or "").strip()
 
                # Texte de l'option : span[id*="cpt"] en priorité, sinon innerText du td
                label_txt = ""
                try:
                    spans = td.find_elements(By.CSS_SELECTOR, "span[id*='cpt']")
                    if spans:
                        label_txt = _norm(
                            spans[0].text or spans[0].get_attribute("innerText") or ""
                        )
                except Exception:
                    pass
 
                if not label_txt:
                    label_txt = _norm(td.text or td.get_attribute("innerText") or "")
 
                if not label_txt:
                    continue
 
                nk = _norm_key(label_txt)
                if not nk or nk in option_xpath_map:
                    continue
 
                # XPath ciblant le td cliquable (handler jQuery) via radio id ou value+name
                # On préfère l'id du radio comme ancre stable, puis fallback name+value.
                if radio_id:
                    xp = (
                        f"//input[@id={_xpath_literal(radio_id)}]"
                        f"/ancestor::td[contains(concat(' ',normalize-space(@class),' '),"
                        f"' myresponse')][1]"
                    )
                elif value:
                    name_lit = _xpath_literal(group_name)
                    val_lit = _xpath_literal(value)
                    xp = (
                        f"//input[@type='radio' and @name={name_lit} and @value={val_lit}]"
                        f"/ancestor::td[contains(concat(' ',normalize-space(@class),' '),"
                        f"' myresponse')][1]"
                    )
                else:
                    xp = _best_xpath_for_element(driver, td)
 
                if not xp:
                    continue
 
                option_xpath_map[nk] = xp
                options.append(label_txt)
 
            except Exception:
                continue
 
        if len(options) < 2 or not option_xpath_map:
            continue
 
        # Fallback question si non trouvée globalement
        q_text = question or f"Question {group_name}"
 
        group_key = f"radio:name:{group_name}"
        target_id = make_target_id("group", group_key, q_text)
 
        register_target(
            target_id,
            {
                "kind": "group",
                "itype": "radio",
                "group_key": group_key,
                "question": q_text,
                "option_xpath_map": option_xpath_map,
                "frame_chain": frame_chain,
                "askia_myresponse": True,
            },
        )
 
        blocks.append(
            {
                "question": q_text,
                "itype": "radio",
                "options": options,
                "max_select": _compute_max_select("radio", options),
                "target_id": target_id,
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "askia_myresponse": True,
                },
            }
        )
 
        log_debug(
            "[DOM_ASKIA_MYRESPONSE]",
            f"name={group_name!r} question={q_text!r} options={options}",
        )
 
    return blocks


# ================================================================================
# ASKIA — QUESTION CHECKBOX myresponse* (td cliquables + input checkbox masqué)
# ================================================================================

def _extract_askia_myresponse_checkbox_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Extraction checkbox pour pages Askia AskiaExt à structure myresponse*.

    Gate DOM strict (additif, non provider-wide) :
    - form[name="FormAskia"] présent dans le DOM
    - au moins 2 td[class*="myresponse"] contenant chacune un input[type="checkbox"]
      dont le name suit le pattern chk<QID> <optId> (ex: chkM312 5010, chkM312 5013)

    Structure ciblée :
      <td class="myresponse askia-response askia-question-312">
        <input type="hidden" name="M312 5010" ...>
        <input type="checkbox" name="chkM312 5010" class="askia-live">
        <span id="cpt312_5010">En demandant conseil à votre entourage</span>
      </td>

    Groupement : toutes les checkboxes dont le name commence par le même préfixe
    "chk<QID>" (partie avant l'espace) sont regroupées en un seul bloc.

    Le clic doit cibler le <td> cliquable (handler jQuery bindé dessus),
    pas le <input> masqué.

    La question est extraite depuis td[class*="askia-question-label"]
    ou td[class*="askia-caption"].

    Produit exactement 1 bloc checkbox par groupe QID.
    """
    frame_chain = list(frame_chain or [])

    # Gate 1 : form Askia présent
    try:
        if not driver.find_elements(By.CSS_SELECTOR, "form[name='FormAskia']"):
            return []
    except Exception:
        return []

    # Gate 2 : au moins 2 td myresponse avec input checkbox name^="chk"
    try:
        sample = driver.find_elements(
            By.CSS_SELECTOR,
            "td[class*='myresponse'] input[type='checkbox'][name]",
        )
    except Exception:
        return []

    chk_sample = [
        el for el in sample
        if (el.get_attribute("name") or "").lower().startswith("chk")
    ]
    if len(chk_sample) < 2:
        return []

    # Récupérer tous les td myresponse* portant un input checkbox name^="chk"
    try:
        response_tds = driver.find_elements(
            By.CSS_SELECTOR,
            "td[class*='myresponse']",
        )
    except Exception:
        return []

    # Regrouper par préfixe "chk<QID>" (partie du name avant l'espace)
    # Ex: "chkM312 5010" -> préfixe "chkm312", option id "5010"
    grouped: dict[str, list] = {}  # prefix -> list of td elements
    for td in response_tds:
        try:
            boxes = td.find_elements(
                By.CSS_SELECTOR, "input[type='checkbox'][name]"
            )
            if not boxes:
                continue
            raw_name = (boxes[0].get_attribute("name") or "").strip()
            if not raw_name.lower().startswith("chk"):
                continue
            # Préfixe stable = partie avant le premier espace (chk<QID>)
            prefix = raw_name.split(" ")[0].lower()
            if not prefix:
                continue
            grouped.setdefault(prefix, []).append(td)
        except Exception:
            continue

    if not grouped:
        return []

    # Texte de question global : td askia-question-label ou askia-caption
    question = ""
    try:
        q_nodes = driver.find_elements(
            By.CSS_SELECTOR,
            "td.askia-question-label, td[class*='askia-caption'], td[class*='askia-question-label']",
        )
        for qn in q_nodes:
            txt = _norm(qn.text or qn.get_attribute("innerText") or "")
            if txt and len(txt) >= 3:
                question = txt
                break
    except Exception:
        question = ""

    blocks: list[dict] = []

    for prefix, tds in grouped.items():
        if len(tds) < 2:
            continue

        options: list[str] = []
        option_xpath_map: dict[str, str] = {}

        for td in tds:
            try:
                boxes = td.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
                if not boxes:
                    continue
                box = boxes[0]
                box_id = (box.get_attribute("id") or "").strip()
                box_name = (box.get_attribute("name") or "").strip()

                # Texte de l'option : span[id*="cpt"] en priorité, sinon innerText du td
                label_txt = ""
                try:
                    spans = td.find_elements(By.CSS_SELECTOR, "span[id*='cpt']")
                    if spans:
                        label_txt = _norm(
                            spans[0].text or spans[0].get_attribute("innerText") or ""
                        )
                except Exception:
                    pass

                if not label_txt:
                    label_txt = _norm(td.text or td.get_attribute("innerText") or "")

                if not label_txt:
                    continue

                nk = _norm_key(label_txt)
                if not nk or nk in option_xpath_map:
                    continue

                # XPath ciblant le td cliquable via l'id ou le name de la checkbox
                if box_id:
                    xp = (
                        f"//input[@id={_xpath_literal(box_id)}]"
                        f"/ancestor::td[contains(concat(' ',normalize-space(@class),' '),"
                        f"' myresponse')][1]"
                    )
                elif box_name:
                    name_lit = _xpath_literal(box_name)
                    xp = (
                        f"//input[@type='checkbox' and @name={name_lit}]"
                        f"/ancestor::td[contains(concat(' ',normalize-space(@class),' '),"
                        f"' myresponse')][1]"
                    )
                else:
                    xp = _best_xpath_for_element(driver, td)

                if not xp:
                    continue

                option_xpath_map[nk] = xp
                options.append(label_txt)

            except Exception:
                continue

        if len(options) < 2 or not option_xpath_map:
            continue

        q_text = question or f"Question {prefix}"
        group_key = f"checkbox:name:{prefix}"
        target_id = make_target_id("group", group_key, q_text)

        register_target(
            target_id,
            {
                "kind": "group",
                "itype": "checkbox",
                "group_key": group_key,
                "question": q_text,
                "option_xpath_map": option_xpath_map,
                "frame_chain": frame_chain,
                "askia_myresponse_checkbox": True,
            },
        )

        max_sel = _compute_max_select("checkbox", options, q_text)
        blocks.append(
            {
                "question": q_text,
                "itype": "checkbox",
                "options": options,
                "max_select": max_sel,
                "min_select": 1,
                "target_id": target_id,
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "askia_myresponse_checkbox": True,
                },
            }
        )

        log_debug(
            "[DOM_ASKIA_MYRESPONSE_CB]",
            f"prefix={prefix!r} question={q_text!r} options={options}",
        )

    return blocks


# ================================================================================
# ASKIA — RANKING ISOTOPE (div.adc-ranking-isotope + div.statement[data-value])
# ================================================================================

def _extract_askia_ranking_isotope_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait les questions Askia rendues comme widget de classement (ranking).

    Structure DOM cible (moai-surveys.com — adc-ranking-isotope) :
      <form name="FormAskia">
        <div id="adc_N" class="adc-ranking-isotope ...">
          <div class="istope-item statement isotope-item" data-value="5002">
            <span class="rank_text"></span>
            <span class="statement_text">L'expertise des conseillers</span>
          </div>
          ...
          <input type="hidden" name="R310 5002" id="R310_5002" value="">
          ...
        </div>
      </form>

    Le widget JS (adcRanking) lit les inputs hidden R{Q}_{V} pour enregistrer
    le rang sélectionné. Le clic se fait sur le div.statement directement.

    Gate DOM additif strict :
      1. form[name="FormAskia"] présent
      2. Au moins un div[class*="adc-ranking-isotope"] dans le DOM
      3. Au moins 2 div.statement[data-value] dans ce conteneur,
         chacun portant un span.statement_text non vide

    Produit 1 bloc "checkbox" (sélection multiple ordonnée, max=setMax)
    par conteneur adc-ranking-isotope. Le max_select est lu depuis le
    script inline (setMax) ou fallback len(options).

    Le XPath cible le div.statement par data-value pour que l'action
    dispatcher clique dessus directement.
    """
    frame_chain = list(frame_chain or [])

    # Gate 1 : form Askia présent
    try:
        if not driver.find_elements(By.CSS_SELECTOR, "form[name='FormAskia']"):
            return []
    except Exception:
        return []

    # Gate 2 : conteneur adc-ranking-isotope présent
    try:
        containers = driver.find_elements(
            By.CSS_SELECTOR, "div[class*='adc-ranking-isotope']"
        )
    except Exception:
        return []

    if not containers:
        return []

    blocks: list[dict] = []

    for container in containers:
        try:
            container_id = (container.get_attribute("id") or "").strip()

            # Gate 3 : au moins 2 div.statement[data-value] avec span.statement_text
            try:
                statement_divs = container.find_elements(
                    By.CSS_SELECTOR, "div.statement[data-value]"
                )
            except Exception:
                continue

            if len(statement_divs) < 2:
                continue

            # Extraire les options : texte depuis span.statement_text
            options: list[str] = []
            option_xpath_map: dict[str, str] = {}

            for div in statement_divs:
                try:
                    data_value = (div.get_attribute("data-value") or "").strip()
                    if not data_value:
                        continue

                    # Texte depuis span.statement_text en priorité
                    label_txt = ""
                    try:
                        spans = div.find_elements(By.CSS_SELECTOR, "span.statement_text")
                        if spans:
                            label_txt = _norm(
                                spans[0].text or spans[0].get_attribute("innerText") or ""
                            )
                    except Exception:
                        pass

                    if not label_txt:
                        label_txt = _norm(div.text or div.get_attribute("innerText") or "")

                    if not label_txt:
                        continue

                    nk = _norm_key(label_txt)
                    if not nk or nk in option_xpath_map:
                        continue

                    # XPath stable : div.statement dans le conteneur par data-value
                    if container_id:
                        xp = (
                            f"//*[@id={_xpath_literal(container_id)}]"
                            f"//div[contains(concat(' ',normalize-space(@class),' '),' statement ')]"
                            f"[@data-value={_xpath_literal(data_value)}]"
                        )
                    else:
                        xp = (
                            f"//div[contains(concat(' ',normalize-space(@class),' '),'adc-ranking-isotope')]"
                            f"//div[contains(concat(' ',normalize-space(@class),' '),' statement ')]"
                            f"[@data-value={_xpath_literal(data_value)}]"
                        )

                    option_xpath_map[nk] = xp
                    options.append(label_txt)

                except Exception:
                    continue

            if len(options) < 2 or not option_xpath_map:
                continue

            # Question depuis td.askia-question-label / td[class*='askia-caption']
            question = ""
            try:
                q_nodes = driver.find_elements(
                    By.CSS_SELECTOR,
                    "td.askia-question-label, td[class*='askia-caption'], "
                    "td[class*='askia-question-label']",
                )
                for qn in q_nodes:
                    # Exclure le contenu du span#indic (instruction de cardinalité)
                    txt = _norm(driver.execute_script(
                        """
                        const td = arguments[0];
                        if (!td) return '';
                        const clone = td.cloneNode(true);
                        const indic = clone.querySelector('#indic, span[id="indic"]');
                        if (indic) indic.remove();
                        return (clone.innerText || clone.textContent || '').replace(/\\s+/g, ' ').trim();
                        """,
                        qn,
                    ) or "")
                    if txt and len(txt) >= 3:
                        question = txt
                        break
            except Exception:
                question = ""

            if not question:
                question = f"Classement {container_id}" if container_id else "Classement"

            # max_select : lire setMax dans le script inline, fallback len(options)
            max_select = len(options)
            try:
                set_max_raw = driver.execute_script(
                    """
                    const cid = arguments[0];
                    if (!cid) return null;
                    // Chercher setMax dans le texte des scripts inline
                    const scripts = document.querySelectorAll('script');
                    const re = /setMax\\s*:\\s*parseInt\\(['"]?(\\d+)['"]?\\)/;
                    for (const s of scripts) {
                        const m = (s.textContent || '').match(re);
                        if (m) return parseInt(m[1], 10);
                    }
                    return null;
                    """,
                    container_id,
                )
                if set_max_raw and isinstance(set_max_raw, (int, float)) and int(set_max_raw) >= 1:
                    max_select = int(set_max_raw)
            except Exception:
                pass

            group_key = (
                f"askia_ranking:{container_id}"
                if container_id
                else f"askia_ranking:opts:{_norm_key('|'.join(options[:4]))}"
            )
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
                    "askia_ranking_isotope": True,
                    "max_select": max_select,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "checkbox",
                    "options": options,
                    "max_select": max_select,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "askia_ranking_isotope": True,
                    },
                }
            )

            log_debug(
                "[DOM_ASKIA_RANKING]",
                f"container={container_id!r} question={question[:60]!r} "
                f"options={len(options)} max_select={max_select}",
            )

        except Exception:
            continue

    return blocks


# ================================================================================
# ASKIA ADC-SLIDER (noUiSlider)
# ================================================================================

def _extract_askia_adc_slider_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait les sliders Askia (div.adc-slider / noUiSlider).

    Structure DOM cible :
      <div id="adc_N" class="adc-slider">
        <input type="hidden" id="UXXX" name="UXXX" value="">
        <div class="sliderContainer">
          <table class="slider">
            <tr class="sliderTop">
              <td><div class="leftLabel">...</div></td>
              <td><div class="rightLabel">...</div></td>
            </tr>
            <tr class="sliderMiddle">
              <td><div class="noUiSlider ..."><div class="noUi-base">
                <div class="noUi-origin"><div class="noUi-handle noUi-handle-lower"></div></div>
              </div></div></td>
            </tr>
            <tr class="sliderDK">          <!-- optionnel -->
              <td><div class="dk" data-value="NNNN">Vous ne savez pas</div></td>
            </tr>
          </table>
        </div>
      </div>

    La sub-question est dans le td.askia-caption[QID] ou td.askia-question-label
    précédant le td.askia-control[class*="askia-question-QUID"] dans le même tableau.

    Les valeurs numériques (min/max/step) sont lues depuis les attributs du conteneur
    adc-slider ou, en fallback, depuis la position du handle (left: 50% → valeur médiane).
    L'interaction se fait via JS sur l'input hidden + dispatch d'un event 'change'.

    Gate DOM stricte :
      - form[name="FormAskia"] présent
      - ET au moins un div.adc-slider contenant un input[type="hidden"][name] ET un div.noUi-handle
    """
    blocks: list[dict] = []

    # Gate 1 : form Askia
    try:
        if not driver.find_elements(By.CSS_SELECTOR, "form[name='FormAskia']"):
            return blocks
    except Exception:
        return blocks

    # Gate 2 : au moins un adc-slider avec handle
    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.adc-slider")
    except Exception:
        return blocks

    if not containers:
        return blocks

    seen_names: set[str] = set()

    for container in containers:
        try:
            # input hidden portant la valeur de réponse
            hidden_inputs = container.find_elements(
                By.CSS_SELECTOR, "input[type='hidden'][name]"
            )
            if not hidden_inputs:
                continue
            hidden_input = hidden_inputs[0]
            input_name = (hidden_input.get_attribute("name") or "").strip()
            input_id = (hidden_input.get_attribute("id") or "").strip()
            if not input_name or input_name in seen_names:
                continue

            # Gate : handle noUiSlider présent (confirme que c'est bien un slider actif)
            if not container.find_elements(By.CSS_SELECTOR, "div.noUi-handle"):
                continue

            container_id = (container.get_attribute("id") or "").strip()

            # ── Sub-question : td précédant le td.askia-control dans la même table ──
            sub_question = ""
            try:
                # Cherche le td.askia-control qui contient ce container
                control_td = driver.execute_script(
                    "return arguments[0].closest('td[class*=\"askia-control\"]');",
                    container,
                )
                if control_td:
                    # Remonte au tr, puis cherche le tr précédent avec td.askia-question-label
                    sub_question = driver.execute_script(
                        """
                        var td = arguments[0];
                        var tr = td.closest('tr');
                        if (!tr) return '';
                        var prev = tr.previousElementSibling;
                        while (prev) {
                            var label = prev.querySelector(
                                'td.askia-question-label, td[class*="askia-caption"]'
                            );
                            if (label) {
                                return (label.innerText || label.textContent || '').trim();
                            }
                            prev = prev.previousElementSibling;
                        }
                        return '';
                        """,
                        control_td,
                    ) or ""
                    sub_question = _norm(sub_question)
            except Exception:
                sub_question = ""

            # ── Question globale (instruction commune en tête de page) ──
            global_question = ""
            try:
                q_nodes = driver.find_elements(
                    By.CSS_SELECTOR,
                    "td.askia-question-label, td[class*='askia-caption']",
                )
                for qn in q_nodes:
                    raw = _norm(qn.text or qn.get_attribute("innerText") or "")
                    if raw and raw != sub_question:
                        global_question = raw
                        break
            except Exception:
                pass

            if sub_question and global_question:
                question = f"{global_question} | {sub_question}"
            else:
                question = sub_question or global_question
            if not question:
                continue

            # ── Options : leftLabel + rightLabel + DK ──
            left_label = ""
            right_label = ""
            try:
                ll_els = container.find_elements(By.CSS_SELECTOR, "div.leftLabel")
                if ll_els:
                    left_label = _norm(ll_els[0].text or ll_els[0].get_attribute("innerText") or "")
            except Exception:
                pass
            try:
                rl_els = container.find_elements(By.CSS_SELECTOR, "div.rightLabel")
                if rl_els:
                    right_label = _norm(rl_els[0].text or rl_els[0].get_attribute("innerText") or "")
            except Exception:
                pass

            # DK button : div.dk[data-value]
            dk_text = ""
            dk_data_value = ""
            try:
                dk_els = container.find_elements(By.CSS_SELECTOR, "div.dk[data-value]")
                if dk_els:
                    dk_text = _norm(dk_els[0].text or dk_els[0].get_attribute("innerText") or "")
                    dk_data_value = (dk_els[0].get_attribute("data-value") or "").strip()
            except Exception:
                pass

            # ── Construction des options et de l'option_xpath_map ──
            # Le noUiSlider Askia va de 0 à 10 (11 positions).
            # On expose chaque position numérique comme option explicite, avec libellé de pôle
            # aux extrêmes, pour que le bot puisse viser n'importe quelle valeur de déplacement.
            # Pour l'interaction, on utilise JS sur l'input hidden + trigger change.
            options: list[str] = []
            option_xpath_map: dict[str, str] = {}
            value_map: dict[str, str] = {}   # option_key → valeur à injecter dans l'input hidden

            name_lit = _xpath_literal(input_name)

            # ── Positions 0–10 : libellés lisibles avec contexte aux pôles ──
            # Format : "0% (100% seul(e) en autonomie)", "50%", "100% (100% accompagné...)"
            for pct in range(0, 101, 10):
                position = pct // 10  # 0..10
                if pct == 0 and left_label:
                    label = f"{pct}% ({left_label})"
                elif pct == 100 and right_label:
                    label = f"{pct}% ({right_label})"
                else:
                    label = f"{pct}%"
                key = _norm_key(label)
                options.append(label)
                option_xpath_map[key] = f"//input[@name={name_lit}]"
                value_map[key] = str(position)   # position 0..10 utilisée par action_dispatcher

            if dk_text and dk_data_value:
                key = _norm_key(dk_text)
                options.append(dk_text)
                # Le DK est un div cliquable — XPath direct et stable
                if container_id:
                    cid_lit = _xpath_literal(container_id)
                    dv_lit = _xpath_literal(dk_data_value)
                    option_xpath_map[key] = (
                        f"//*[@id={cid_lit}]//div[contains(@class,'dk')][@data-value={dv_lit}]"
                    )
                else:
                    dv_lit = _xpath_literal(dk_data_value)
                    option_xpath_map[key] = (
                        f"//div[contains(@class,'adc-slider')]"
                        f"//div[contains(@class,'dk')][@data-value={dv_lit}]"
                    )
                value_map[key] = dk_data_value

            if len(options) < 1:
                continue

            group_key = (
                f"askia_adc_slider:{container_id}:{input_name}"
                if container_id
                else f"askia_adc_slider:{input_name}"
            )
            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": list(frame_chain or []),
                    "askia_adc_slider": True,
                    "input_name": input_name,
                    "input_id": input_id,
                    "container_id": container_id,
                    "value_map": value_map,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "min_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "askia_adc_slider": True,
                        "input_name": input_name,
                    },
                }
            )

            seen_names.add(input_name)

            log_debug(
                "[DOM_ASKIA_ADC_SLIDER]",
                f"container={container_id!r} input_name={input_name!r} "
                f"question={question[:60]!r} options={len(options)}",
            )

        except Exception:
            continue

    if blocks:
        log_info("[DOM_ASKIA_ADC_SLIDER]", f"blocks_extracted={len(blocks)}")

    return blocks


# ================================================================================
# CONFIRMIT / FORSTA WIX — QUESTION RANKING (cf-question--ranking)
# ================================================================================

def _extract_confirmit_cf_ranking_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """Forsta/Confirmit Wix : question de classement par clic séquentiel.

    Gate DOM strict (additif) :
    - au moins un div.cf-question--ranking présent
    - contient des div.cf-list__item.cf-ranking-answer[role="button"]

    Mécanique d'interaction :
    Chaque option est un div[role="button"] cliquable. Un clic attribue le rang
    suivant disponible (cf-ranking-answer__rank passe de "-" à "1", "2", …).
    L'ordre des clics détermine le classement — il n'y a pas d'input natif à cocher.
    Le dispatcher doit cliquer les options dans l'ordre des valeurs reçues d'OpenAI
    (valeur "1" = cliquer en premier, "2" = en deuxième, etc.).

    Contrainte max : portée par multiCount.max dans le JSON Confirmit inline.
    On la lit depuis div.cf-question__instruction (mention "cinq", "5", etc.) ou
    on la fixe à 5 par défaut si la lecture échoue — comportement safe.

    Structure ciblée :
      div.cf-question.cf-question--ranking#Q11
        div.cf-question__text           ← texte de la question
        div.cf-question__instruction    ← instruction de classement (incluse dans le contexte)
        div.cf-list
          div.cf-list__item.cf-ranking-answer[role="button"]#Q11_11
            div.cf-ranking-answer__rank  ← "-" si non sélectionné, entier sinon
            div.cf-ranking-answer__content
              div.cf-ranking-answer__text  ← texte de l'option
          ... (N items, dont éventuellement un avec cf-ranking-answer__other-input)

    Signal de sélection : cf-ranking-answer--selected + aria-pressed="true" sur la div.
    Signal de quota atteint : les items non sélectionnés reçoivent cf-ranking-answer--disabled.

    Un seul bloc est produit par question (itype="checkbox", max_select=max_rank).
    Les options excluent les items contenant uniquement un champ texte libre "Autres".
    """
    frame_chain = list(frame_chain or [])
    blocks: list[dict] = []

    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "div.cf-question--ranking")
    except Exception:
        return blocks
    if not containers:
        return blocks

    for qc in containers[:10]:
        try:
            # --- Gate : présence d'au moins 2 items ranking cliquables ---
            items = qc.find_elements(
                By.CSS_SELECTOR,
                "div.cf-list__item.cf-ranking-answer[role='button']",
            )
            if len(items) < 2:
                continue

            # --- Texte de la question ---
            question = ""
            try:
                q_el = qc.find_element(By.CSS_SELECTOR, "div.cf-question__text")
                question = _norm(q_el.get_attribute("textContent") or q_el.text or "")
            except Exception:
                pass
            if not question:
                continue

            # --- Instruction de classement ---
            instruction = ""
            try:
                ins_el = qc.find_element(By.CSS_SELECTOR, "div.cf-question__instruction")
                instruction = _norm(ins_el.get_attribute("textContent") or ins_el.text or "")
            except Exception:
                pass

            # Fusionner l'instruction dans le texte de question exposé à OpenAI.
            # Sans cette fusion, OpenAI ne voit pas la contrainte de classement.
            question_for_openai = f"{question} {instruction}".strip() if instruction else question

            # --- Extraction des options (texte des items, sauf champ "Autres" pur) ---
            options: list[str] = []
            item_ids: list[str] = []
            item_xpaths: list[str] = []

            for item in items:
                try:
                    item_id = (item.get_attribute("id") or "").strip()

                    # Exclure les items qui ne contiennent qu'un input texte libre (Autres)
                    has_text_div = bool(item.find_elements(
                        By.CSS_SELECTOR, "div.cf-ranking-answer__text"
                    ))
                    if not has_text_div:
                        # Item "Autres" sans texte structuré → on l'ignore
                        continue

                    txt_el = item.find_element(By.CSS_SELECTOR, "div.cf-ranking-answer__text")
                    txt = _norm(txt_el.get_attribute("textContent") or txt_el.text or "")
                    if not txt:
                        continue

                    try:
                        xpath = _best_xpath_for_element(driver, item)
                    except Exception:
                        xpath = f"//*[@id='{item_id}']" if item_id else ""
                    if not xpath:
                        continue

                    options.append(txt)
                    item_ids.append(item_id)
                    item_xpaths.append(xpath)
                except Exception:
                    continue

            if len(options) < 2:
                continue

            # max_select = nombre total d'options disponibles.
            # Le "cinq" de l'instruction est une contrainte métier transmise via question_for_openai,
            # pas une limite DOM — OpenAI choisira lui-même combien d'options classer.
            max_select = len(options)

            q_id = (qc.get_attribute("id") or "").strip()
            group_key = f"confirmit_cf_ranking:{q_id}:{question[:60]}"
            target_id = make_target_id("group", group_key, question)

            # Registry : on stocke la carte texte→xpath pour le dispatcher
            option_xpath_map: dict[str, str] = {
                _norm_lc(txt): xpath
                for txt, xpath in zip(options, item_xpaths)
            }

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "checkbox",
                    "question": question,
                    "instruction": instruction,
                    "options": options,
                    "item_ids": item_ids,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                    "q_id": q_id,
                    "confirmit_cf_ranking": True,
                },
            )

            blocks.append(
                {
                    # question_for_openai intègre l'instruction de classement pour qu'OpenAI
                    # comprenne la contrainte (classer de 1 à N, 1 = plus important).
                    "question": question_for_openai,
                    "itype": "checkbox",
                    "options": options,
                    "max_select": max_select,
                    "min_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "group",
                        "group_key": group_key,
                        "instruction": instruction,
                        "confirmit_cf_ranking": True,
                    },
                }
            )

            log_debug(
                "[DOM_CONFIRMIT_CF_RANKING]",
                f"q_id={q_id!r} options={len(options)} max_select={max_select} "
                f"question={question[:60]!r}",
            )

        except Exception:
            continue

    if blocks:
        log_info("[DOM_CONFIRMIT_CF_RANKING]", f"blocks_extracted={len(blocks)}")
    return blocks