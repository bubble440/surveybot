# Survey/dom_analyzer.py
"""
DOM Analyzer - Orchestration principale de l'analyse DOM

Ce module est le point d'entrée principal pour l'analyse DOM des surveys.
Il orchestre tous les extracteurs spécifiques et construit une représentation
unifiée des questions/inputs disponibles.

Fonctions principales:
- analyze_dom(driver): Point d'entrée, sélectionne meilleure frame et analyse
- _analyze_dom_current_context(driver, frame_chain): Analyse le contexte DOM actuel

Architecture:
1. Sélection du meilleur contexte DOM (frame)
2. Extraction par extracteurs platform-spécifiques
3. Fallback sur extracteurs génériques
4. Construction du registre unifié
"""

from __future__ import annotations
from typing import List, Dict, Any, Tuple
import os, re

from selenium.webdriver.common.by import By

# Imports des modules DOM
try:
    # Utilitaires de base
    from Survey.dom_utils import (
        _norm, _norm_lc, _norm_key,
        _looks_like_system_field, _is_actionable_visible,
        _best_xpath_for_element, _xpath_literal,
        _is_question_text, _is_validation_instruction,
        _detect_itype, _dropdown_field_hint, _env_truthy
    )
    
    # Extraction de questions
    from Survey.dom_question_extractor import (
        _find_question_text_near_element, _find_associated_label,
        _extract_ssi_confirmit_question, _extract_surveywriter_ssi_question,
        _nearest_question_container, _extract_question_from_container,
        _group_key_for_choice, _compute_max_select
    )
    
    # Gestion des frames
    from Survey.dom_frame_selector import (
        _wait_for_survey_dom, _score_dom_context, _select_best_frame_chain
    )
    
    # Extracteurs platform-spécifiques
    from Survey.dom_extractors_decipher import (
        _extract_focusvision_answers_list_groups,
        _extract_focusvision_cardsort_block,
        _extract_decipher_answers_list_fallback
    )
    
    from Survey.dom_extractors_areyounet import (
        _extract_areyounet_matrix_blocks,
        _extract_areyounet_switch_radio_blocks,
        _extract_areyounet_switch_checkbox_blocks
    )
    
    from Survey.dom_extractors_misc import (
        _extract_angular_material_radio_groups,
        _extract_walr_cardsort_block,
        _extract_askandanswer_mobile_matrix_rows,
        _extract_askandanswer_selection_list_questions,
        _extract_rnw_ionicon_multi_choice_blocks,
        _extract_table_matrix_radio_rows,
        _extract_cmix_simple_grid_question_blocks,
        _extract_cmix_radio_question_blocks,
        _extract_ipsos_slider_question_blocks,
        _extract_cloudresearch_sentry_blocks,
        _extract_purespectrum_mobile_date_blocks,
    )
    
    # Registre et utilitaires
    from Survey.dom_registry import clear_registry, register_target, make_target_id
    from Survey.frame_utils import switch_to_frame_chain
    from Survey.sliderpoints_extractor import extract_sliderpoints_question_blocks
    
except ImportError:
    # Fallback pour tests locaux (ne devrait pas arriver en production)
    from Survey.dom_utils import (
        _norm, _norm_lc, _norm_key,
        _looks_like_system_field, _is_actionable_visible,
        _best_xpath_for_element, _xpath_literal,
        _is_question_text, _is_validation_instruction,
        _detect_itype, _dropdown_field_hint, _env_truthy
    )
    from Survey.dom_question_extractor import (
        _find_question_text_near_element, _find_associated_label,
        _extract_ssi_confirmit_question, _extract_surveywriter_ssi_question,
        _nearest_question_container, _extract_question_from_container,
        _group_key_for_choice, _compute_max_select
    )
    from Survey.dom_frame_selector import (
        _wait_for_survey_dom, _score_dom_context, _select_best_frame_chain
    )
    from Survey.dom_extractors_decipher import (
        _extract_focusvision_answers_list_groups,
        _extract_focusvision_cardsort_block,
        _extract_decipher_answers_list_fallback
    )
    from Survey.dom_extractors_areyounet import (
        _extract_areyounet_matrix_blocks,
        _extract_areyounet_switch_radio_blocks,
        _extract_areyounet_switch_checkbox_blocks
    )
    from Survey.dom_extractors_misc import (
        _extract_angular_material_radio_groups,
        _extract_walr_cardsort_block,
        _extract_askandanswer_mobile_matrix_rows,
        _extract_askandanswer_selection_list_questions,
        _extract_rnw_ionicon_multi_choice_blocks,
        _extract_table_matrix_radio_rows,
        _extract_cmix_simple_grid_question_blocks,
        _extract_cmix_radio_question_blocks,
        _extract_ipsos_slider_question_blocks,
        _extract_cloudresearch_sentry_blocks,
        _extract_purespectrum_mobile_date_blocks,
    )


# ================================================================================
# FONCTION PRINCIPALE - ANALYSE CONTEXTE DOM ACTUEL
# ================================================================================

def _analyze_dom_current_context(driver, frame_chain=None) -> List[Dict[str, Any]]:
    """
    Analyse le DOM courant et retourne une liste de QuestionBlock.
    IMPORTANT: 1 bloc par question (group radio/checkbox).
    """
    
    frame_chain = frame_chain or []
    question_blocks: List[Dict[str, Any]] = []
    clear_registry()

    # --- 0) FocusVision cardsort (UI visible) ---
    # Pattern spécifique
    try:
        cs_block = _extract_focusvision_cardsort_block(driver, frame_chain)
        if cs_block:
            return [cs_block]
    except Exception:
        pass


    # --- 0a-bis) Walr Image Evaluation (rsScrollGridWrappper + rsBtn) ---
    # DISABLED: Image evaluation requires Vision API - handled by survey_difficulty_guard
    # These surveys are abandoned with reason="image_evaluation" before reaching this code.
    # try:
    #     walr_img_block = _extract_walr_image_eval_block(driver, frame_chain)
    #     ...
    # except Exception as e:
    #     ...

    # --- 0a) Walr CardSort (button.answer-button) ---
    # Pattern: #cardSortContainer > .statement-box + button.answer-button
    # Pas de radios natifs, on clique directement sur les boutons.
    try:
        walr_cs_block = _extract_walr_cardsort_block(driver, frame_chain)
        print(f"[WALR_CS] bloc retourné: {walr_cs_block is not None}")
        if walr_cs_block:
            print(f"[WALR_CS] SUCCESS - returning block with {len(walr_cs_block.get('options', []))} options")
            return [walr_cs_block]
    except Exception as e:
        print(f"[WALR_CS] EXCEPTION dans appel: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    # --- 0b) Ask&Answer / FirstInsight : matrice mobile (expansion panels) ---
    # Pattern spécifique
    try:
        aa_blocks = _extract_askandanswer_mobile_matrix_rows(driver, frame_chain)
        if aa_blocks:
            return aa_blocks
    except Exception:
        pass

    # --- 0c) Ask&Answer / FirstInsight : listes multi (mat-selection-list) ---
    # Pattern spécifique
    try:
        aa_sl_blocks = _extract_askandanswer_selection_list_questions(driver, frame_chain)
        if aa_sl_blocks:
            return aa_sl_blocks
    except Exception:
        pass

    # --- 0c-bis) React-Native-Web: listes multi avec wrappers tabindex + icône ionicons ---
    # Objectif: extraire les checkboxes custom sans <input> natif.
    try:
        rnw_multi_blocks = _extract_rnw_ionicon_multi_choice_blocks(driver, frame_chain)
        if rnw_multi_blocks:
            return rnw_multi_blocks
    except Exception:
        pass

    # --- 0d-1) CMIX SIMPLE_GRID : matrices table.cm-simple-grid__table ---
    # Objectif: extraire les grilles CMIX où chaque ligne = 1 question radio.
    # DOIT être appelé AVANT l'extracteur CMIX générique (qui échoue sur ce markup).
    try:
        cmix_sg_blocks = _extract_cmix_simple_grid_question_blocks(driver, frame_chain)
        if cmix_sg_blocks:
            return cmix_sg_blocks
    except Exception:
        pass

    # --- 0d-1bis) Matrices HTML génériques (table + radios groupés par ligne) ---
    # Objectif: éviter l'aplatissement en bloc checkbox sur certaines grilles provider-variants.
    try:
        table_matrix_blocks = _extract_table_matrix_radio_rows(driver, frame_chain)
        if table_matrix_blocks:
            return table_matrix_blocks
    except Exception:
        pass

    # --- 0d-2) CMIX (survey.cmix.com) : radios rendus via .cm-question-wrapper ---
    # Objectif: éviter le fallback CTA-only quand les radios sont visibles mais non extraites.
    try:
        cmix_blocks = _extract_cmix_radio_question_blocks(driver, frame_chain)
        if cmix_blocks:
            return cmix_blocks
    except Exception:
        pass

    # --- 0d-3) IPSOS sliders (bootstrap-slider 1..N) ---
    # Objectif: extraire les questions IPSOS sans radios natives (input hidden bs-slider).
    try:
        ipsos_slider_blocks = _extract_ipsos_slider_question_blocks(driver, frame_chain)
        if ipsos_slider_blocks:
            return ipsos_slider_blocks
    except Exception:
        pass

    # Pattern spécifique
    # Objectif: extraire les matrices (1 ligne = 1 question radio).
    try:
        ayn_matrix_blocks = _extract_areyounet_matrix_blocks(driver, frame_chain)
        if ayn_matrix_blocks:
            return ayn_matrix_blocks
    except Exception:
        pass

    # --- 0f) AreYouNet SIMPLE (areyounet.com / runet) : radios via onclick switch_radio() ---
    # Pattern spécifique
    try:
        ayn_blocks = _extract_areyounet_switch_radio_blocks(driver, frame_chain)
        if ayn_blocks:
            return ayn_blocks
    except Exception:
        pass

    # --- 0g) AreYouNet CHECKBOX (areyounet.com / runet) : checkboxes via onclick switch_checkbox() ---
    # Pattern spécifique
    try:
        ayn_chk_blocks = _extract_areyounet_switch_checkbox_blocks(driver, frame_chain)
        if ayn_chk_blocks:
            return ayn_chk_blocks
    except Exception:
        pass

    # --- 0h) CloudResearch/Sentry : divs role="button" comme boutons radio ---
    # Objectif: extraire les questions CloudResearch/Sentry qui utilisent Vue.js
    # avec des divs cliquables au lieu d'inputs radio traditionnels.
    try:
        cr_blocks = _extract_cloudresearch_sentry_blocks(driver, frame_chain)
        if cr_blocks:
            return cr_blocks
    except Exception:
        pass

    # --- 0i) PureSpectrum mobile date picker (ps-select-scroll) ---
    # Objectif: extraire les blocs date quand aucun input/select natif n'est présent.
    try:
        ps_date_blocks = _extract_purespectrum_mobile_date_blocks(driver, frame_chain)
        if ps_date_blocks:
            return ps_date_blocks
    except Exception:
        pass

    # Pattern spécifique
    try:
        choice_els = driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)"
        )
    except Exception:
        choice_els = []

    # Pattern spécifique
    # Pattern spécifique
    # Pattern spécifique
    try:
        has_real_inputs = any((e.tag_name or "").lower() == "input" for e in choice_els)
        if has_real_inputs:
            filtered = []
            for e in choice_els:
                try:
                    tag = (e.tag_name or "").lower()
                    if tag in {"svg", "path", "polygon", "rect", "circle", "g", "title"}:
                        continue
                    filtered.append(e)
                except Exception:
                    continue
            choice_els = filtered
    except Exception:
        pass

    groups: Dict[tuple[str, str], List[Any]] = {}

    def _choice_has_visible_proxy(el) -> bool:
        """
        Certains frameworks masquent l'input radio/checkbox natif et n'affichent
        que le label (ou un wrapper). Dans ce cas `is_displayed()` sur l'input
        retourne False alors que l'option est bien visible et cliquable.
        """
        try:
            if _is_actionable_visible(el):
                return True
        except Exception:
            pass

        try:
            return bool(driver.execute_script(
                """
                const el = arguments[0];
                if (!el) return false;

                const isVisible = (node) => {
                  if (!node || !(node instanceof Element)) return false;
                  const st = window.getComputedStyle(node);
                  if (!st) return false;
                  if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
                  const r = node.getBoundingClientRect();
                  return r.width > 0 && r.height > 0;
                };

                if (isVisible(el)) return true;

                // 1) Label englobant
                const parentLabel = el.closest('label');
                if (isVisible(parentLabel)) return true;

                // 2) Label lié via for=id
                const id = el.id;
                if (id) {
                  const cssEscape = (window.CSS && CSS.escape) ? CSS.escape(id) : id.replace(/([ #;?%&,.+*~\\':\"!^$\[\]()=>|\\/@])/g, '\\\\$1');
                  const linked = document.querySelector(`label[for="${cssEscape}"]`);
                  if (isVisible(linked)) return true;
                }

                // 3) Wrapper option visible (cas UI custom)
                const optionWrapper = el.closest('[role="radio"], [role="checkbox"], .form-check, .option, li, .choice');
                if (isVisible(optionWrapper)) return true;

                return false;
                """,
                el,
            ))
        except Exception:
            return False

    for el in choice_els:
        try:
            itype = _detect_itype(el)
            if itype not in ("radio", "checkbox"):
                continue
            # Masqué
            try:
                if _looks_like_system_field(el):
                    continue
            except Exception:
                pass
            if not _choice_has_visible_proxy(el):
                continue
            raw_name_key = _group_key_for_choice(el, itype)
            if not raw_name_key:
                continue
            group_key = f"{itype}:name:{raw_name_key}"
            groups.setdefault((itype, group_key), []).append(el)
        except Exception:
            continue

    seen_signatures = set()
    seen_multi_text_groups = set()

    for (itype, group_key), els in groups.items():
        try:
            # options = labels des inputs
            options: List[str] = []
            for e in els:
                lbl = _find_associated_label(driver, e)
                if lbl:
                    options.append(lbl)
            # Pattern spécifique
            options = list(dict.fromkeys([o for o in options if o]))

            # Fallback Decipher/FocusVision: certains widgets custom (ex: tiled checkbox)
            # exposent des inputs exploitables mais sans label associé directement.
            # Dans ce cas, on récupère les libellés visibles depuis le conteneur réponse.
            if not options:
                try:
                    container = _nearest_question_container(els[0])
                    if container:
                        label_nodes = container.find_elements(
                            By.CSS_SELECTOR,
                            ".answers.answers-table .row-legend, "
                            ".answers.answers-table label[for], "
                            ".sq-atm1d-legend"
                        )
                        fallback_options: List[str] = []
                        for node in label_nodes:
                            txt = _norm((node.text or "").strip())
                            if txt and txt not in fallback_options:
                                fallback_options.append(txt)
                        if len(fallback_options) >= 2:
                            options = fallback_options
                except Exception:
                    pass

            # question = depuis conteneur (et on exclut options)
            # Tentative prioritaire: pattern SurveyWriter/SSI (#QText_{N})
            question = _extract_surveywriter_ssi_question(driver, els[0])
            if not question:
                # Fallback: extraction générique via conteneur
                container = _nearest_question_container(els[0])
                question = _extract_question_from_container(container, options) if container else ""

            # Pattern spécifique
            if not question:
                # Pattern spécifique
                try:
                    if not question:
                        el_label = driver.find_elements(By.CSS_SELECTOR, "#label")
                        if el_label:
                            t = _norm(el_label[0].text)
                            if t:
                                question = t
                except Exception:
                    pass

                near = _norm(_find_question_text_near_element(driver, els[0]))
                if near:
                    near_lc = _norm_lc(near)
                    opt_lc = {_norm_lc(o) for o in (options or []) if o}
                    # Pattern spécifique
                    # IMPORTANT: ne pas rejeter une vraie question longue qui contient juste
                    # Pattern spécifique
                    is_meta = bool(re.match(r"^question\s*\d+", near_lc))
                    if not is_meta:
                        # Pattern spécifique
                        if (len(near_lc) < 140) and ("veuillez" in near_lc) and (("sÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©lection" in near_lc) or ("selection" in near_lc)):
                            is_meta = True
                    if (near_lc not in opt_lc) and (not is_meta):
                        question = near

            if not question:
                # dernier recours: bloc "1 option" (rare, mais utile)
                if len(options) == 1 and len(els) == 1:
                    question = options[0]
                else:
                    continue

            # Pattern spécifique
            if not options and len(els) == 1 and question:
                options = [question]

            # Pattern spécifique
            # Pattern spécifique
            sig = group_key if group_key.startswith(f"{itype}:name:") else (question, itype)
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)

            # --- target_id + registry pour group (radio/checkbox)
            target_id = make_target_id("group", group_key, question)

            # map option -> xpath de l'input correspondant
            option_xpath_map = {}
            for e in els:
                try:
                    lbl = _find_associated_label(driver, e)

                    # Pattern spécifique
                    if not lbl and len(els) == 1 and question:
                        lbl = question

                    if not lbl:
                        continue

                    # Pattern spécifique
                    inp_id = ""
                    inp_type = ""
                    inp_name = ""
                    inp_value = ""
                    try:
                        inp_id = (e.get_attribute("id") or "").strip()
                        inp_type = (e.get_attribute("type") or "").strip().lower()
                        inp_name = (e.get_attribute("name") or "").strip()
                        inp_value = (e.get_attribute("value") or "").strip()
                    except Exception:
                        pass

                    xp = ""

                    # 1) Le plus stable : label[for="<id>"] (ou fallback input#id)
                    # Masqué
                    # et le click doit viser le <label for="...">.
                    if inp_id:
                        id_lit = _xpath_literal(inp_id)

                        # Pattern spécifique
                        # Pattern spécifique
                        in_grid = False
                        try:
                            in_grid = bool(e.find_elements(By.XPATH, "ancestor::table[contains(@class,'grid')][1]"))
                        except Exception:
                            in_grid = False

                        if in_grid:
                            xp = (
                                f"(//*[@id={id_lit}]/ancestor::td[contains(@class,'clickableCell')][1] | "
                                f"//*[@id={id_lit}]/ancestor::td[1] | "
                                f"//label[@for={id_lit}]//*[normalize-space(.)!=''] | "
                                f"//label[@for={id_lit}] | "
                                f"//*[@id={id_lit}])"
                            )
                        else:
                            try:
                                has_label = bool(driver.find_elements(By.XPATH, f"//label[@for={id_lit}]"))
                            except Exception:
                                has_label = False

                            if has_label:
                                xp = f"(//label[@for={id_lit}]//*[normalize-space(.)!=''] | //label[@for={id_lit}] | //*[@id={id_lit}])"
                            else:
                                xp = f"//*[@id={id_lit}]"

                    # 2) Fallback stable : input par (type,name,value) si pas d'id
                    elif inp_type in ("radio", "checkbox") and inp_name and inp_value:
                        t_lit = _xpath_literal(inp_type)
                        n_lit = _xpath_literal(inp_name)
                        v_lit = _xpath_literal(inp_value)
                        xp = f"(//input[@type={t_lit} and @name={n_lit} and @value={v_lit}]/ancestor::label[1] | //input[@type={t_lit} and @name={n_lit} and @value={v_lit}])[1]"

                    # 3) Dernier recours : XPath absolu
                    else:
                        click_el = e
                        try:
                            lab = e.find_element(By.XPATH, "ancestor::label[1]")
                            if lab:
                                click_el = lab
                        except Exception:
                            pass
                        xp = _best_xpath_for_element(driver, click_el)

                    if not xp:
                        continue

                    option_xpath_map[_norm_key(lbl)] = xp
                except Exception:
                    continue

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": itype,
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,  # {norm(label)->xpath}
                    "frame_chain": frame_chain,
                },
            )

            block = {
                "question": question,
                "itype": itype,
                "options": options,
                "max_select": _compute_max_select(itype, options),
                "target_id": target_id,
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                },
            }

            question_blocks.append(block)
        except Exception:
            continue

    # Pattern spécifique
    # Objectif: quand les options ne sont PAS des <input type=radio> visibles,
    # mais une liste de <li>/<button> cliquables (ex: Decipher cardrating)

    def _is_nav_like_choice(text: str) -> bool:
        v = _norm_lc(text)
        if not v:
            return False
        nav_tokens = [
            "continue", "continuer", "next", "suivant",
            "back", "retour", "previous", "prÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©cÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©dent", "precedent",
            "ok", "submit", "valider", "envoyer", "send",
            "start", "commencer", "finish", "terminer",
            "close", "fermer", "cancel", "annuler",
            "refuser", "decline",
        ]
        return any(tok in v for tok in nav_tokens)

    def _stable_xpath_for_buttonish(el) -> str:
        """
        Locator stable prioritaire pour Decipher:
        - data-uid est trÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨s souvent unique et stable sur la page.
        - sinon data-label + data-index
        - sinon id
        - sinon XPath absolu.
        """
        try:
            uid = (el.get_attribute("data-uid") or "").strip()
            if uid:
                return f"//*[@data-uid={_xpath_literal(uid)}]"

            dlabel = (el.get_attribute("data-label") or "").strip()
            dindex = (el.get_attribute("data-index") or "").strip()
            if dlabel and dindex:
                return f"(//*[@data-label={_xpath_literal(dlabel)} and @data-index={_xpath_literal(dindex)}])[1]"
        except Exception:
            pass

        return _best_xpath_for_element(driver, el)

    try:
        btn_like = driver.find_elements(
            By.CSS_SELECTOR,
            "button, a[role='button'], [role='button'], .sq-cardrating-button"
        )
    except Exception:
        btn_like = []

    btn_groups: Dict[str, Dict[str, Any]] = {}
    for b in btn_like:
        try:
            if not _is_actionable_visible(b):
                continue

            # Filtre Decipher cardrating : ignore disabled / non-clickable
            cls = _norm_lc(b.get_attribute("class") or "")
            if "sq-cardrating-button" in cls:
                if _norm_lc(b.get_attribute("data-clickable") or "") in ("false", "0"):
                    continue
                if _norm_lc(b.get_attribute("data-disabled") or "") in ("true", "1"):
                    continue

            # Texte (pour cardrating, le texte est dans le <li>)
            t = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
            if (not t or len(t) < 2) and "sq-cardrating-button" in cls:
                # Pattern spécifique
                try:
                    t = _norm(b.find_element(By.CSS_SELECTOR, ".sq-cardrating-content").text)
                except Exception:
                    pass

            if not t or len(t) < 2:
                continue
            if _is_nav_like_choice(t):
                continue

            cont = _nearest_question_container(b)
            if not cont:
                try:
                    cont = b.find_element(By.XPATH, "ancestor::*[self::div or self::section or self::form][1]")
                except Exception:
                    cont = None
            if not cont:
                continue

            cid = (cont.get_attribute("id") or "").strip()
            ccl = _norm_lc(cont.get_attribute("class") or "")
            gk = f"btn_group:{cid}:{ccl}:{id(cont)}"
            g = btn_groups.setdefault(gk, {"container": cont, "buttons": []})
            g["buttons"].append(b)
        except Exception:
            continue

    for _gk, g in (btn_groups or {}).items():
        try:
            cont = g.get("container")
            btns = g.get("buttons") or []
            if len(btns) < 3:
                continue

            # Pattern spécifique
            options: List[str] = []
            for b in btns:
                tt = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
                if not tt or _is_nav_like_choice(tt):
                    continue
                if tt not in options:
                    options.append(tt)

            if len(options) < 3:
                continue

            question = ""
            if cont:
                question = _extract_question_from_container(cont, options=options) or ""

            if not question:
                question = _norm(_find_question_text_near_element(driver, btns[0]))

            # Pattern spécifique
            qlc = _norm_lc(question)
            if qlc and ("un problÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨me est survenu" in qlc or ((len(qlc) < 140) and ("veuillez" in qlc) and (("sÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©lection" in qlc) or ("selection" in qlc)))):
                # Pattern spécifique
                for cand in btns[1:3]:
                    near2 = _norm(_find_question_text_near_element(driver, cand))
                    near2_lc = _norm_lc(near2)
                    if near2 and ("un problÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨me est survenu" not in near2_lc) and not ("veuillez" in near2_lc and "sÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©lection" in near2_lc):
                        question = near2
                        break

            question = _norm(question)
            if not question:
                continue

            sig = (question, "radio")
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)

            # group_key stable-ish: id/class du conteneur + quelques options
            cid = (cont.get_attribute("id") or "").strip() if cont else ""
            ccl = _norm_lc(cont.get_attribute("class") or "") if cont else ""
            opt_sig = "|".join(_norm_key(o) for o in (options[:5] or []))
            group_key = f"radio:button_group:{cid}:{ccl}:{opt_sig}"

            target_id = make_target_id("group", group_key, question)

            option_xpath_map = {}
            for b in btns:
                lbl = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
                if not lbl or _is_nav_like_choice(lbl):
                    continue
                xp = _best_xpath_for_element(driver, b)
                if xp:
                    option_xpath_map[_norm_key(lbl)] = xp

            if not option_xpath_map:
                continue

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                },
            )

            question_blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )
        except Exception:
            continue

    # --- 2) Autres inputs (dropdown / text / textarea / button) ---
    try:
        other_inputs = driver.find_elements(
            By.CSS_SELECTOR,
            "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']",
        )
    except Exception:
        other_inputs = []

    for el in other_inputs:
        try:
            itype = _detect_itype(el)

            # 1) On ignore les champs techniques/hidden
            if itype == "hidden" or _looks_like_system_field(el):
                continue

            # Pattern spécifique
            if not _is_actionable_visible(el):
                continue

            if itype in ("radio", "checkbox", "unknown"):
                continue

            # on ne veut pas transformer un "bouton next" en question
            if itype == "button":
                # 1) filtres structurels (navigation)
                bid = (el.get_attribute("id") or "").strip().lower()
                bname = (el.get_attribute("name") or "").strip().lower()

                if bid in {"next_button", "back_button", "skip_button"}:
                    continue
                if bname in {"next", "back"}:
                    continue

                try:
                    # conteneurs nav typiques (YouGov & autres)
                    if el.find_elements(
                        By.XPATH,
                        "ancestor::*[@role='navigation' or @id='mainNav' or contains(@class,'nav-buttons')][1]"
                    ):
                        continue
                except Exception:
                    pass

                # 2) filtre textuel (plus large)
                txt = _norm(el.text or el.get_attribute("innerText") or "")
                tlc = _norm_lc(txt)
                if tlc in {
                    "next", "suivant", "continue", "continuer",
                    "next page", "previous page",
                    "page suivante", "page prÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©cÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©dente",
                }:
                    continue

            container = _nearest_question_container(el) or el

            question = ""
            if container:
                question = _extract_question_from_container(container, options=[]) or ""

            # Pattern spécifique
            # Pattern spécifique
            # Masqué
            # Pattern spécifique
            multi = False
            hint = None
            try:
                if itype == "dropdown" and container:
                    sels = container.find_elements(By.TAG_NAME, "select")
                    multi = bool(sels and len(sels) >= 2)
                    if multi:
                        hint = _dropdown_field_hint(driver, el)
                        field_labels = {"mois", "month", "annÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©e", "annee", "year", "jour", "day"}
                        qlc = _norm_lc(question)
                        if (qlc in field_labels) or (hint and qlc == _norm_lc(hint)):
                            alt = _find_question_text_near_element(driver, el) or ""
                            alt_lc = _norm_lc(alt)
                            if alt and alt_lc not in field_labels:
                                question = alt
            except Exception:
                pass

            # --- [PATCH] Dropdown unique : détecter si question = placeholder et chercher le vrai texte ---
            # QuestionPro et autres plateformes peuvent extraire le placeholder "-- Sélectionner --" 
            # au lieu de la vraie question qui est dans un élément sibling/parent
            if itype == "dropdown" and question:
                qlc = _norm_lc(question)
                placeholder_patterns = {
                    "selectionner", "sélectionner", "select", "choose", "choisir",
                    "-- selectionner --", "-- sélectionner --", "-- select --",
                    "- select -", "- selectionner -", "- sélectionner -",
                    "please select", "veuillez sélectionner", "veuillez selectionner",
                    "click to select", "cliquez pour sélectionner",
                }
                # Vérifier si la question est un placeholder (match exact ou pattern --)
                is_placeholder = (
                    qlc in placeholder_patterns
                    or (qlc.startswith("--") and qlc.endswith("--"))
                    or (qlc.startswith("-") and qlc.endswith("-") and len(qlc) < 30)
                    or qlc in {"", "none", "aucun", "aucune"}
                )
                if is_placeholder:
                    alt = _find_question_text_near_element(driver, el) or ""
                    if alt and _norm_lc(alt) not in placeholder_patterns:
                        question = alt

            if not question:
                # Pattern spécifique
                question = _find_question_text_near_element(driver, el) or ""

            if not question:
                question = _find_associated_label(driver, el) or ""
            question = _norm(question)

            # --- [PATCH SSI/Confirmit] Filtrer les instructions de validation et chercher la vraie question ---
            if _is_validation_instruction(question) or not question:
                # Pattern spécifique
                ssi_q = _extract_ssi_confirmit_question(driver, el)
                if ssi_q:
                    question = ssi_q
                elif _is_validation_instruction(question):
                    # Pattern spécifique
                    continue

            if not question:
                continue

            # Pattern spécifique
            if itype in ("text", "textarea"):                
                try:
                    cont_id = (container.get_attribute("id") or "").strip()
                    nm = (el.get_attribute("name") or "").strip()

                    # prefix: "QA03:948176_1" -> "QA03:948176"
                    prefix = nm
                    m_pref = re.match(r"^(.*)_(\d{1,3})$", nm)
                    if m_pref:
                        prefix = m_pref.group(1)

                    # fallback si container id vide: on stabilise avec un xpath de container (rare)
                    if not cont_id and container:
                        try:
                            cont_id = _best_xpath_for_element(driver, container) or ""
                        except Exception:
                            cont_id = ""

                    group_key = f"multitext:{cont_id}:{prefix}"
                    if group_key in seen_multi_text_groups:
                        continue

                    # Pattern spécifique
                    try:
                        peers = container.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea")
                    except Exception:
                        peers = []

                    fields = []
                    peer_names = []
                    for p in peers:
                        try:
                            pt = _detect_itype(p)
                            if pt != itype:
                                continue
                            if _looks_like_system_field(p):
                                continue
                            if not _is_actionable_visible(p):
                                continue
                            pn = (p.get_attribute("name") or "").strip()
                            if pn:
                                peer_names.append(pn)
                            fields.append(p)
                        except Exception:
                            continue

                    if len(fields) >= 2:
                        # Pattern spécifique
                        container_txt = _norm_lc(container.text or container.get_attribute("innerText") or "")
                        has_one_per_box = (
                            ("par case" in container_txt)
                            or ("one per box" in container_txt)
                            or ("one per field" in container_txt)
                        )

                        same_prefix_count = 0
                        if prefix and prefix != nm:
                            for pn in peer_names:
                                mm = re.match(r"^(.*)_(\d{1,3})$", pn)
                                if mm and mm.group(1) == prefix:
                                    same_prefix_count += 1

                        if has_one_per_box or same_prefix_count >= 2:
                            max_items = min(3, len(fields))
                            multi_target_id = make_target_id("multi", group_key, question)

                            field_payloads = []
                            for f in fields:
                                try:
                                    fid = (f.get_attribute("id") or "").strip()
                                    fname = (f.get_attribute("name") or "").strip()
                                    ftag = (f.tag_name or "").strip().lower()
                                    fxp = _best_xpath_for_element(driver, f)

                                    falt = []
                                    try:
                                        if ftag and fname:
                                            falt.append(f"//{ftag}[@name={_xpath_literal(fname)}]")
                                        elif fname:
                                            falt.append(f"//*[@name={_xpath_literal(fname)}]")
                                    except Exception:
                                        pass
                                    try:
                                        if fid:
                                            falt.append(f"//*[@id='{fid}']")
                                    except Exception:
                                        pass

                                    falt = [x for x in dict.fromkeys(falt) if x and x != fxp][:4]

                                    field_payloads.append(
                                        {"xpath": fxp, "alt_xpaths": falt, "name": fname, "id": fid, "tag": ftag}
                                    )
                                except Exception:
                                    continue

                            if field_payloads:
                                register_target(
                                    multi_target_id,
                                    {
                                        "kind": "multi_text",
                                        "itype": itype,
                                        "question": question,
                                        "fields": field_payloads,
                                        "frame_chain": frame_chain,
                                        "meta": {"max_items": max_items, "multi_text": True},
                                    },
                                )

                                question_blocks.append(
                                    {
                                        "question": question,
                                        "itype": itype,
                                        "options": [],
                                        "max_select": max_items,  # Pattern spécifique
                                        "target_id": multi_target_id,
                                        "context": {
                                            "kind": "multi_text",
                                            "fields_count": len(field_payloads),
                                            "max_items": max_items,
                                            "name_prefix": prefix or "",
                                        },
                                    }
                                )

                                seen_multi_text_groups.add(group_key)
                                continue
                except Exception:
                    pass

            # Pattern spécifique
            # Pattern spécifique
            # Pattern spécifique
            if itype == "dropdown":
                try:
                    if not multi and container:
                        sels = container.find_elements(By.TAG_NAME, "select")
                        if len(sels) >= 2:
                            multi = True
                    if multi:
                        if not hint:
                            hint = _dropdown_field_hint(driver, el)
                        if hint and hint.lower() not in (question or "").lower():
                            question = _norm(f"{question} ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â¦ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â {hint}")
                except Exception:
                    pass
            sig = (question, itype)
            if itype == "dropdown":
                try:
                    sig = (
                        question,
                        itype,
                        (el.get_attribute("name") or "").strip(),
                        (el.get_attribute("id") or "").strip(),
                    )
                except Exception:
                    sig = (question, itype)

            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)

            options: List[str] = []
            if itype == "dropdown":
                try:
                    for o in el.find_elements(By.TAG_NAME, "option"):
                        if o.get_attribute("disabled"):
                            continue
                        t = _norm(o.text or o.get_attribute("innerText") or "")
                        if t:
                            options.append(t)
                    options = list(dict.fromkeys(options))
                except Exception:
                    pass

            # --- target_id + registry pour single input
            el_id = (el.get_attribute("id") or "").strip()
            el_name = (el.get_attribute("name") or "").strip()
            el_tag = (el.tag_name or "").strip().lower()

            single_key = f"{itype}:{el_id}:{el_name}"
            target_id = make_target_id("single", single_key, question)

            xpath = _best_xpath_for_element(driver, el)

            # Locators alternatifs (stables) : en pratique, @name survit aux re-render Wicket/Bootstrap-select
            alt_xpaths = []
            try:
                if el_tag and el_name:
                    alt_xpaths.append(f"//{el_tag}[@name={_xpath_literal(el_name)}]")
                elif el_name:
                    alt_xpaths.append(f"//*[@name={_xpath_literal(el_name)}]")
            except Exception:
                pass

            try:
                if el_id:
                    alt_xpaths.append(f"//*[@id='{el_id}']")
            except Exception:
                pass

            # Pattern spécifique
            alt_xpaths = [x for x in dict.fromkeys(alt_xpaths) if x and x != xpath][:4]

            register_target(
                target_id,
                {
                    "kind": "single",
                    "itype": itype,
                    "question": question,
                    "xpath": xpath,
                    "alt_xpaths": alt_xpaths,
                    "tag": el_tag,
                    "name": el_name,
                    "id": el_id,
                    "frame_chain": frame_chain,
                },
            )

            block = {
                "question": question,
                "itype": itype,
                "options": options,
                "max_select": _compute_max_select(itype, options),
                "target_id": target_id,
                "context": {
                    "kind": "single",
                    "tag": el.tag_name,
                    "name": el.get_attribute("name"),
                    "id": el.get_attribute("id"),
                    "role": el.get_attribute("role"),
                },
            }
            
            question_blocks.append(block)

        except Exception:
            continue

    return question_blocks



# ================================================================================
# POINT D'ENTRÉE - ANALYZE DOM
# ================================================================================

def analyze_dom(driver) -> List[Dict[str, Any]]:
    """
    Analyse le DOM et retourne une liste de QuestionBlock.
    Frame-aware: choisit automatiquement le meilleur contexte (default ou iframe) jusqu'ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â  depth=DOM_FRAME_MAX_DEPTH (dÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¾ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©faut=2).
    """
    clear_registry()

    _wait_for_survey_dom(driver)
    max_depth = int(os.getenv("DOM_FRAME_MAX_DEPTH", "2") or "2")
    best_chain, _meta = _select_best_frame_chain(driver, max_depth=max_depth)
    if _env_truthy("DOM_CONTEXT_DEBUG", "1"):
        print(
            f"[DOM_CONTEXT_DEBUG] analyze_dom selected_chain={best_chain} "
            f"selected_ps_date_question={_meta.get('selected_ps_date_question_count', 0)} "
            f"score={_meta.get('score', 0)}"
        )
        print(
            f"[DOM_CONTEXT_DEBUG] analyze_dom stage=context_selected "
            f"blocks_count=0 chain_len={len(best_chain or [])}"
        )

    def _blocks_summary_preview(items: List[Dict[str, Any]], max_items: int = 3) -> str:
        preview = []
        for b in (items or [])[:max_items]:
            q = _norm((b or {}).get("question") or "")
            preview.append(
                {
                    "itype": (b or {}).get("itype"),
                    "question": q[:60],
                    "options": len((b or {}).get("options") or []),
                }
            )
        return str(preview)

    # Pattern spécifique
    blocks: List[Dict[str, Any]] = []
    chain: List[Any] = []
    with switch_to_frame_chain(driver, best_chain) as ok:
        chain = best_chain if ok else []

        # --- FocusVision/Decipher sliderpoints (matrix dropdowns) ---
        sp_blocks = extract_sliderpoints_question_blocks(driver)
        if sp_blocks:
            return sp_blocks

        blocks = _analyze_dom_current_context(driver, frame_chain=chain)
        blocks.extend(_extract_focusvision_answers_list_groups(driver, frame_chain=chain))
        blocks.extend(_extract_angular_material_radio_groups(driver, frame_chain=chain))

        if not blocks:
            blocks = _extract_decipher_answers_list_fallback(driver, frame_chain=chain)

    # Pattern spécifique
    if not blocks and chain:
        with switch_to_frame_chain(driver, []) as ok:
            if ok:
                sp_blocks = extract_sliderpoints_question_blocks(driver)
                if sp_blocks:
                    return sp_blocks
                blocks = _analyze_dom_current_context(driver)
                blocks.extend(_extract_focusvision_answers_list_groups(driver))
                blocks.extend(_extract_angular_material_radio_groups(driver))

                if not blocks:
                    blocks = _extract_decipher_answers_list_fallback(driver, frame_chain=chain)

    # Dédup ciblée FocusVision/Decipher:
    # sur certains DOMs, l'extraction générique peut créer un bloc "group" incomplet
    # (context.group_key vide) avant l'extracteur FocusVision dédié.
    # Cela peut pousser OpenAI à renvoyer un target_id non applicable.
    # Règle: pour une même question/itype/options, on garde la variante avec group_key non vide.
    dedup_map: dict[tuple[str, str, tuple[str, ...]], dict] = {}
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue
        q_sig = _norm((b.get("question") or "")).lower()
        t_sig = _norm((b.get("itype") or "")).lower()
        o_sig = tuple(sorted(_norm((o or "")).lower() for o in (b.get("options") or []) if _norm(o)))
        sig = (q_sig, t_sig, o_sig)
        cur = dedup_map.get(sig)

        def _group_key_len(x: dict) -> int:
            try:
                return len(((x.get("context") or {}).get("group_key") or "").strip())
            except Exception:
                return 0

        if cur is None:
            dedup_map[sig] = b
            continue

        if _group_key_len(b) > _group_key_len(cur):
            dedup_map[sig] = b

    if dedup_map:
        blocks = list(dedup_map.values())

    blocks = _prune_focusvision_fragmented_groups(blocks)

    if _env_truthy("DOM_CONTEXT_DEBUG", "1"):
        print(
            f"[DOM_CONTEXT_DEBUG] analyze_dom stage=raw_extraction "
            f"blocks_count={len(blocks or [])} sample={_blocks_summary_preview(blocks)}"
        )
        print(
            f"[DOM_CONTEXT_DEBUG] analyze_dom stage=before_return "
            f"blocks_count={len(blocks or [])} sample={_blocks_summary_preview(blocks)}"
        )

    return blocks


def _prune_focusvision_fragmented_groups(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Supprime les fragments mono-option d'un même groupe FocusVision/Decipher.

    Le prune est strictement déclenché quand un bloc "riche" est marqué
    `context.focusvision_answers_list=True` (créé par l'extracteur dédié).
    """
    rich_focusvision = [
        b for b in (blocks or [])
        if isinstance(b, dict)
        and ((b.get("context") or {}).get("focusvision_answers_list") is True)
        and len((b.get("options") or [])) >= 2
    ]
    if not rich_focusvision:
        return blocks

    pruned: list[dict] = []
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue

        b_q = _norm((b.get("question") or "")).lower()
        b_t = _norm((b.get("itype") or "")).lower()
        b_opts = {_norm((o or "")).lower() for o in (b.get("options") or []) if _norm(o)}

        drop_fragment = False
        if len(b_opts) <= 1 and b_t in {"checkbox", "radio"}:
            for rb in rich_focusvision:
                r_q = _norm((rb.get("question") or "")).lower()
                r_t = _norm((rb.get("itype") or "")).lower()
                r_opts = {_norm((o or "")).lower() for o in (rb.get("options") or []) if _norm(o)}
                if b_q == r_q and b_t == r_t and b_opts and b_opts.issubset(r_opts):
                    drop_fragment = True
                    break

        if not drop_fragment:
            pruned.append(b)

    return pruned
