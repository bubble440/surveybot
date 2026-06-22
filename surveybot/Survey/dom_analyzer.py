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
from typing import List, Dict, Any, Tuple, Set
import os, re, json

from Survey.log_utils import is_debug, log_debug, log_info

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
        _find_group_heading_text_near_element, _extract_mriweb_grid_question_text,
        _group_key_for_choice, _compute_max_select, _compute_min_select
    )
    
    # Gestion des frames
    from Survey.dom_frame_selector import (
        _wait_for_survey_dom, _score_dom_context, _select_best_frame_chain
    )
    
    # Extracteurs platform-spécifiques
    from Survey.dom_extractors_decipher import (
        _extract_focusvision_answers_list_groups,
        _extract_focusvision_cardsort_block,
        _extract_decipher_table_text_rows_blocks,
        _extract_decipher_grid_single_col_text_rows,
        _extract_decipher_grid_select_blocks,
        _extract_decipher_answers_list_fallback,
        _extract_qarts_hidden_answers_groups,
        _extract_decipher_ranksort_dropdown_blocks,
        _extract_decipher_atmrating_blocks,
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
        _extract_intellisurvey_table_matrix_blocks,
        _extract_encuesta_matrix_blocks,
        _extract_yougov_grid_text_question_blocks,
        _extract_cmix_simple_grid_question_blocks,
        _extract_cmix_grid_question_blocks,
        _extract_cmix_radio_question_blocks,
        _extract_ipsos_slider_question_blocks,
        _extract_confirmit_slider_grid_blocks,
        _extract_cloudresearch_sentry_blocks,
        _extract_purespectrum_date_dropdown_blocks,
        _extract_ps_select_dropdown_blocks,
        _extract_purespectrum_mobile_date_blocks,
        _extract_collapsed_section_radio_rows,
        _extract_jqm_lrw_collapsible_radio_rows,
        _extract_jqm_lrw_collapsible_checkbox_rows,
        _extract_custom_testid_single_select_radio_blocks,
        _extract_button_choice_radio_blocks,
        _extract_custom_testid_multi_select_checkbox_blocks,
        _extract_single_consent_checkbox_block,
        _extract_consent_modal_radio_block,
        _extract_confirmit_wix_checkbox_grid_blocks,
        _extract_confirmit_wix_fieldset_radio_block,
        _extract_confirmit_wix_rankedorderclick_block,
        _extract_runtime_answerrow_radio_blocks,
        _extract_toluna_runtime_ranking_blocks,
        _extract_kantar_rowpicker_radio_blocks,
        _extract_kantar_rowrank_blocks,
        _extract_label_radio_list_blocks,
        _extract_qualtrics_choice_structure_radio_blocks,
        _extract_qualtrics_choice_structure_checkbox_blocks,
        _extract_qualtrics_dl_select_blocks,
        _extract_qualtrics_sl_text_blocks,
        _extract_qualtrics_form_multi_text_blocks,
        _extract_qualtrics_te_matrix_multi_text_blocks,
        _extract_qualtrics_bankedsa_single_row_radio_blocks,
        _extract_qualtrics_matrix_dropdown_row_blocks,
        _extract_decipher_clickable_ranking_blocks,
        _extract_savanta_jqm_carousel_block,
        _extract_questmindshare_chatbot_blocks,
        _extract_confirmit_cf_desktop_grid_blocks,
        _extract_confirmit_cf_bipolar_button_grid_blocks,
        _extract_confirmit_cf_hrs_single_blocks,
        _extract_groupcaliber_rating_row_blocks,
        _extract_confirmit_cf_carousel_blocks,
        _extract_confirmit_cf_single_choice_blocks,
        _extract_confirmit_cf_single_image_choice_blocks,
        _extract_confirmit_cf_multi_choice_blocks,
        _extract_confirmit_cf_numeric_list_blocks,
        _extract_confirmit_cf_open_list_blocks,
        _extract_runtime_dropdown_blocks,
        _extract_rps_select_blocks,
        _extract_ssi_confirmit_native_grid_blocks,
        _extract_gfk_accordion_radio_rows,
        _extract_askia_statement_list_blocks,
        _extract_askia_myresponse_radio_blocks,
        _extract_askia_myresponse_checkbox_blocks,
        _extract_askia_responsive_table_checkbox_rows,
        _extract_askia_ranking_isotope_blocks,
        _extract_askia_adc_slider_blocks,
        _extract_confirmit_cf_ranking_blocks,
        _extract_datadiggers_icontrol_radio_block,
        _extract_prodege_prescreener_radio_block,
        _extract_researchnow_autoscreener_radio_blocks,
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
        _find_group_heading_text_near_element, _extract_mriweb_grid_question_text,
        _group_key_for_choice, _compute_max_select, _compute_min_select
    )
    from Survey.dom_frame_selector import (
        _wait_for_survey_dom, _score_dom_context, _select_best_frame_chain
    )
    from Survey.dom_extractors_decipher import (
        _extract_focusvision_answers_list_groups,
        _extract_focusvision_cardsort_block,
        _extract_decipher_table_text_rows_blocks,
        _extract_decipher_grid_single_col_text_rows,
        _extract_decipher_grid_select_blocks,
        _extract_decipher_answers_list_fallback,
        _extract_qarts_hidden_answers_groups,
        _extract_decipher_ranksort_dropdown_blocks,
        _extract_decipher_atmrating_blocks,
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
        _extract_intellisurvey_table_matrix_blocks,
        _extract_encuesta_matrix_blocks,
        _extract_yougov_grid_text_question_blocks,
        _extract_cmix_simple_grid_question_blocks,
        _extract_cmix_grid_question_blocks,
        _extract_cmix_radio_question_blocks,
        _extract_ipsos_slider_question_blocks,
        _extract_confirmit_slider_grid_blocks,
        _extract_cloudresearch_sentry_blocks,
        _extract_purespectrum_date_dropdown_blocks,
        _extract_ps_select_dropdown_blocks,
        _extract_purespectrum_mobile_date_blocks,
        _extract_collapsed_section_radio_rows,
        _extract_jqm_lrw_collapsible_radio_rows,
        _extract_jqm_lrw_collapsible_checkbox_rows,
        _extract_custom_testid_single_select_radio_blocks,
        _extract_button_choice_radio_blocks,
        _extract_custom_testid_multi_select_checkbox_blocks,
        _extract_single_consent_checkbox_block,
        _extract_consent_modal_radio_block,
        _extract_confirmit_wix_checkbox_grid_blocks,
        _extract_confirmit_wix_fieldset_radio_block,
        _extract_confirmit_wix_rankedorderclick_block,
        _extract_runtime_answerrow_radio_blocks,
        _extract_toluna_runtime_ranking_blocks,
        _extract_kantar_rowpicker_radio_blocks,
        _extract_kantar_rowrank_blocks,
        _extract_label_radio_list_blocks,
        _extract_qualtrics_choice_structure_radio_blocks,
        _extract_qualtrics_choice_structure_checkbox_blocks,
        _extract_qualtrics_dl_select_blocks,
        _extract_qualtrics_sl_text_blocks,
        _extract_qualtrics_form_multi_text_blocks,
        _extract_qualtrics_te_matrix_multi_text_blocks,
        _extract_qualtrics_bankedsa_single_row_radio_blocks,
        _extract_qualtrics_matrix_dropdown_row_blocks,
        _extract_decipher_clickable_ranking_blocks,
        _extract_savanta_jqm_carousel_block,
        _extract_questmindshare_chatbot_blocks,
        _extract_confirmit_cf_desktop_grid_blocks,
        _extract_confirmit_cf_bipolar_button_grid_blocks,
        _extract_confirmit_cf_hrs_single_blocks,
        _extract_groupcaliber_rating_row_blocks,
        _extract_confirmit_cf_carousel_blocks,
        _extract_confirmit_cf_single_choice_blocks,
        _extract_confirmit_cf_single_image_choice_blocks,
        _extract_confirmit_cf_multi_choice_blocks,
        _extract_confirmit_cf_numeric_list_blocks,
        _extract_confirmit_cf_open_list_blocks,
        _extract_runtime_dropdown_blocks,
        _extract_rps_select_blocks,
        _extract_ssi_confirmit_native_grid_blocks,
        _extract_gfk_accordion_radio_rows,
        _extract_askia_statement_list_blocks,
        _extract_askia_myresponse_radio_blocks,
        _extract_askia_myresponse_checkbox_blocks,
        _extract_askia_responsive_table_checkbox_rows,
        _extract_askia_ranking_isotope_blocks,
        _extract_askia_adc_slider_blocks,
        _extract_confirmit_cf_ranking_blocks,
        _extract_datadiggers_icontrol_radio_block,
        _extract_prodege_prescreener_radio_block,
        _extract_researchnow_autoscreener_radio_blocks,
    )




def _pw_page(d):
    """Extrait la Page Playwright native depuis un PlaywrightDriverShim ou retourne d tel quel."""
    if hasattr(d, '_page'):
        return d._page
    return d


def _handle(el):
    """Extrait le ElementHandle Playwright natif depuis un PlaywrightElementShim, ou retourne el."""
    if hasattr(el, '_h'):
        return el._h
    return el

def _is_auxiliary_text_for_choice_group(driver, el, container, question: str) -> bool:
    """
    Détecte les champs texte auxiliaires affichés dans le même bloc qu'une question
    principale à options (radio/checkbox/boutons).

    Stratégie unique (score) :
      - prérequis strict: le container contient déjà >=2 options de choix
      - score >=3 sur 4 signaux DOM pour classer le champ comme auxiliaire
    """
    if not container:
        return False

    try:
        choice_nodes = container.find_elements(
            "css selector",
            "input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox'], button, a[role='button']",
        )
    except Exception:
        return False

    option_words: Set[str] = set()
    choice_count = 0
    for node in choice_nodes or []:
        try:
            txt = _norm(node.text or node.get_attribute("innerText") or node.get_attribute("value") or "")
            if not txt:
                txt = _norm(_find_associated_label(driver, node) or "")
            if not txt:
                continue
            tlc = _norm_lc(txt)
            if tlc in {"next", "suivant", "continue", "continuer", "back", "retour", "submit", "valider"}:
                continue
            choice_count += 1
            option_words.update(re.findall(r"[a-z0-9à-ÿ]{2,}", tlc))
        except Exception:
            continue

    if choice_count < 2:
        return False

    # Cas inline "Autre, précisez" : le champ texte est rendu dans le même
    # wrapper d'option qu'un contrôle radio/checkbox. Dans ce cas, on le
    # classe directement en auxiliaire pour éviter un bloc single parasite.
    # Détection DOM-first via ancêtres/wrappers d'option observables.
    try:
        option_roots = el.find_elements(
            "xpath",
            "ancestor::*[self::label or self::li or contains(concat(' ', normalize-space(@class), ' '), ' answer_options ') or contains(concat(' ', normalize-space(@class), ' '), ' option ') or contains(concat(' ', normalize-space(@class), ' '), ' form-check ') ]",
        )
    except Exception:
        option_roots = []

    for root in option_roots or []:
        try:
            if container and root == container:
                continue
            has_choice_in_root = bool(root.find_elements(
                "css selector",
                "input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox']",
            ))
            if has_choice_in_root:
                return True
        except Exception:
            continue

    # Garde-fou JS pour les structures qui n'exposent pas bien les ancêtres.
    try:
        inline_with_choice = bool(_pw_page(driver).evaluate(
            """([el, container]) => {
            if (!el || !container || !el.closest || !container.contains) return false;
            const optionRoot = el.closest('.answer_options, label, li, .option, .form-check, [role="radio"], [role="checkbox"], .cf-radio-answer, .cf-checkbox-answer');
            if (!optionRoot || !container.contains(optionRoot)) return false;
            return !!optionRoot.querySelector('input[type="radio"], input[type="checkbox"], [role="radio"], [role="checkbox"]');
        }""", [_handle(el), _handle(container)]))
        if inline_with_choice:
            return True
    except Exception:
        pass

    el_aria_label = _norm(el.get_attribute("aria-label") or "")
    el_aria_labelledby = _norm(el.get_attribute("aria-labelledby") or "")
    own_label = _norm(_find_associated_label(driver, el) or "")
    has_own_label = bool(own_label or el_aria_label or el_aria_labelledby)

    qlc = _norm_lc(question or "")
    q_words = set(re.findall(r"[a-z0-9à-ÿ]{2,}", qlc))
    overlap = 0.0
    if q_words:
        overlap = len(q_words & option_words) / max(1, len(q_words))

    generic_q = (
        not qlc
        or qlc in {"other", "autre", "enter your answer here", "type your answer", "please specify"}
        or "choose" in qlc
        or "select" in qlc
        or overlap >= 0.7
    )

    placeholder = _norm_lc(el.get_attribute("placeholder") or "")
    generic_placeholder = placeholder in {
        "enter your answer here",
        "type your answer",
        "other",
        "please specify",
    }
    required_attr = _norm_lc(el.get_attribute("required") or "")
    is_required = required_attr in {"true", "required", "1"}

    try:
        container_text = _norm_lc(container.text or container.get_attribute("innerText") or "")
    except Exception:
        container_text = ""

    has_choice_instruction = (
        "choose exactly" in container_text
        or "select all that apply" in container_text
        or "select one" in container_text
        or "choose one" in container_text
    )

    score = 0
    if not has_own_label:
        score += 1
    if generic_q:
        score += 1
    if generic_placeholder or not is_required:
        score += 1
    if has_choice_instruction:
        score += 1
    return score >= 3


def _is_checkbox_optout_companion_for_text(driver, els: List[Any], options: List[str]) -> bool:
    """
    Détecte un "opt-out" checkbox auxiliaire lié à un champ texte (pattern mrIWeb/Kantar).

    Garde-fous DOM-first (tous requis):
    - groupe checkbox singleton (1 input, 1 option)
    - signal opt-out observable sur l'input (name *_XREF / value REF / isexclusive=true / openendid
      / class askia-exclusive)
    - libellé option = "je ne souhaite pas répondre" (ou équivalent proche)
      EXCEPTION Askia : libellé libre si class="askia-exclusive" + textarea dans le même <table>
    - `openendid` référence un input texte/textarea présent dans le même conteneur de question
    """
    if len(els or []) != 1 or len(options or []) != 1:
        return False

    el = els[0]

    # --- Pattern Askia : class="askia-exclusive" ---
    # Garde-fous stricts (DOM-first) :
    #   1. L'input porte la classe "askia-exclusive"
    #   2. Un <textarea> existe dans le même ancêtre <table> (même question composite)
    # Le libellé n'est pas contraint (typiquement "Vous ne savez pas").
    try:
        el_classes = _norm_lc(el.get_attribute("class") or "")
        if "askia-exclusive" in el_classes:
            try:
                ancestor_tables = el.find_elements("xpath", "ancestor::table[1]")
                if ancestor_tables:
                    has_textarea = bool(
                        ancestor_tables[0].find_elements("css selector", "textarea")
                    )
                    if has_textarea:
                        log_debug(
                            "[DOM_GROUPING]",
                            f"skip_askia_exclusive_optout option={options[0]!r}",
                        )
                        return True
            except Exception:
                pass
    except Exception:
        pass

    opt_lc = _norm_lc(options[0])
    if not any(
        token in opt_lc
        for token in (
            "souhaite pas r",
            "prefer not to answer",
            "no answer",
        )
    ):
        return False

    try:
        raw_name = _norm_lc(el.get_attribute("name") or "")
        raw_value = _norm_lc(el.get_attribute("value") or "")
        raw_isexclusive = _norm_lc(el.get_attribute("isexclusive") or "")
        openendid = _norm(el.get_attribute("openendid") or "")
    except Exception:
        return False

    has_optout_flag = (
        bool(openendid)
        or raw_name.endswith("_xref")
        or raw_value == "ref"
        or raw_isexclusive in {"true", "1", "yes", "on"}
    )
    if not has_optout_flag:
        return False

    if not openendid:
        return False

    try:
        ref_txt = driver.find_elements("css selector", f"input#{openendid}, textarea#{openendid}")
    except Exception:
        ref_txt = []

    if ref_txt:
        return True

    try:
        container = _nearest_question_container(el)
    except Exception:
        container = None

    if not container:
        return False

    try:
        return bool(
            container.find_elements("css selector", f"input#{openendid}, textarea#{openendid}")
        )
    except Exception:
        return False


def _is_open_ended_choice_companion(el, container) -> bool:
    """
    Détecte les champs open-end de type oeXXXX.Y liés à une option de choix ansXXXX.*
    dans le même bloc de question (cas FocusVision/Forsta "Autre - préciser").
    """
    if not container:
        return False

    try:
        el_id_lc = _norm_lc(el.get_attribute("id") or "")
        el_name_lc = _norm_lc(el.get_attribute("name") or "")
        cls_lc = _norm_lc(el.get_attribute("class") or "")
    except Exception:
        return False

    marker = el_name_lc or el_id_lc
    match = re.match(r"^oe(\d+)(?:\.\d+)?$", marker)
    if not match:
        return False

    has_oe_class = bool(re.search(r"(?:^|\s)oe(?:\s|$)", cls_lc))
    if not has_oe_class:
        return False

    stem = match.group(1)
    try:
        choice_inputs = container.find_elements("css selector", "input[type='radio'][name], input[type='checkbox'][name]")
    except Exception:
        return False

    for choice in choice_inputs or []:
        try:
            nm = _norm_lc(choice.get_attribute("name") or "")
            if re.match(rf"^ans{re.escape(stem)}\.", nm):
                return True
        except Exception:
            continue
    return False


def _is_decipher_dropdown_open_companion(container) -> bool:
    """
    Détecte le champ texte "Autre" compagnon d'un dropdown Decipher/FocusVision.

    Pattern DOM : le div.question parent du champ texte porte simultanément
    - id="question_<QID>_open"  (suffixe _open sur l'id de la question)
    - class="... label_<QID>_open ..."  (même suffixe sur la classe label)
    Le dropdown correspondant est dans un div.question frère avec id="question_<QID>"
    et class="... label_<QID> ...".  Ces deux conditions conjointes sont un signal
    Decipher exclusif — aucun autre provider n'utilise ce schéma d'id/class.
    Guard : double condition id + class → zéro faux positif inter-platform.
    """
    if not container:
        return False
    try:
        cid = _norm_lc(container.get_attribute("id") or "")
        cls = _norm_lc(container.get_attribute("class") or "")
    except Exception:
        return False
    id_open = bool(re.match(r"^question_\w+_open$", cid))
    cls_open = bool(re.search(r"\blabel_\w+_open\b", cls))
    return id_open and cls_open


def _is_angular_material_image_only_textarea_question(
    driver,
    el,
    question: str,
) -> bool:
    """
    Détecte le cas Angular Material où la "question" est une image (img.taImage)
    et le texte extrait n'est qu'un heading de page (ou vide).

    Critères DOM stricts (additif, scope minimal):
    - input courant = textarea dans un wrapper mat-form-field,
    - présence de img.taImage dans le conteneur survey local,
    - aucun texte question lisible proche du mat-form-field,
    - texte extrait vide OU identique au heading local.
    """
    try:
        tag = (el.tag_name or "").strip().lower()
        if tag != "textarea":
            return False

        survey_scope = None
        try:
            survey_scope = el.find_element(
                "xpath",
                "ancestor::*[self::app-survey or contains(@class,'survey-window') or contains(@class,'survey-section')][1]",
            )
        except Exception:
            survey_scope = None
        if not survey_scope:
            return False

        # Garde-fou Angular Material textarea
        has_mat_form_field = False
        try:
            has_mat_form_field = bool(el.find_elements("xpath", "ancestor::mat-form-field[1]"))
        except Exception:
            has_mat_form_field = False
        if not has_mat_form_field:
            return False

        # Question rendue via image
        try:
            ta_images = survey_scope.find_elements("css selector", "img.taImage, img[class*='taImage']")
        except Exception:
            ta_images = []
        if not ta_images:
            return False

        q_norm = _norm(question)
        q_lc = _norm_lc(q_norm)

        # Heading local (h1/h2) : souvent "Commençons cette enquête !"
        heading = ""
        try:
            heading_nodes = survey_scope.find_elements(
                "css selector",
                ".header-window h1, .header-window h2, h1[translate], h1, h2",
            )
            for hn in heading_nodes:
                heading_txt = _norm(hn.text or hn.get_attribute("innerText") or "")
                if heading_txt:
                    heading = heading_txt
                    break
        except Exception:
            heading = ""

        # Vérifier qu'il n'existe pas de texte question exploitable à proximité du champ
        # (hors heading et hors texte du textarea lui-même)
        near_text = ""
        try:
            near_text = _norm(_find_question_text_near_element(driver, el) or "")
        except Exception:
            near_text = ""

        near_lc = _norm_lc(near_text)
        heading_lc = _norm_lc(heading)
        has_readable_near_question = bool(near_text) and near_lc not in {"", heading_lc}

        if has_readable_near_question:
            return False

        # Cas nominal du bug ciblé:
        # - image taImage présente dans le scope de question,
        # - aucun texte de question lisible près du textarea.
        # Dans ce contexte DOM-only, on considère la question non exploitable
        # même si un texte générique court a été extrait ailleurs.
        if not has_readable_near_question:
            return True

        return (not q_norm) or (bool(heading_lc) and q_lc == heading_lc)
    except Exception:
        return False


def _is_other_specify_choice_companion(driver, el, container, question: str) -> bool:
    """
    Détecte un champ texte compagnon d'une option "Autre/Other/Précisez" dans un
    groupe radio/checkbox pour éviter un bloc text autonome parasite.
    """
    if not container:
        return False

    try:
        choice_inputs = container.find_elements("css selector", "input[type='radio'], input[type='checkbox']")
    except Exception:
        return False
    if len(choice_inputs or []) < 2:
        return False

    try:
        is_other_context = bool(_pw_page(driver).evaluate(
            r"""([el, container]) => {
            if (!el || !container || !container.contains(el)) return false;
            const norm = (v) => (v || '').toLowerCase().replace(/\s+/g, ' ').trim();
            const kw = ['other', 'autre', 'précisez', 'precisez', 'specify', 'please specify'];
            const wrappers = [el.closest('label'), el.closest('.answer-container'), el.closest('li'), el.parentElement].filter(Boolean);
            for (const node of wrappers) {
              const hasChoice = !!node.querySelector('input[type="radio"], input[type="checkbox"], [role="radio"], [role="checkbox"]');
              if (!hasChoice) continue;
              const txt = norm(node.textContent || node.innerText || '');
              if (kw.some(k => txt.includes(k))) return true;
            }
            const prev = el.previousElementSibling;
            const prevTxt = norm(prev ? (prev.textContent || prev.innerText || '') : '');
            if (kw.some(k => prevTxt.includes(k))) return true;
            return false;
        }""", [_handle(el), _handle(container)]))
    except Exception:
        is_other_context = False

    if not is_other_context:
        return False

    parent_question = _norm(_extract_question_from_container(container, options=[]) or "")
    question_norm = _norm(question or "")
    parent_cmp = re.sub(r"\W+", "", _norm_lc(parent_question))
    question_cmp = re.sub(r"\W+", "", _norm_lc(question_norm))

    if not question_cmp:
        return True

    if parent_cmp and (question_cmp == parent_cmp or question_cmp in parent_cmp or parent_cmp in question_cmp):
        return True

    option_texts: List[str] = []
    for choice in choice_inputs or []:
        try:
            label_txt = _norm(_find_associated_label(driver, choice) or choice.text or choice.get_attribute("value") or "")
            if label_txt:
                option_texts.append(_norm_lc(label_txt))
        except Exception:
            continue
    option_texts = list(dict.fromkeys(option_texts))
    if not option_texts:
        return False

    option_hits = sum(1 for txt in option_texts if txt and txt in _norm_lc(question_norm))
    return option_hits >= max(2, min(4, len(option_texts) // 2))


def _looks_like_aggregated_container_option(option_text: str, question_text: str) -> bool:
    """
    Détecte un faux bloc mono-option où l'option recopie exactement le texte
    agrégé de la question (question + liste de choix concaténée).
    """
    opt_lc = _norm_lc(option_text)
    q_lc = _norm_lc(question_text)
    if not opt_lc or not q_lc:
        return False
    if opt_lc != q_lc:
        return False

    words = re.findall(r"[a-z0-9à-ÿ]{2,}", q_lc)
    return len(words) >= 20


def _selection_signal_text(driver, el, question_text: str | None = None) -> str:
    """
    Construit un signal texte pour les règles de cardinalité:
    question + instruction DOM (si présente).
    """
    parts: list[str] = []
    q = _norm(question_text or "")
    if q:
        parts.append(q)

    try:
        instruction = _norm(_pw_page(driver).evaluate(
            r"""(el) => {
            if (!el) return '';
            const norm = (v) => (v || '').replace(/\s+/g, ' ').trim();
            const isVisible = (node) => {
              if (!node || !(node instanceof Element)) return false;
              const st = window.getComputedStyle(node);
              if (!st || st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
              const r = node.getBoundingClientRect();
              return r.width > 0 && r.height > 0;
            };
            const roots = [
              el.closest('[id^="question_"]'), el.closest('.question'), el.closest('fieldset'), el.closest('form'),
            ].filter(Boolean);
            const selectors = ['.instruction-text', '.instruction', '[class*="instruction"]'];
            for (const root of roots) {
              for (const sel of selectors) {
                for (const node of root.querySelectorAll(sel)) {
                  if (!isVisible(node)) continue;
                  const t = norm(node.textContent || node.innerText || '');
                  if (t.length >= 8) return t;
                }
              }
            }
            return '';
        }""", _handle(el)))
    except Exception:
        instruction = ""

    if instruction and instruction not in parts:
        parts.append(instruction)

    return _norm(" ".join(parts))


def _choice_option_has_inline_open_text(driver, choice_el) -> bool:
    """
    Détecte si une option radio/checkbox embarque un champ texte inline visible
    (cas "Autre - préciser"), auquel cas l'option ne doit pas être proposée
    comme choix fermé.
    """
    try:
        return bool(_pw_page(driver).evaluate(
            r"""(el) => {
            if (!el) return false;
            const isVisible = (node) => {
              if (!node || !(node instanceof Element)) return false;
              const st = window.getComputedStyle(node);
              if (!st) return false;
              if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
              const r = node.getBoundingClientRect();
              return r.width > 0 && r.height > 0;
            };
            const roots = [];
            const directLabel = el.closest('label');
            if (directLabel) roots.push(directLabel);
            const id = el.id || '';
            if (id) {
              try {
                const esc = (window.CSS && CSS.escape) ? CSS.escape(id) : id.replace(/([ #;?%&,.+*~\':\"!^$\[\]()=>|\/@])/g, '\\$1');
                const linked = document.querySelector(`label[for="${esc}"]`);
                if (linked) roots.push(linked);
              } catch (_) {}
            }
            const wrapper = el.closest('.element, .option, li, .choice, .cell-sub-wrapper, .form-check, [role="radio"], [role="checkbox"]');
            if (wrapper) roots.push(wrapper);
            const seen = new Set();
            for (const root of roots) {
              if (!root) continue;
              if (seen.has(root)) continue;
              seen.add(root);
              const fields = root.querySelectorAll('input[type="text"], textarea');
              for (const f of fields) {
                if (f === el) continue;
                if (isVisible(f)) return true;
              }
            }
            return false;
        }""", _handle(choice_el)))
    except Exception:
        return False


def _get_choice_trailing_open_info(driver, choice_el) -> dict | None:
    """
    Détecte si une option radio/checkbox embarque un champ texte via .trailing-open /
    .openend-inline (pattern YouGov "Je préfère me décrire"). Retourne {"name":..., "id":...}
    du champ texte si trouvé et visible, sinon None.
    Permet d'inclure l'option dans le groupe tout en identifiant le bloc text autonome
    à supprimer.
    """
    try:
        result = _pw_page(driver).evaluate(
            r"""(el) => {
            if (!el) return null;
            const label = el.closest('label')
                || (el.id ? document.querySelector('label[for="' + el.id + '"]') : null);
            if (!label) return null;
            const inp = label.querySelector(
                '.trailing-open input[type="text"], .openend-inline input[type="text"]'
            );
            if (!inp) return null;
            const st = window.getComputedStyle(inp);
            if (!st || st.display === 'none' || st.visibility === 'hidden') return null;
            const r = inp.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) return null;
            return { name: inp.name || '', id: inp.id || '' };
        }""", _handle(choice_el))
        if result and (result.get("name") or result.get("id")):
            return result
        return None
    except Exception:
        return None


def _is_modal_related_control(driver, el) -> bool:
    """
    Ignore les contrôles UI liés à des modals/dialogs (confirmation/info)
    pour éviter de les interpréter comme des questions.
    """
    try:
        in_modal = _pw_page(driver).evaluate(
            """(el) => {
            if (!el || !el.closest) return false;
            return !!el.closest('.modal, [role="dialog"], [role="alertdialog"], [aria-modal="true"], [id*="modal" i], [id*="dialog" i], [id*="overlay" i], [id*="refuse" i], [id*="confirm" i]');
        }""", _handle(el))
        if bool(in_modal):
            return True
    except Exception:
        pass

    try:
        ref_candidates = [
            el.get_attribute("href") or "",
            el.get_attribute("aria-controls") or "",
            el.get_attribute("data-target") or "",
            el.get_attribute("data-bs-target") or "",
            el.get_attribute("id") or "",
            el.get_attribute("name") or "",
        ]
    except Exception:
        ref_candidates = []

    joined = _norm_lc(" ".join(v for v in ref_candidates if v))
    return any(k in joined for k in ("modal", "dialog", "overlay", "refuse", "confirm"))


def _find_bootstrap_selectpicker_question_label(el) -> str:
    """
    Fallback DOM-first ciblé pour Bootstrap Select:
    - <select class="selectpicker"> potentiellement masqué
    - question portée par un <span class="z-label"> proche dans le même bloc
    """
    try:
        cls = _norm_lc(el.get_attribute("class") or "")
        if "selectpicker" not in cls:
            return ""

        wrappers = el.find_elements(
            "xpath",
            "ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' bootstrap-select ')][1]",
        )
        if not wrappers:
            return ""

        labels = el.find_elements(
            "xpath",
            (
                "ancestor::*[.//span[contains(concat(' ', normalize-space(@class), ' '), ' z-label ')]]"
                "[1]//span[contains(concat(' ', normalize-space(@class), ' '), ' z-label ')]"
            ),
        )
        for lb in labels or []:
            txt = _norm(lb.text or lb.get_attribute("innerText") or "")
            if txt:
                return txt
    except Exception:
        return ""

    return ""


def _extract_nfield_dragndrop_blocks(driver, frame_chain=None) -> List[Dict[str, Any]]:
    """
    Nfield dragndrop (metaType=dragndrop): React DnD skin over a hidden mrQuestionTable.
    Guard: div._dragndrop + table.mrQuestionTable both present in DOM.
    Returns a single matrix block; dispatch via hidden radio JS click per (row, col).
    """
    try:
        if not driver.find_elements("css selector", "div._dragndrop"):
            return []
    except Exception:
        return []

    try:
        table = driver.find_element("css selector", "table.mrQuestionTable")
    except Exception:
        return []

    col_headers: dict = {}
    try:
        for idx, cell in enumerate(table.find_elements("css selector", "td.mrGridQuestionText")):
            txt = re.sub(r"\s+", " ", (cell.get_attribute("innerText") or cell.text or "")).strip()
            if txt:
                col_headers[idx] = txt
    except Exception:
        pass

    if not col_headers:
        return []

    options_list = [col_headers[i] for i in sorted(col_headers)]

    question_label = ""
    qname = ""
    try:
        fieldset = driver.find_element("css selector", "fieldset[questionname]")
        qname = (fieldset.get_attribute("questionname") or "").strip()
        legend = fieldset.find_element("css selector", "legend.mrQuestionText")
        question_label = re.sub(r"\s+", " ", (legend.get_attribute("innerText") or legend.text or "")).strip()
    except Exception:
        pass

    # Exclusive columns: column names appearing inside « » in the legend (capacity=1 per Nfield DnD).
    exclusive_columns: List[str] = []
    if question_label:
        for _m in re.findall(r'«\s*([^»]+?)\s*»', question_label):
            _m = _m.strip()
            if _m in options_list:
                exclusive_columns.append(_m)

    matrix_rows: List[str] = []
    nested_xpath_map: Dict[str, Any] = {}  # {row_label: {col_label: xpath}}

    try:
        rows = table.find_elements("css selector", "tr")
    except Exception:
        return []

    for tr in rows:
        try:
            cat_tds = tr.find_elements("css selector", "td.mrGridCategoryText")
            if not cat_tds:
                continue
            row_label = re.sub(r"\s+", " ", (cat_tds[0].get_attribute("innerText") or cat_tds[0].text or "")).strip()
            if not row_label:
                continue

            radios = tr.find_elements("css selector", "input[type='radio']")
            if not radios:
                continue

            radio_name = (radios[0].get_attribute("name") or "").strip()

            option_xpath_map = {}
            for radio in radios:
                try:
                    colid_str = (radio.get_attribute("colid") or "").strip()
                    if not colid_str.isdigit():
                        continue
                    colid = int(colid_str)
                    col_label = col_headers.get(colid, "")
                    if not col_label:
                        continue
                    r_id = (radio.get_attribute("id") or "").strip()
                    xp = (f"//input[@id='{r_id}']" if r_id
                          else f"//input[@type='radio'][@name='{radio_name}'][@colid='{colid}']")
                    option_xpath_map[col_label] = xp
                except Exception:
                    continue

            if not option_xpath_map:
                continue

            matrix_rows.append(row_label)
            nested_xpath_map[row_label] = option_xpath_map
        except Exception:
            continue

    if not matrix_rows:
        return []

    if not qname:
        qname = options_list[0][:30] if options_list else "dnd"

    target_id = make_target_id("matrix", f"dragndrop:{qname}", question_label[:80] or qname)
    register_target(target_id, {
        "kind": "group",
        "itype": "matrix",
        "option_xpath_map": nested_xpath_map,
        "nfield_dragndrop_hidden": True,
    })

    ctx_block: Dict[str, Any] = {"matrix_rows": matrix_rows}
    if exclusive_columns:
        ctx_block["exclusive_columns"] = exclusive_columns

    return [{
        "target_id": target_id,
        "itype": "matrix",
        "label": question_label or qname,
        "options": options_list,
        "context": ctx_block,
        "min_select": len(matrix_rows),
        "max_select": len(matrix_rows),
    }]


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
    table_matrix_row_names: Set[str] = set()
    table_matrix_sge_prefixes: Set[str] = set()
    clear_registry()

    # --- 0-pre) Nfield dragndrop (metaType=dragndrop, div._dragndrop + hidden mrQuestionTable) ---
    try:
        dnd_blocks = _extract_nfield_dragndrop_blocks(driver, frame_chain)
        if dnd_blocks:
            return dnd_blocks
    except Exception:
        pass

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
        log_debug("[WALR_CS]", f"bloc retourné: {walr_cs_block is not None}")
        if walr_cs_block:
            log_debug("[WALR_CS]", f"SUCCESS - returning block with {len(walr_cs_block.get('options', []))} options")
            return [walr_cs_block]
    except Exception as e:
        log_debug("[WALR_CS]", f"exception={type(e).__name__}: {e}")
        if is_debug():
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

    # --- 0c-ter) Askia StatementList (widget propriétaire AskiaExt / adc-statementList) ---
    # Gate DOM strict : div[class*='adc-statementList'] + div.responseItem[data-value] + span.statement_text[data-id]
    # Produit 1 bloc checkbox pour le statement actuellement visible.
    try:
        askia_sl_blocks = _extract_askia_statement_list_blocks(driver, frame_chain)
        if askia_sl_blocks:
            return askia_sl_blocks
    except Exception:
        pass

    # --- 0c-quater) Askia myresponse* : question radio/NPS sur td cliquables (input masqué)
    # Gate DOM strict : form[name="FormAskia"] + td[class*="myresponse"] input[type="radio"] >= 2
    try:
        askia_mr_blocks = _extract_askia_myresponse_radio_blocks(driver, frame_chain)
        if askia_mr_blocks:
            return askia_mr_blocks
    except Exception:
        pass

    # --- 0c-quinquies-bis) Askia myresponse* : question checkbox (name chk<QID> <optId>)
    # Gate DOM strict : form[name="FormAskia"] + td[class*="myresponse"] input[type="checkbox"][name^="chk"] >= 2
    try:
        askia_cb_blocks = _extract_askia_myresponse_checkbox_blocks(driver, frame_chain)
        if askia_cb_blocks:
            return askia_cb_blocks
    except Exception:
        pass

    # --- 0c-quinquies-ter) Askia ResponsiveTable checkbox matrix : 1 bloc par ligne
    # Gate DOM strict : form[name="FormAskia"] + div.adc-responsiveTable + tr.askiarow[data-id]
    try:
        askia_rt_blocks = _extract_askia_responsive_table_checkbox_rows(driver, frame_chain)
        if askia_rt_blocks:
            return askia_rt_blocks
    except Exception:
        pass

    # --- 0c-quinquies) Askia ranking isotope : classement par clic sur div.statement[data-value]
    # Gate DOM strict : form[name="FormAskia"] + div[class*="adc-ranking-isotope"]
    #                   + div.statement[data-value] + span.statement_text >= 2
    try:
        askia_rank_blocks = _extract_askia_ranking_isotope_blocks(driver, frame_chain)
        if askia_rank_blocks:
            return askia_rank_blocks
    except Exception:
        pass

    # --- 0c-sexies) Askia adc-slider (noUiSlider) : sliders discrets sur input hidden
    # Gate DOM strict : form[name="FormAskia"] + div.adc-slider + input[type=hidden][name] + div.noUi-handle
    try:
        askia_slider_blocks = _extract_askia_adc_slider_blocks(driver, frame_chain)
        if askia_slider_blocks:
            return askia_slider_blocks
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

    # --- 0d-1bis) CMIX GRID : matrices table.cm-grid-response-set ---
    # Objectif: extraire data-type=GRID (lignes x colonnes) avant les extracteurs génériques radio.
    try:
        cmix_grid_blocks = _extract_cmix_grid_question_blocks(driver, frame_chain)
        if cmix_grid_blocks:
            return cmix_grid_blocks
    except Exception:
        pass

    # --- 0d-1ter) Matrices HTML génériques (table + radios groupés par ligne) ---
    # Objectif: éviter l'aplatissement en bloc checkbox sur certaines grilles provider-variants.
    try:
        decipher_rank_blocks = _extract_decipher_clickable_ranking_blocks(driver, frame_chain)
        if decipher_rank_blocks:
            return decipher_rank_blocks
    except Exception:
        pass

    # --- 0d-1quater) Matrices HTML génériques (table + radios groupés par ligne) ---
    # Objectif: éviter l'aplatissement en bloc checkbox sur certaines grilles provider-variants.
    try:
        table_matrix_blocks = _extract_table_matrix_radio_rows(driver, frame_chain)
        if table_matrix_blocks:
            question_blocks.extend(table_matrix_blocks)
            for _matrix_block in table_matrix_blocks:
                if not isinstance(_matrix_block, dict):
                    continue
                _ctx = (_matrix_block.get("context") or {}) if isinstance(_matrix_block.get("context"), dict) else {}
                if _ctx.get("table_matrix_radio") is not True:
                    continue
                _group_key = _norm_lc(_ctx.get("group_key") or "")
                if _group_key.startswith("table_matrix_radio:name:"):
                    _row_name = _group_key.split("table_matrix_radio:name:", 1)[1].strip()
                    if _row_name:
                        table_matrix_row_names.add(_row_name)
                elif _group_key.startswith("table_matrix_sge:name:"):
                    _sge_prefix = _group_key.split("table_matrix_sge:name:", 1)[1].strip()
                    if _sge_prefix:
                        table_matrix_sge_prefixes.add(_sge_prefix)
    except Exception:
        pass

    # --- 0d-1quater-bis) IntelliSurvey matrix (table.i-question-table.i-dynamic) ---
    # Objectif: extraire les matrices IntelliSurvey (rows x columns) quand le markup est custom.
    try:
        intellisurvey_matrix_blocks = _extract_intellisurvey_table_matrix_blocks(driver, frame_chain)
        if intellisurvey_matrix_blocks:
            return intellisurvey_matrix_blocks
    except Exception:
        pass

    # --- 0d-1quater-ter) encuesta.com matrix (Vuetify ee__matrix--*) ---
    # Objectif: extraire les matrices encuesta avant le fallback générique.
    try:
        encuesta_matrix_blocks = _extract_encuesta_matrix_blocks(driver, frame_chain)
        if encuesta_matrix_blocks:
            return encuesta_matrix_blocks
    except Exception:
        pass

    # --- 0d-1quinter) Decipher table text rows (i-question-table) ---
    # Objectif: extraire 1 bloc text par ligne non-readonly dans les tables Decipher.
    try:
        decipher_table_text_blocks = _extract_decipher_table_text_rows_blocks(driver, frame_chain)
        if decipher_table_text_blocks:
            return decipher_table_text_blocks
    except Exception:
        pass

    # --- 0d-1sexies-a) Decipher/FocusVision grid single-col text rows (table.grid.grid-table-mode) ---
    # Objectif: extraire 1 bloc text par ligne dans les grilles single-col FocusVision/Decipher.
    try:
        decipher_grid_sc_blocks = _extract_decipher_grid_single_col_text_rows(driver, frame_chain)
        if decipher_grid_sc_blocks:
            return decipher_grid_sc_blocks
    except Exception:
        pass

    # --- 0d-1sexies) YouGov grid text (fieldset.question-grid-text) ---
    # Objectif: extraire 1 bloc text par ligne (input texte) au lieu d'un single aplati.
    try:
        yougov_grid_text_blocks = _extract_yougov_grid_text_question_blocks(driver, frame_chain)
        if yougov_grid_text_blocks:
            return yougov_grid_text_blocks
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

    # --- 0d-4) Forsta/Confirmit slider-grid (cf-question--slider-grid) ---
    # Objectif: extraire les lignes slider custom (role=slider) en blocs radio DOM-first.
    try:
        confirmit_slider_blocks = _extract_confirmit_slider_grid_blocks(driver, frame_chain)
        if confirmit_slider_blocks:
            return confirmit_slider_blocks
    except Exception:
        pass

    # --- 0d-4bis) Forsta/Confirmit CF desktop radio-grid (table.cf-table-layout + div.cf-radio) ---
    # Objectif: extraire les lignes radio custom Confirmit CF (pas d'input[type=radio]).
    # Gate DOM: table.cf-table-layout + div.cf-radio[role='radio'] dans tbody.
    try:
        confirmit_cf_grid_blocks = _extract_confirmit_cf_desktop_grid_blocks(driver, frame_chain)
        if confirmit_cf_grid_blocks:
            return confirmit_cf_grid_blocks
    except Exception:
        pass

    # --- 0d-4bis-b) Forsta/Confirmit CF bipolar button rating scale (cf-button-answer__button) ---
    # Objectif: extraire les questions bipolaires avec boutons numérotés (sans cf-radio ni thead).
    # Gate DOM: table.cf-table-layout + tbody div.cf-button-answer__button[role='radio'].
    try:
        confirmit_bipolar_blocks = _extract_confirmit_cf_bipolar_button_grid_blocks(driver, frame_chain)
        if confirmit_bipolar_blocks:
            return confirmit_bipolar_blocks
    except Exception:
        pass

    # --- 0d-4ter) Forsta/Confirmit Horizontal Rating Scale Single (div.cf-hrs-single) ---
    # Objectif: extraire les radiogroups horizontaux non-tabulaires Confirmit CF.
    # Gate DOM: div.cf-hrs-single[role='radiogroup'] + div.cf-horizontal-rating-item[role='radio'].
    try:
        confirmit_hrs_single_blocks = _extract_confirmit_cf_hrs_single_blocks(driver, frame_chain)
        if confirmit_hrs_single_blocks:
            return confirmit_hrs_single_blocks
    except Exception:
        pass

    # --- 0d-4quater) GroupCaliber/IPSOS Bootstrap rating rows (data-question_type="5") ---
    # Gate DOM: h6[data-question_type="5"] + div.row.bg-light[div.col-md-3 b + radios name=\d+_\d+].
    try:
        caliber_blocks = _extract_groupcaliber_rating_row_blocks(driver, frame_chain)
        if caliber_blocks:
            return caliber_blocks
    except Exception:
        pass

    # --- 0d-4quinquies) Forsta/Confirmit CF carousel (div.cf-carousel + cf-answer-button) ---
    # Gate DOM: div.cf-carousel + div.cf-carousel__content-item + div.cf-answer-button.
    try:
        confirmit_carousel_blocks = _extract_confirmit_cf_carousel_blocks(driver, frame_chain)
        if confirmit_carousel_blocks:
            return confirmit_carousel_blocks
    except Exception:
        pass

    # --- 0d-4septies/octies/nonies) Forsta/Confirmit CF : accumulation multi-types ---
    # Une même page peut contenir simultanément plusieurs types CF (single + numeric-list +
    # open-list). On collecte les blocs des trois extracteurs avant de retourner, sans return
    # intermédiaire entre eux.
    # Gates DOM : chaque extracteur a sa propre gate stricte, les types absents renvoient [].
    try:
        ranking_blocks = _extract_confirmit_cf_ranking_blocks(driver, frame_chain)
        if ranking_blocks:
            return ranking_blocks
    except Exception:
        pass

    cf_combined: list[dict] = []
    try:
        cf_combined.extend(_extract_confirmit_cf_single_choice_blocks(driver, frame_chain))
    except Exception:
        pass
    try:
        cf_combined.extend(_extract_confirmit_cf_single_image_choice_blocks(driver, frame_chain))
    except Exception:
        pass
    try:
        cf_combined.extend(_extract_confirmit_cf_multi_choice_blocks(driver, frame_chain))
    except Exception:
        pass
    try:
        cf_combined.extend(_extract_confirmit_cf_numeric_list_blocks(driver, frame_chain))
    except Exception:
        pass
    try:
        cf_combined.extend(_extract_confirmit_cf_open_list_blocks(driver, frame_chain))
    except Exception:
        pass
    if cf_combined:
        return cf_combined

    # --- 0d-4sexies) SSI/Confirmit native radio grid (div.question.grid > table.inner_table) ---
    # Gate DOM: div.question.grid + tr.column_header_row td[role="columnheader"] + tr[role="radiogroup"].
    try:
        ssi_native_grid_blocks = _extract_ssi_confirmit_native_grid_blocks(driver, frame_chain)
        if ssi_native_grid_blocks:
            return ssi_native_grid_blocks
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

    # --- 0h-bis) Angular custom data-testid radios (sans input natif) ---
    # Objectif: extraire les blocs radio pilotés par wrappers + labels data-testid.
    try:
        custom_testid_blocks = _extract_custom_testid_single_select_radio_blocks(driver, frame_chain)
        if custom_testid_blocks:
            return custom_testid_blocks
    except Exception:
        pass

    # --- 0h-bis-2) Runtime answers rows + radio wrapper (sans input radio natif) ---
    # Objectif: extraire les groupes radio custom où les options sont des rows cliquables.
    # Combiné avec les dropdowns/date/texte du même runtime (display_drop_down + MultiValueSelectWrapper).
    try:
        runtime_answerrow_blocks = _extract_runtime_answerrow_radio_blocks(driver, frame_chain)
        runtime_dd_blocks = _extract_runtime_dropdown_blocks(driver, frame_chain)
        runtime_combined = runtime_answerrow_blocks + runtime_dd_blocks
        if runtime_combined:
            return runtime_combined
    except Exception as e:
        if is_debug():
            log_debug("[DOM_CONTEXT_DEBUG]", f"runtime_extractor_exception={type(e).__name__}: {e}")

    # --- 0h-bis-2a-0) Toluna ranking séquentiel par clic (display_clicking_order) ---
    # Objectif: extraire ranking_question sans input natif (options = RankingItemWrapper).
    try:
        toluna_ranking_blocks = _extract_toluna_runtime_ranking_blocks(driver, frame_chain)
        if toluna_ranking_blocks:
            return toluna_ranking_blocks
    except Exception as e:
        if is_debug():
            log_debug("[DOM_CONTEXT_DEBUG]", f"toluna_ranking_extractor_exception={type(e).__name__}: {e}")

    # --- 0h-bis-2a) Kantar rowpicker (cartes cliquables sans input radio natif visible) ---
    # Objectif: extraire les options dans `div._rowpicker[data-test='main-contain']`.
    try:
        kantar_rowpicker_blocks = _extract_kantar_rowpicker_radio_blocks(driver, frame_chain)
        if kantar_rowpicker_blocks:
            return kantar_rowpicker_blocks
    except Exception:
        pass

    # --- 0h-bis-2a-bis) Kantar rowrank (classement ordinal, metaType=rowrank, mrIWeb) ---
    # Objectif: extraire les cartes visuelles ._rowrank + guard Qslice inputs.
    try:
        kantar_rowrank_blocks = _extract_kantar_rowrank_blocks(driver, frame_chain)
        if kantar_rowrank_blocks:
            return kantar_rowrank_blocks
    except Exception:
        pass

    # --- 0h-bis-2b) Listes label.radio sans input natif (Angular custom) ---
    # Objectif: extraire les groupes single-select rendus via labels cliquables.
    try:
        label_radio_blocks = _extract_label_radio_list_blocks(driver, frame_chain)
        if label_radio_blocks:
            return label_radio_blocks
    except Exception:
        pass

    # --- 0h-bis-3) Qualtrics ChoiceStructure radios (QuestionOuter + QR~) ---
    # Objectif: extraire les radios Qualtrics non couvertes par le générique.
    # On accumule (pas de return immédiat) pour pouvoir capturer d'autres types de
    # questions Qualtrics présentes sur la même page (ex: dropdown DL).
    _qualtrics_page = False
    try:
        qualtrics_choice_blocks = _extract_qualtrics_choice_structure_radio_blocks(driver, frame_chain)
        if qualtrics_choice_blocks:
            if table_matrix_row_names:
                _filtered_qc = []
                for _qb in qualtrics_choice_blocks:
                    _qctx = _qb.get("context") if isinstance(_qb.get("context"), dict) else {}
                    _qgk = _norm_lc(_qctx.get("group_key") or "")
                    if _qgk.startswith("radio:name:") and _qgk[len("radio:name:"):] in table_matrix_row_names:
                        continue
                    _filtered_qc.append(_qb)
                qualtrics_choice_blocks = _filtered_qc
            if qualtrics_choice_blocks:
                question_blocks.extend(qualtrics_choice_blocks)
                _qualtrics_page = True
    except Exception:
        pass

    # --- 0h-bis-3b) Qualtrics ChoiceStructure checkboxes (MAVR/MAHR) ---
    # Objectif: extraire les multi-sélections Qualtrics non couvertes par le générique.
    try:
        qualtrics_choice_checkbox_blocks = _extract_qualtrics_choice_structure_checkbox_blocks(driver, frame_chain)
        if qualtrics_choice_checkbox_blocks:
            question_blocks.extend(qualtrics_choice_checkbox_blocks)
            _qualtrics_page = True
    except Exception:
        pass

    # --- 0h-bis-3c) Qualtrics DL dropdown (1 <select> unique par QuestionOuter.DL) ---
    # Objectif: extraire les questions dropdown Qualtrics layout DL non couvertes
    # par le générique singles (qui n'est atteint que si aucun extracteur radio n'a rien trouvé).
    try:
        qualtrics_dl_blocks = _extract_qualtrics_dl_select_blocks(driver, frame_chain)
        if qualtrics_dl_blocks:
            question_blocks.extend(qualtrics_dl_blocks)
            _qualtrics_page = True
    except Exception:
        pass

    # --- 0h-bis-3d) Qualtrics texte libre (input[type=TEXT] dans QuestionOuter.SL / type TE) ---
    try:
        qualtrics_sl_text_blocks = _extract_qualtrics_sl_text_blocks(driver, frame_chain)
        if qualtrics_sl_text_blocks:
            question_blocks.extend(qualtrics_sl_text_blocks)
            _qualtrics_page = True
    except Exception:
        pass

    # --- 0h-bis-3e) Qualtrics texte libre FORM multi-cases (div.Inner.FORM + ≥2 inputs) ---
    try:
        qualtrics_form_multi_text_blocks = _extract_qualtrics_form_multi_text_blocks(driver, frame_chain)
        if qualtrics_form_multi_text_blocks:
            question_blocks.extend(qualtrics_form_multi_text_blocks)
            _qualtrics_page = True
    except Exception:
        pass

    # --- 0h-bis-3f) Qualtrics texte libre Matrix-TE multi-cases (div.QuestionOuter.Matrix.mf + div.Inner.TE) ---
    # Couvre les pages de type "Matrix Fill Text" où div.Inner.TE contient une table.ChoiceStructure
    # avec N tr.ChoiceRow, chacun portant un input[type='text'][name^='QR~'].
    # Distinct du layout FORM (div.Inner.FORM) → extracteur séparé, même format de sortie.
    try:
        qualtrics_te_matrix_multi_text_blocks = _extract_qualtrics_te_matrix_multi_text_blocks(driver, frame_chain)
        if qualtrics_te_matrix_multi_text_blocks:
            question_blocks.extend(qualtrics_te_matrix_multi_text_blocks)
            _qualtrics_page = True
    except Exception:
        pass

    # --- 0h-bis-3g) Qualtrics Matrix.mf BankedSA 1-ligne (div.customChoice + 1 ChoiceRow same name) ---
    # Couvre le cas CS_BankedSA single-row non couvert par 0h-bis-3 (garde multi-name exclut 1 seule ligne).
    try:
        qualtrics_bankedsa_blocks = _extract_qualtrics_bankedsa_single_row_radio_blocks(driver, frame_chain)
        if qualtrics_bankedsa_blocks:
            question_blocks.extend(qualtrics_bankedsa_blocks)
            _qualtrics_page = True
    except Exception:
        pass

    if _qualtrics_page and question_blocks:
        return question_blocks

    # --- 0h-ter-0) QuestMindshare chatbot (div[data-testid^="option-"] sans input natif) ---
    # Gate strict : div[data-testid^="option-"][tabindex="0"] présent.
    try:
        qm_blocks = _extract_questmindshare_chatbot_blocks(driver, frame_chain)
        if qm_blocks:
            return qm_blocks
    except Exception:
        pass

    # --- 0h-ter) Angular custom data-testid checkboxes (sans input natif) ---
    # Objectif: extraire les blocs checkbox pilotés par wrappers + labels data-testid.
    try:
        custom_testid_checkbox_blocks = _extract_custom_testid_multi_select_checkbox_blocks(driver, frame_chain)
        if custom_testid_checkbox_blocks:
            return custom_testid_checkbox_blocks
    except Exception:
        pass

    # --- 0h-quinquies-rps) rps-select Angular custom (Toluna/SurveyRouter screener) ---
    # Gate strict : rps-select[@data-selector] + div.selection + div.option-item avec texte.
    # Précède consent_checkbox : le select Sexe doit prendre le dessus sur le checkbox ng-hide.
    try:
        rps_select_blocks = _extract_rps_select_blocks(driver, frame_chain)
        if rps_select_blocks:
            return rps_select_blocks
    except Exception:
        pass

    # --- 0h-quater) Consent checkbox unique + CTA accept/start ---
    # Objectif: couvrir les écrans de consentement qui ne ressortent pas via le générique.
    try:
        consent_checkbox_blocks = _extract_single_consent_checkbox_block(driver, frame_chain)
        if consent_checkbox_blocks:
            return consent_checkbox_blocks
    except Exception:
        pass

    # --- 0h-quinquies) Consent modal radio + bouton confirmer ---
    # Objectif: couvrir les modals RGPD avec radios masquées + labels custom.
    try:
        consent_modal_blocks = _extract_consent_modal_radio_block(driver, frame_chain)
        if consent_modal_blocks:
            return consent_modal_blocks
    except Exception:
        pass

    # --- 0h-sexies-a) Confirmit/Wix grille checkbox multi-colonnes (layout /wix/2/) ---
    # Objectif: couvrir les grilles confirmit-grid (table.confirmit-grid) avec checkboxes
    # à top:-9000px. 1 bloc par ligne-facteur (rowIdx≠98), options = colonnes détaillants.
    try:
        wix_cb_grid_blocks = _extract_confirmit_wix_checkbox_grid_blocks(driver, frame_chain)
        if wix_cb_grid_blocks:
            return wix_cb_grid_blocks
    except Exception:
        pass

    # --- 0h-sexies) Confirmit/Wix natif fieldset radio (layout /wix/2/) ---
    # Objectif: couvrir les pages Toluna/Confirmit avec fieldset[id^="fieldset_"] +
    # confirmit-table où les inputs radio sont à top:-9000px (non interactables).
    try:
        wix_fieldset_blocks = _extract_confirmit_wix_fieldset_radio_block(driver, frame_chain)
        if wix_fieldset_blocks:
            return wix_fieldset_blocks
    except Exception:
        pass

    # --- 0h-septies) Confirmit/Wix natif ranked-order-click (layout /wix/2/) ---
    # Objectif: couvrir les questions de classement séquentiel (RankedOrderClick) où
    # les inputs sont des checkbox masqués et la td.confirmit-rankedorderclick est la cible de clic.
    # Gate strict : fieldset.confirmit-rankedorderclick-default + td.confirmit-rankedorderclick.
    try:
        wix_rank_blocks = _extract_confirmit_wix_rankedorderclick_block(driver, frame_chain)
        if wix_rank_blocks:
            return wix_rank_blocks
    except Exception:
        pass

    # --- 0i) ps-select-dropdown (ng-bootstrap) month/year ---
    # Objectif: couvrir les dropdowns custom avec trigger ngbdropdowntoggle.
    try:
        ps_select_dropdown_blocks = _extract_ps_select_dropdown_blocks(driver, frame_chain)
        if ps_select_dropdown_blocks:
            return ps_select_dropdown_blocks
    except Exception:
        pass

    # --- 0i) PureSpectrum date picker (dropdown desktop) ---
    # Objectif: extraire les blocs date `month/year` sur ps-select-dropdown custom.
    try:
        ps_date_dropdown_blocks = _extract_purespectrum_date_dropdown_blocks(driver, frame_chain)
        if ps_date_dropdown_blocks:
            return ps_date_dropdown_blocks
    except Exception:
        pass

    # --- 0i-bis) PureSpectrum mobile date picker (ps-select-scroll) ---
    # Objectif: extraire les blocs date quand aucun input/select natif n'est présent.
    try:
        ps_date_blocks = _extract_purespectrum_mobile_date_blocks(driver, frame_chain)
        if ps_date_blocks:
            return ps_date_blocks
    except Exception:
        pass

    # --- 0i-bis-0) GfK mrIWeb accordéon Angular (div.acc_ct / div.acc-element) ---
    # Objectif: extraire les sous-questions accordion GfK avant les extracteurs génériques.
    try:
        gfk_accordion_blocks = _extract_gfk_accordion_radio_rows(driver, frame_chain)
        if gfk_accordion_blocks:
            return gfk_accordion_blocks
    except Exception:
        pass

    # --- 0i-bis) Matrice radio en sections repliées (header/content) ---
    # Objectif: isoler chaque ligne d'accordéon en bloc radio indépendant.
    try:
        section_matrix_blocks = _extract_collapsed_section_radio_rows(driver, frame_chain)
        if section_matrix_blocks:
            return section_matrix_blocks
    except Exception:
        pass

    # --- 0i-ter) jQuery Mobile LRW: lignes checkbox dans accordéon collapsible ---
    # Objectif: isoler chaque section de checkbox et ignorer les boutons toggle d'accordéon.
    try:
        jqm_collapsible_checkbox_blocks = _extract_jqm_lrw_collapsible_checkbox_rows(driver, frame_chain)
        if jqm_collapsible_checkbox_blocks:
            return jqm_collapsible_checkbox_blocks
    except Exception:
        pass

    # --- 0i-ter) jQuery Mobile LRW: lignes radio dans accordéon collapsible ---
    # Objectif: éviter la confusion entre boutons toggle (heading) et options radio.
    try:
        jqm_collapsible_blocks = _extract_jqm_lrw_collapsible_radio_rows(driver, frame_chain)
        if jqm_collapsible_blocks:
            return jqm_collapsible_blocks
    except Exception:
        pass

    # --- 0j) Savanta JQM carousel (fieldset.carousel + slick-slider) ---
    # Objectif: extraire l'item courant du carousel + les options d'un fieldset.carousel-buttons.
    # Doit s'exécuter avant le button_group générique pour éviter la mauvaise question.
    try:
        savanta_carousel_blocks = _extract_savanta_jqm_carousel_block(driver, frame_chain)
        if savanta_carousel_blocks:
            return savanta_carousel_blocks
    except Exception:
        pass

    # --- 0i-quater) Boutons de choix custom (button.choice avec id, sans input natif) ---
    # Objectif: extraire un bloc radio depuis des options rendues en boutons purs.
    try:
        button_choice_blocks = _extract_button_choice_radio_blocks(driver, frame_chain)
        if button_choice_blocks:
            return button_choice_blocks
    except Exception:
        pass

    # --- 0i-quinquies) DataDiggers icontrol (AngularJS Screener) ---
    # Guard DOM strict : div.main_survey_page + form[id^="attention_questions_"]
    try:
        dd_blocks = _extract_datadiggers_icontrol_radio_block(driver, frame_chain)
        if dd_blocks:
            return dd_blocks
    except Exception:
        pass

    # --- 0i-sexies) Prodege/Swagbucks prescreener (prsrvy.com) ---
    # Guard DOM strict : div.profilerContainer + p.profilerQuestionText
    try:
        prodege_blocks = _extract_prodege_prescreener_radio_block(driver, frame_chain)
        if prodege_blocks:
            return prodege_blocks
    except Exception:
        pass

    # --- 0i-septies) Decipher/NorstatSurveys ranksort dropdown (div.question.sq-ranksort) ---
    # Guard DOM strict : div.question.sq-ranksort
    # Cible : selects dans table.grid[display:none] — invisibles pour le pipeline générique.
    try:
        ranksort_blocks = _extract_decipher_ranksort_dropdown_blocks(driver, frame_chain)
        if ranksort_blocks:
            return ranksort_blocks
    except Exception:
        pass

    # --- 0i-septies-bis) Decipher sq-atmrating (rating par affirmations, boutons span) ---
    # Guard DOM strict : div.question.sq-atmrating + div.sq-atmrating-container + span.atmrating-btn
    # Les inputs type=text sont non-visibles → skippés par le pipeline générique.
    try:
        atmrating_blocks = _extract_decipher_atmrating_blocks(driver, frame_chain)
        if atmrating_blocks:
            return atmrating_blocks
    except Exception:
        pass

    # --- 0i-octies) ResearchNow/PureSpectrum auto-screener (surveymyopinion.researchnow.com) ---
    # Guard DOM strict : [ng-controller*="autoScreenerController"] +
    #                    div.parameter-rendered.single_select.tooBigForDropdown
    # Problème : inputs radio avec name différents (31, 33, 35…) → 7 groupes au lieu de 1.
    try:
        rn_blocks = _extract_researchnow_autoscreener_radio_blocks(driver, frame_chain)
        if rn_blocks:
            return rn_blocks
    except Exception:
        pass

    # Pattern spécifique
    try:
        choice_els = driver.find_elements(
            "css selector",
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

    def _is_sq_atm1d_widget_element(el) -> bool:
        """Skip elements inside a sq-atm1d widget (handled by _extract_focusvision_answers_list_groups)."""
        try:
            return bool(
                el.find_elements(
                    "xpath",
                    "ancestor::ul[contains(concat(' ', normalize-space(@class), ' '), ' sq-atm1d-buttons ')]",
                )
            )
        except Exception:
            return False

    def _is_focusvision_table_mode_matrix_cell(el) -> bool:
        """
        Détecte les cellules d'une grille Decipher/FocusVision `table-mode`
        déjà couvertes par l'extracteur dédié `_extract_focusvision_answers_list_groups`.

        Garde-fou DOM-first strict:
        - input radio/checkbox au format name `ans<d>.<d>.<d>`
        - dans une table `table.grid[data-settings*='table-mode']`
          (couvre à la fois grid-table-mode et grid-list-mode)
        """
        try:
            raw_name = (el.get_attribute("name") or "").strip()
        except Exception:
            return False

        if not re.fullmatch(r"ans\d+\.\d+\.\d+", raw_name):
            return False

        try:
            return bool(
                el.find_elements(
                    "xpath",
                    "ancestor::table[contains(concat(' ', normalize-space(@class), ' '), ' grid ') and "
                    "contains(@data-settings, 'table-mode')][1]",
                )
            )
        except Exception:
            return False

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
            return bool(_pw_page(driver).evaluate(
                r"""(el) => {
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
                const parentLabel = el.closest('label');
                if (isVisible(parentLabel)) return true;
                const id = el.id;
                if (id) {
                  const cssEscape = (window.CSS && CSS.escape) ? CSS.escape(id) : id.replace(/([ #;?%&,.+*~\':\"!^$\[\]()=>|\/@])/g, '\\$1');
                  const linked = document.querySelector(`label[for="${cssEscape}"]`);
                  if (isVisible(linked)) return true;
                  if (linked) {
                    const ls = window.getComputedStyle(linked);
                    if (ls && ls.display !== 'none' && ls.visibility !== 'hidden' && ls.opacity !== '0') {
                      const txt = (linked.innerText || linked.textContent || '').trim();
                      if (txt) return true;
                    }
                  }
                }
                const optionWrapper = el.closest('[role="radio"], [role="checkbox"], .form-check, .option, li, .choice, .answer_options, [class*="answer_options"], div.answer');
                if (isVisible(optionWrapper)) return true;
                return false;
            }""", _handle(el)))
        except Exception:
            return False

    for el in choice_els:
        try:
            itype = _detect_itype(el)
            if itype not in ("radio", "checkbox"):
                continue
            if _is_focusvision_table_mode_matrix_cell(el):
                continue
            if _is_sq_atm1d_widget_element(el):
                continue
            # Decipher/FocusVision: les options marquées `no-answer`
            # ("je ne souhaite pas répondre", "je ne sais pas", etc.)
            # sont des opt-outs et ne doivent pas devenir des blocs choix.
            try:
                classes = _norm_lc(el.get_attribute("class") or "")
                if " no-answer " in f" {classes} ":
                    continue
            except Exception:
                pass
            # Masqué
            try:
                if _looks_like_system_field(el):
                    continue
            except Exception:
                pass
            if not _choice_has_visible_proxy(el):
                # LimeSurvey (et variants proches) peut cacher l'input natif
                # tout en rendant la question/option cliquable via wrapper CSS.
                # On autorise alors l'option si son conteneur de question est
                # visiblement rendu, sans élargir cette règle aux autres structures.
                container = _nearest_question_container(el)
                container_cls = ""
                try:
                    container_cls = _norm_lc(container.get_attribute("class") or "") if container else ""
                except Exception:
                    container_cls = ""
                has_visible_radiolayout = False
                if container:
                    try:
                        has_visible_radiolayout = bool(_pw_page(driver).evaluate(
                            """(container) => {
                            if (!container) return false;
                            const rows = container.querySelectorAll('.radioLayout');
                            if (!rows || !rows.length) return false;
                            for (const row of Array.from(rows)) {
                              const st = window.getComputedStyle(row);
                              if (!st) continue;
                              if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') continue;
                              const r = row.getBoundingClientRect();
                              if (r.width > 0 && r.height > 0) return true;
                            }
                            return false;
                        }""", _handle(container)))
                    except Exception:
                        has_visible_radiolayout = False
                if not (
                    container
                    and (
                        (
                            "question-container" in container_cls
                            and _is_actionable_visible(container)
                        )
                        or (
                            "questioncontainer" in container_cls
                            and has_visible_radiolayout
                        )
                    )
                ):
                    continue
            raw_name_key = _group_key_for_choice(el, itype)
            if not raw_name_key:
                continue
            raw_name_key_lc = _norm_lc(raw_name_key)
            if raw_name_key_lc in table_matrix_row_names:
                continue
            if any(raw_name_key_lc.startswith(f"{prefix}-") for prefix in table_matrix_sge_prefixes):
                continue
            # Savanta JQM fieldset pattern: forcer itype="checkbox" pour que
            # les radio noneof soient fusionnés avec les checkboxes du même fieldset.
            if raw_name_key.startswith("fieldset:"):
                effective_itype = "checkbox"
                group_key = f"checkbox:fieldset:{raw_name_key[len('fieldset:'):]}"
            else:
                effective_itype = itype
                group_key = f"{itype}:name:{raw_name_key}"
            if is_debug() and raw_name_key.startswith("dom_container:"):
                try:
                    log_debug(
                        "[DOM_GROUPING] checkbox_container_pattern_detected "
                        f"key={raw_name_key}"
                    )
                except Exception:
                    pass
            groups.setdefault((effective_itype, group_key), []).append(el)
        except Exception:
            continue

    # --- Fusion des groupes "1 input = 1 name" partageant le même conteneur question
    # Signal DOM : plusieurs inputs distincts (name différent) rendus dans le même
    # fieldset/conteneur (ex: jQuery Mobile). Garde-fou : on ne fusionne que les
    # groupes à exactement 1 élément pour éviter de toucher les groupes légitimes.
    try:
        _cont_groups: dict[tuple, list] = {}  # (itype, container_uid) -> [(group_key, els)]
        for (itype, group_key), els in list(groups.items()):
            if len(els) != 1:
                continue
            try:
                container = _nearest_question_container(els[0])
                if container is None:
                    continue
                uid = _pw_page(driver).evaluate(
                    "(el) => { if (!el.__sq_uid__) { el.__sq_uid__ = ++((window.__sq_uid__ = window.__sq_uid__ || 0)); } return el.__sq_uid__; }",
                    _handle(container),
                )
                if uid is None:
                    continue
                _cont_groups.setdefault((itype, uid), []).append((group_key, els))
            except Exception:
                continue
        for (_itype, _uid), entries in _cont_groups.items():
            if len(entries) < 2:
                continue
            first_key, first_els = entries[0]
            for other_key, other_els in entries[1:]:
                first_els.extend(other_els)
                del groups[(_itype, other_key)]
            groups[(_itype, first_key)] = first_els
            if is_debug():
                try:
                    log_debug(
                        "[DOM_GROUPING]",
                        f"merge_single_name_inputs itype={_itype} uid={_uid} "
                        f"merged={len(entries)} groups into {first_key}",
                    )
                except Exception:
                    pass
    except Exception:
        pass

    seen_signatures = set()
    seen_multi_text_groups = set()

    group_reject_reasons: Dict[str, int] = {}
    created_group_count = 0

    for (itype, group_key), els in groups.items():
        try:
            # options = labels des inputs
            options: List[str] = []
            option_elements: List[Tuple[Any, str]] = []
            inline_openend_names: List[str] = []
            for e in els:
                lbl = _find_associated_label(driver, e)
                if not lbl:
                    continue
                trailing_info = _get_choice_trailing_open_info(driver, e)
                if trailing_info:
                    # Option avec champ texte inline (.trailing-open/.openend-inline) :
                    # on l'inclut dans le groupe et on mémorise le name/id pour pruning.
                    option_elements.append((e, lbl))
                    options.append(lbl)
                    nm = (trailing_info.get("name") or trailing_info.get("id") or "").strip()
                    if nm:
                        inline_openend_names.append(nm)
                elif not _choice_option_has_inline_open_text(driver, e):
                    option_elements.append((e, lbl))
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
                            "css selector",
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
            # Savanta JQM fieldset pattern: extraire la question depuis <legend>
            # pour éviter d'inclure les sous-titres de sections (Bien-être, etc.).
            if not question and group_key.startswith("checkbox:fieldset:"):
                try:
                    legends = els[0].find_elements(
                        "xpath",
                        "ancestor::fieldset[contains(@class,'question-wrapper')][1]//legend",
                    )
                    if legends:
                        q_txt = _norm(legends[0].text or "")
                        if q_txt and _is_question_text(q_txt):
                            question = q_txt
                except Exception:
                    pass
            if not question and raw_name_key.startswith("dom:"):
                # Groupes sans name natif (ex: Forsta/Confirmit ARIA widgets):
                # résoudre directement l'aria-labelledby du conteneur radiogroup
                # pour éviter que _nearest_question_container remonte trop haut
                # et concatène les textes de plusieurs questions sur la même page.
                try:
                    rg_nodes = els[0].find_elements(
                        "xpath",
                        "ancestor::*[@role='radiogroup' or @role='group'][1]"
                    )
                    if rg_nodes:
                        labelledby = (rg_nodes[0].get_attribute("aria-labelledby") or "").strip()
                        if labelledby:
                            texts = []
                            for ref_id in labelledby.split():
                                if not ref_id:
                                    continue
                                try:
                                    node = driver.find_element("id", ref_id)
                                    txt = _norm(node.text or node.get_attribute("innerText") or "")
                                    if txt and txt not in texts:
                                        texts.append(txt)
                                except Exception:
                                    pass
                            if texts:
                                question = _norm(" ".join(texts))
                except Exception:
                    pass
            # Pattern screener-style: div.answer > div.options.js-question-options
            # La question est dans div.question, frère précédent de div.answer.
            # Guard: présence d'un ancêtre div avec classe js-question-options
            # dont le parent immédiat est un div avec classe "answer".
            if not question:
                try:
                    q_nodes = els[0].find_elements(
                        "xpath",
                        "ancestor::div[contains(@class,'js-question-options')][1]"
                        "/parent::div[contains(@class,'answer')]"
                        "/preceding-sibling::*[contains(@class,'question')][1]"
                    )
                    if q_nodes:
                        q_txt = _norm(q_nodes[0].text or q_nodes[0].get_attribute("innerText") or "")
                        if q_txt and _is_question_text(q_txt):
                            question = q_txt
                            log_debug("[DOM_CONTEXT]", f"js_question_options_sibling resolved question={question[:60]!r}")
                except Exception:
                    pass

            if not question:
                # Fallback: extraction générique via conteneur
                container = _nearest_question_container(els[0])
                question = _extract_question_from_container(container, options) if container else ""

            # mrIWeb/GfK: inputs carry class "mrSingle" or "mrMultiple" but no ancestor
            # matches standard container selectors. span.mrQuestionText is a DOM sibling
            # of the choices container — query it directly, scoped to the same form.
            if not question:
                try:
                    _el_cls = _norm_lc(els[0].get_attribute("class") or "")
                    if "mrsingle" in _el_cls or "mrmultiple" in _el_cls:
                        scope_nodes = els[0].find_elements("xpath", "ancestor::form[1]")
                        scope = scope_nodes[0] if scope_nodes else None
                        q_spans = (
                            scope.find_elements("css selector", "span.mrQuestionText")
                            if scope else
                            driver.find_elements("css selector", "span.mrQuestionText")
                        )
                        opt_lc = {_norm_lc(o) for o in options if o}
                        for q_span in q_spans:
                            txt = _norm(q_span.text or q_span.get_attribute("innerText") or "")
                            if txt and _is_question_text(txt) and _norm_lc(txt) not in opt_lc:
                                question = txt
                                log_debug("[DOM_CONTEXT]", f"mriweb_mr_fallback resolved question={question[:60]!r}")
                                break
                except Exception:
                    pass

            # Pattern spécifique
            if not question:
                # Pattern spécifique
                try:
                    if not question:
                        el_label = driver.find_elements("css selector", "#label")
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
                        if (len(near_lc) < 140) and ("veuillez" in near_lc) and (("sélection" in near_lc) or ("selection" in near_lc)):
                            is_meta = True
                    if (near_lc not in opt_lc) and (not is_meta):
                        question = near

                if not question:
                    heading = _norm(_find_group_heading_text_near_element(driver, els[0], options))
                    if heading:
                        heading_lc = _norm_lc(heading)
                        opt_lc = {_norm_lc(o) for o in (options or []) if o}
                        if heading_lc not in opt_lc:
                            question = heading

            # Cint/QPS et similaires : p.muted sibling de h2#label
            # → instruction de cardinalité à appender à la question principale.
            # Conditions : question déjà établie (non vide), #label présent dans
            # le DOM, élément .muted visible avec texte non déjà inclus.
            if question:
                try:
                    muted_instruction = _norm(_pw_page(driver).evaluate(
                        """() => {
                        const labelEl = document.querySelector('#label');
                        if (!labelEl || !labelEl.parentElement) return '';
                        const candidates = labelEl.parentElement.querySelectorAll(
                            '.muted, p.help-block, small.help-block, .instruction-text'
                        );
                        for (const c of candidates) {
                            const st = window.getComputedStyle(c);
                            if (!st || st.display === 'none' || st.visibility === 'hidden') continue;
                            const r = c.getBoundingClientRect();
                            if (r.width === 0 && r.height === 0) continue;
                            const txt = (c.innerText || c.textContent || '').replace(/\s+/g, ' ').trim();
                            if (txt.length >= 4) return txt;
                        }
                        return '';
                    }"""
                    ) or "")
                    if muted_instruction and _norm_lc(muted_instruction) not in _norm_lc(question):
                        question = question + " " + muted_instruction
                except Exception:
                    pass

            if not question:
                # dernier recours: bloc "1 option" (rare, mais utile)
                if len(options) == 1 and len(els) == 1:
                    question = options[0]
                else:
                    group_reject_reasons["missing_question"] = group_reject_reasons.get("missing_question", 0) + 1
                    continue

            # Pattern spécifique
            if not options and len(els) == 1 and question:
                options = [question]

            if (
                itype == "radio"
                and len(els) == 1
                and len(options) == 1
                and _looks_like_aggregated_container_option(options[0], question)
            ):
                group_reject_reasons["radio_aggregated_container_option"] = (
                    group_reject_reasons.get("radio_aggregated_container_option", 0) + 1
                )
                continue
            if itype == "checkbox" and _is_checkbox_optout_companion_for_text(driver, els, options):
                group_reject_reasons["checkbox_optout_companion_text"] = (
                    group_reject_reasons.get("checkbox_optout_companion_text", 0) + 1
                )
                if is_debug():
                    try:
                        log_debug(
                            "[DOM_GROUPING]",
                            f"skip_checkbox_optout_companion group_key={group_key} option={options[0] if options else ''}",
                        )
                    except Exception:
                        pass
                continue

            # Pattern spécifique
            # Pattern spécifique
            sig = group_key if group_key.startswith(f"{itype}:name:") else (question, itype)
            if sig in seen_signatures:
                group_reject_reasons["duplicate_signature"] = group_reject_reasons.get("duplicate_signature", 0) + 1
                continue
            seen_signatures.add(sig)

            # --- target_id + registry pour group (radio/checkbox)
            target_id = make_target_id("group", group_key, question)

            # map option -> xpath de l'input correspondant
            option_xpath_map = {}
            for e, lbl in option_elements:
                try:
                    # Pattern spécifique
                    if not lbl and len(els) == 1 and question:
                        lbl = question

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

                        # MetrixLab/Toluna single-select pattern DOM-only:
                        # input[type=checkbox].radioQT dans un wrapper .answer_options
                        # avec UI cliquable portée par .option_radio.
                        # On cible explicitement ce wrapper visuel plutôt que l'input.
                        is_radioqt = False
                        has_answer_options = False
                        has_option_radio = False
                        try:
                            is_radioqt = "radioqt" in _norm_lc(e.get_attribute("class") or "")
                        except Exception:
                            is_radioqt = False
                        if is_radioqt:
                            try:
                                has_answer_options = bool(
                                    e.find_elements(
                                        "xpath",
                                        "ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1]",
                                    )
                                )
                            except Exception:
                                has_answer_options = False
                            try:
                                has_option_radio = bool(
                                    e.find_elements(
                                        "xpath",
                                        "ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1]//*[contains(concat(' ', normalize-space(@class), ' '), ' option_radio ')]",
                                    )
                                )
                            except Exception:
                                has_option_radio = False

                        if is_radioqt and has_answer_options and has_option_radio:
                            xp = (
                                f"(//*[@id={id_lit}]/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1]"
                                f"//*[contains(concat(' ', normalize-space(@class), ' '), ' option_radio ')][1]"
                                f" | //*[@id={id_lit}]/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1])"
                            )
                        elif inp_id:

                        # Pattern spécifique
                        # Pattern spécifique
                            in_grid = False
                            try:
                                in_grid = bool(e.find_elements("xpath", "ancestor::table[contains(@class,'grid')][1]"))
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
                                    has_label = bool(driver.find_elements("xpath", f"//label[@for={id_lit}]"))
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
                            lab = e.find_element("xpath", "ancestor::label[1]")
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

            _block_ctx: Dict[str, Any] = {"kind": "group", "group_key": group_key}
            if inline_openend_names:
                _block_ctx["inline_openend_names"] = list(dict.fromkeys(inline_openend_names))

            block = {
                "question": question,
                "itype": itype,
                "options": options,
                "max_select": _compute_max_select(itype, options, _selection_signal_text(driver, els[0], question)),
                "target_id": target_id,
                "context": _block_ctx,
            }

            # Nfield/Kantar rowpicker: tag exclusive radio blocks for post-merge
            if itype == "radio":
                try:
                    if els and all(
                        _norm_lc(e.get_attribute("isexclusive") or "") in {"true", "1", "yes", "on"}
                        for e in els
                    ):
                        radio_name = _norm_lc(els[0].get_attribute("name") or "")
                        if radio_name:
                            block["context"]["nfield_exclusive_radio"] = True
                            block["context"]["radio_name"] = radio_name
                except Exception:
                    pass

            # Nfield/Kantar rowpicker: store checkbox name prefix for post-merge
            if itype == "checkbox" and group_key.startswith("checkbox:name:dom_container:"):
                try:
                    cb_names = [_norm_lc(e.get_attribute("name") or "") for e in els]
                    cb_names = [n for n in cb_names if n]
                    if len(cb_names) >= 2:
                        common = cb_names[0]
                        for n in cb_names[1:]:
                            while n and not n.startswith(common):
                                common = common[:-1]
                            if not common:
                                break
                        if common and common.endswith("-"):
                            block["context"]["nfield_checkbox_name_prefix"] = common
                except Exception:
                    pass

            question_blocks.append(block)
            created_group_count += 1

            if is_debug() and group_key.startswith("checkbox:name:dom_container:"):
                try:
                    log_debug(
                        "[DOM_GROUPING] checkbox_container_group_created "
                        f"group_key={group_key} options={len(options)}"
                    )
                except Exception:
                    pass
        except Exception:
            group_reject_reasons["group_exception"] = group_reject_reasons.get("group_exception", 0) + 1
            continue

    if is_debug():
        print(
            "[DOM_CONTEXT_DEBUG] analyze_dom choice_groups "
            f"detected={len(groups)} created={created_group_count} "
            f"rejected={group_reject_reasons}"
        )

    # Pattern spécifique
    # Objectif: quand les options ne sont PAS des <input type=radio> visibles,
    # mais une liste de <li>/<button> cliquables (ex: Decipher cardrating)

    def _is_nav_like_choice(text: str) -> bool:
        v = _norm_lc(text)
        if not v:
            return False
        nav_tokens = [
            "continue", "continuer", "next", "suivant",
            "back", "retour", "previous", "précédent", "precedent",
            "ok", "submit", "valider", "envoyer", "send",
            "start", "commencer", "finish", "terminer",
            "close", "fermer", "cancel", "annuler",
            "refuser", "decline",
        ]
        return any(tok in v for tok in nav_tokens)

    def _resolve_button_group_container(el, fallback_container):
        """
        Trouve le plus petit ancêtre DOM qui représente un groupe d'options bouton.

        Cas ciblé: single-choice custom rendu via plusieurs <button> (options)
        + un input texte auxiliaire. Sans ce regroupement, chaque bouton peut être
        vu isolément et le fallback texte prend la main.
        """
        try:
            host = _pw_page(driver).evaluate(
                r"""(el) => {
                if (!el || !el.parentElement) return null;
                const navTokens = ['next','suivant','continue','continuer','submit','start','back','retour','previous'];
                const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                const isVisible = (node) => {
                  if (!node || !(node instanceof Element)) return false;
                  const st = window.getComputedStyle(node);
                  if (!st) return false;
                  if (st.display === 'none' || st.visibility === 'hidden') return false;
                  const r = node.getBoundingClientRect();
                  return r.width > 0 && r.height > 0;
                };
                let cur = el.parentElement;
                for (let depth = 0; cur && depth < 7; depth++, cur = cur.parentElement) {
                  if (!isVisible(cur)) continue;
                  const btns = Array.from(cur.querySelectorAll('button, [role="button"], a[role="button"]'));
                  if (btns.length < 2 || btns.length > 12) continue;
                  let nonNav = 0;
                  for (const b of btns) {
                    if (!isVisible(b)) continue;
                    const txt = norm(b.innerText || b.textContent || '');
                    if (!txt || txt.length < 2) continue;
                    if (navTokens.some(tok => txt.includes(tok))) continue;
                    nonNav += 1;
                  }
                  if (nonNav >= 2) return cur;
                }
                return null;
            }""", _handle(el))
            if host:
                return host
        except Exception:
            pass
        return fallback_container

    def _stable_xpath_for_buttonish(el) -> str:
        """
        Locator stable prioritaire pour Decipher:
        - data-uid est très souvent unique et stable sur la page.
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
            "css selector",
            "button, a[role='button'], [role='button'], .sq-cardrating-button"
        )
    except Exception:
        btn_like = []

    btn_groups: Dict[str, Dict[str, Any]] = {}

    def _is_bootstrap_select_toggle(el) -> bool:
        """Ignore bootstrap-select toggle buttons to avoid fake radio groups.

        These controls mirror native <select> options and must not be treated as
        standalone radio/button-choice options.
        """
        try:
            tag = _norm_lc(getattr(el, "tag_name", "") or "")
            cls = _norm_lc(el.get_attribute("class") or "")
            data_toggle = _norm_lc(el.get_attribute("data-toggle") or "")

            if "bootstrap-select" in cls:
                return True
            if data_toggle == "dropdown" and "dropdown-toggle" in cls:
                return True
            if tag == "button" and "dropdown-toggle" in cls and (el.get_attribute("data-id") or "").strip():
                return True

            return bool(
                _pw_page(driver).evaluate(
                    """(el) => {
                    if (!el || !(el instanceof Element)) return false;
                    if (el.closest('div.bootstrap-select')) return true;
                    const dt = (el.getAttribute('data-toggle') || '').trim().toLowerCase();
                    if (dt === 'dropdown' && el.classList.contains('dropdown-toggle')) return true;
                    return false;
                }""", _handle(el))
            )
        except Exception:
            return False
        except Exception:
            return False

    for b in btn_like:
        try:
            if not _is_actionable_visible(b):
                continue
            if _is_modal_related_control(driver, b):
                continue
            if _is_bootstrap_select_toggle(b):
                continue

            # Filtre CookieYes consent banner : ignore boutons portant data-cky-tag
            # ou appartenant au conteneur .cky-consent-container
            try:
                if b.get_attribute("data-cky-tag") is not None:
                    continue
                _in_cky = _pw_page(driver).evaluate("(el) => el.closest('.cky-consent-container') !== null", _handle(b))
                if _in_cky:
                    continue
            except Exception:
                pass

            # Filtre interview-footer : les boutons spéciaux (Aucun(e), Passer…)
            # dans .interview-footer__options-container sont des options de navigation
            # de page, pas des choix de réponse. Guard DOM strict : ancêtre direct.
            try:
                _in_footer = _pw_page(driver).evaluate("(el) => el.closest('.interview-footer__options-container') !== null", _handle(b))
                if _in_footer:
                    continue
            except Exception:
                pass

            # Filtre Bulbshare UI : progress bar (data-survey-progress) et branding
            # (data-survey-bulbshare) ne sont pas des options de réponse.
            try:
                if (
                    b.get_attribute("data-survey-progress") is not None
                    or b.get_attribute("data-survey-bulbshare") is not None
                ):
                    continue
            except Exception:
                pass

            # Filtre Decipher cardrating : ignore disabled / non-clickable
            cls = _norm_lc(b.get_attribute("class") or "")
            if "sq-cardrating-button" in cls:
                if _norm_lc(b.get_attribute("data-clickable") or "") in ("false", "0"):
                    continue
                if _norm_lc(b.get_attribute("data-disabled") or "") in ("true", "1"):
                    continue

            # Exclude <tr role="button"> in <thead> (lookup table column headers, not selectable)
            _b_tag = _norm_lc(getattr(b, "tag_name", "") or "")
            if _b_tag == "th":
                continue
            if _b_tag == "tr":
                try:
                    _in_thead = _pw_page(driver).evaluate("(el) => el.closest('thead') !== null", _handle(b))
                    if _in_thead:
                        continue
                except Exception:
                    pass

            # Texte (pour cardrating, le texte est dans le <li>)
            t = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
            if _b_tag == "tr":
                # Voxco lookup table: build label from <td> cells (not raw row text)
                try:
                    _tds = b.find_elements("css selector", "td")
                    _td_texts = [_norm(td.text or td.get_attribute("innerText") or "") for td in _tds]
                    _td_texts = [x for x in _td_texts if x]
                    if _td_texts:
                        t = " | ".join(_td_texts)
                except Exception:
                    pass
            if (not t or len(t) < 2) and "sq-cardrating-button" in cls:
                # Pattern spécifique
                try:
                    t = _norm(b.find_element("css selector", ".sq-cardrating-content").text)
                except Exception:
                    pass

            if not t or len(t) < 2:
                continue
            if _is_nav_like_choice(t):
                continue

            cont = _nearest_question_container(b)
            if not cont:
                try:
                    cont = b.find_element("xpath", "ancestor::*[self::div or self::section or self::form][1]")
                except Exception:
                    cont = None
            if not cont:
                continue

            cont = _resolve_button_group_container(b, cont)

            cid = (cont.get_attribute("id") or "").strip()
            ccl = _norm_lc(cont.get_attribute("class") or "")
            cont_xpath = _best_xpath_for_element(driver, cont) or ""
            gk = f"btn_group:{cid}:{ccl}:{cont_xpath}"
            g = btn_groups.setdefault(gk, {"container": cont, "buttons": []})
            g["buttons"].append(b)
        except Exception:
            continue

    for _gk, g in (btn_groups or {}).items():
        try:
            cont = g.get("container")
            btns = g.get("buttons") or []
            if len(btns) < 2:
                continue

            options: List[str] = []
            _is_lookup_table = False
            _lookup_columns: List[str] = []
            _lookup_rows: List[Dict[str, Any]] = []
            _btns_are_tr = btns and _norm_lc(getattr(btns[0], "tag_name", "") or "") == "tr"
            for b in btns:
                if _btns_are_tr:
                    try:
                        _tds = b.find_elements("css selector", "td")
                        _td_texts = [_norm(td.text or td.get_attribute("innerText") or "") for td in _tds]
                        tt = " | ".join(x for x in _td_texts if x)
                    except Exception:
                        tt = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
                else:
                    tt = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
                if not tt or _is_nav_like_choice(tt):
                    continue
                if tt not in options:
                    options.append(tt)
            if _btns_are_tr:
                # Extract lookup table columns and row metadata from the enclosing <table>
                try:
                    _table_el = _pw_page(driver).evaluate("(el) => el.closest('table')", _handle(btns[0]))
                    if _table_el:
                        _th_els = _table_el.find_elements("css selector", "thead th")
                        _lookup_columns = [
                            _norm(th.text or th.get_attribute("innerText") or "") for th in _th_els
                        ]
                        _lookup_columns = [c for c in _lookup_columns if c]
                        if _lookup_columns:
                            _is_lookup_table = True
                            for b in btns:
                                _row_id = (b.get_attribute("id") or "").strip()
                                _tds = b.find_elements("css selector", "td")
                                _row_vals: Dict[str, str] = {}
                                for i, td in enumerate(_tds):
                                    if i < len(_lookup_columns):
                                        _row_vals[_lookup_columns[i]] = _norm(
                                            td.text or td.get_attribute("innerText") or ""
                                        )
                                if _row_vals:
                                    _lookup_rows.append({"row_id": _row_id, "values": _row_vals})
                except Exception:
                    pass

            if len(options) < 2:
                continue

            question = ""

            # Guard Forsta/Confirmit : si le conteneur est dans un ancêtre
            # div[class*="cf-question"], lire le texte depuis div.cf-question__text
            # (+ div.cf-question__instruction si présente).
            # Scopé strictement : déclenché uniquement si cf-question__text non vide trouvé.
            if cont:
                try:
                    _cf_q_text = _pw_page(driver).evaluate(
                        """(el) => {
                        const cfq = el.closest('[class*="cf-question"]');
                        if (!cfq) return null;
                        const txt = cfq.querySelector('.cf-question__text');
                        if (!txt || !txt.innerText.trim()) return null;
                        const instr = cfq.querySelector('.cf-question__instruction');
                        const parts = [txt.innerText.trim()];
                        if (instr && instr.innerText.trim()) parts.push(instr.innerText.trim());
                        return parts.join(' ');
                    }""", _handle(cont))
                    if _cf_q_text:
                        question = _norm(_cf_q_text)
                except Exception:
                    pass

            if not question and cont:
                question = _extract_question_from_container(cont, options=options) or ""

            if not question:
                question = _norm(_find_question_text_near_element(driver, btns[0]))

            # Patch interview-layout : quand le conteneur est dans .interview-question,
            # la vraie question principale est dans h1.interview-header__title (frère
            # dans .interview-layout), hors scope du conteneur. On la récupère et on
            # la préfixe au hint déjà extrait (ex : "CHOISISSEZ UNE OU PLUSIEURS RÉPONSES").
            # Guard DOM strict : les deux sélecteurs doivent exister simultanément.
            try:
                _in_interview_q = _pw_page(driver).evaluate("(el) => el.closest('.interview-question') !== null", _handle(cont)) if cont else False
                if _in_interview_q:
                    _h1_els = driver.find_elements("css selector", "h1.interview-header__title")
                    if _h1_els:
                        _h1_txt = _norm(_h1_els[0].text or _h1_els[0].get_attribute("innerText") or "")
                        if _h1_txt:
                            # Concatène : titre principal + hint (si différent)
                            if question and _norm_lc(_h1_txt) not in _norm_lc(question):
                                question = f"{_h1_txt} {question}"
                            elif not question:
                                question = _h1_txt
                            log_debug(
                                "[DOM_BUTTON_GROUP]",
                                f"interview_layout_h1 recovered: {_h1_txt[:80]!r}",
                            )
            except Exception:
                pass

            # Patch Bulbshare (my.bulbshare.com) : quand le conteneur résolu est
            # dans .pollItemWrap, la question réelle est dans h2.pollItemTitle et
            # l'instruction dans div.itemRulesWrapper — tous deux hors scope du
            # conteneur div.css-gos33m. Guard DOM strict : .pollItemWrap + h2.pollItemTitle.
            try:
                _poll_wrap = _pw_page(driver).evaluate("(el) => el.closest('.pollItemWrap')", _handle(cont)) if cont else None
                if _poll_wrap:
                    _h2_els = _poll_wrap.find_elements("css selector", "h2.pollItemTitle")
                    if _h2_els:
                        _poll_q = _norm(_h2_els[0].text or _h2_els[0].get_attribute("innerText") or "")
                        if _poll_q:
                            _rules_els = _poll_wrap.find_elements("css selector", "div.itemRulesWrapper")
                            _rules_txt = ""
                            if _rules_els:
                                _rules_txt = _norm(_rules_els[0].text or _rules_els[0].get_attribute("innerText") or "")
                            question = f"{_poll_q} {_rules_txt}".strip() if _rules_txt else _poll_q
                            log_debug(
                                "[DOM_BUTTON_GROUP]",
                                f"bulbshare_pollItemWrap recovered: {question[:120]!r}",
                            )
            except Exception:
                pass

            # Pattern spécifique
            qlc = _norm_lc(question)
            if qlc and ("un problème est survenu" in qlc or ((len(qlc) < 140) and ("veuillez" in qlc) and (("sélection" in qlc) or ("selection" in qlc)))):
                # Pattern spécifique
                for cand in btns[1:3]:
                    near2 = _norm(_find_question_text_near_element(driver, cand))
                    near2_lc = _norm_lc(near2)
                    if near2 and ("un problème est survenu" not in near2_lc) and not ("veuillez" in near2_lc and "sélection" in near2_lc):
                        question = near2
                        break

            question = _norm(question)
            if not question:
                continue

            # Détection multi-select interview-layout.
            # Guard A : ul[data-test-id="ChoiceMultiple_ChoiceFields"] (choix texte standard).
            # Guard B : div[role="listbox"] portant class image-select ou image-choice-question__answers
            #           (image-choice multi-sélection). Scopé strictement par ces attributs DOM.
            # Si l'un des deux est vrai → itype=checkbox, max_select=len(options).
            # Sinon → comportement par défaut radio/1.
            _is_choice_multiple = False
            try:
                _is_choice_multiple = _pw_page(driver).evaluate(
                    """(btn) => {
                    const ul = btn.closest('ul[data-test-id="ChoiceMultiple_ChoiceFields"]');
                    if (ul !== null) return true;
                    const lb = btn.closest('div[role="listbox"]');
                    if (lb !== null) {
                        const cls = lb.className || '';
                        return cls.includes('image-select') || cls.includes('image-choice-question__answers');
                    }
                    return false;
                }""", _handle(btns[0]))
            except Exception:
                _is_choice_multiple = False

            _block_itype = "checkbox" if _is_choice_multiple else "radio"
            _block_max_select = len(options) if _is_choice_multiple else 1

            sig = (question, _block_itype)
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)

            # group_key stable-ish: id/class du conteneur + quelques options
            cid = (cont.get_attribute("id") or "").strip() if cont else ""
            ccl = _norm_lc(cont.get_attribute("class") or "") if cont else ""
            opt_sig = "|".join(_norm_key(o) for o in (options[:5] or []))
            group_key = f"{_block_itype}:button_group:{cid}:{ccl}:{opt_sig}"

            target_id = make_target_id("group", group_key, question)

            option_xpath_map = {}
            for b in btns:
                if _btns_are_tr:
                    try:
                        _tds = b.find_elements("css selector", "td")
                        _td_texts = [_norm(td.text or td.get_attribute("innerText") or "") for td in _tds]
                        lbl = " | ".join(x for x in _td_texts if x)
                    except Exception:
                        lbl = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
                else:
                    lbl = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
                if not lbl or _is_nav_like_choice(lbl):
                    continue
                xp = _best_xpath_for_element(driver, b)
                if xp:
                    option_xpath_map[_norm_key(lbl)] = xp

            if not option_xpath_map:
                continue

            _reg_ctx: Dict[str, Any] = {
                "kind": "group",
                "itype": _block_itype,
                "group_key": group_key,
                "question": question,
                "option_xpath_map": option_xpath_map,
                "frame_chain": frame_chain,
            }
            if _is_lookup_table:
                _reg_ctx["lookup_table"] = True
                _reg_ctx["columns"] = _lookup_columns
                _reg_ctx["rows"] = _lookup_rows
            register_target(target_id, _reg_ctx)

            _q_ctx: Dict[str, Any] = {"kind": "group", "group_key": group_key}
            if _is_lookup_table:
                _q_ctx["lookup_table"] = True
                _q_ctx["columns"] = _lookup_columns
                _q_ctx["rows"] = _lookup_rows

            question_blocks.append(
                {
                    "question": question,
                    "itype": _block_itype,
                    "options": options,
                    "max_select": _block_max_select,
                    "target_id": target_id,
                    "context": _q_ctx,
                }
            )
        except Exception:
            continue

    handled_select_ids: Set[str] = set()
    handled_select_names: Set[str] = set()

    # --- 1b) Qualtrics matrix dropdown rows (1 row = 1 dropdown block) ---
    try:
        qmx_blocks, qmx_select_ids, qmx_select_names = _extract_qualtrics_matrix_dropdown_row_blocks(driver, frame_chain)
        if qmx_blocks:
            question_blocks.extend(qmx_blocks)
            handled_select_ids.update(qmx_select_ids)
            handled_select_names.update(qmx_select_names)
    except Exception:
        pass

    # --- 2) Autres inputs (dropdown / text / textarea / button) ---
    try:
        other_inputs = driver.find_elements(
            "css selector",
            "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']",
        )
    except Exception:
        other_inputs = []

    for el in other_inputs:
        try:
            itype = _detect_itype(el)

            # 1) On ignore les champs techniques/hidden
            if itype == "hidden" or _looks_like_system_field(el):
                if is_debug():
                    _el_id = (el.get_attribute("id") or "").strip()
                    _el_name = (el.get_attribute("name") or "").strip()
                    log_debug("[SINGLES_SKIP]", f"hidden_or_system itype={itype} id={_el_id!r} name={_el_name!r}")
                continue

            # Pattern spécifique
            is_bootstrap_selectpicker = (
                (el.tag_name or "").strip().lower() == "select"
                and "selectpicker" in _norm_lc(el.get_attribute("class") or "")
            )
            if not is_bootstrap_selectpicker and not _is_actionable_visible(el):
                if is_debug():
                    _el_id = (el.get_attribute("id") or "").strip()
                    _el_name = (el.get_attribute("name") or "").strip()
                    log_debug("[SINGLES_SKIP]", f"not_actionable_visible itype={itype} id={_el_id!r} name={_el_name!r}")
                continue

            if itype in ("radio", "checkbox", "unknown"):
                continue

            if itype == "dropdown":
                el_id = (el.get_attribute("id") or "").strip()
                el_name = (el.get_attribute("name") or "").strip()
                if (el_id and el_id in handled_select_ids) or (el_name and el_name in handled_select_names):
                    if is_debug():
                        log_debug("[SINGLES_SKIP]", f"already_handled_select id={el_id!r} name={el_name!r}")
                    continue

            # on ne veut pas transformer un "bouton next" en question
            if itype == "button":
                if _is_modal_related_control(driver, el):
                    continue
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
                        "xpath",
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
                    "page suivante", "page précédente",
                }:
                    continue

            container = _nearest_question_container(el) or el

            dropdown_options_for_question: List[str] = []
            if itype == "dropdown":
                try:
                    for o in el.find_elements("tag name", "option"):
                        if o.get_attribute("disabled"):
                            continue
                        t = _norm(o.text or o.get_attribute("innerText") or "")
                        if t:
                            dropdown_options_for_question.append(t)
                    dropdown_options_for_question = list(dict.fromkeys(dropdown_options_for_question))
                except Exception:
                    dropdown_options_for_question = []

            question = ""
            # Critère DOM précis : <legend class="qualification-text"> dans le <fieldset>
            # ancêtre immédiat (pattern prescreener surveys.insights-today.com :
            # fieldset > article > legend.qualification-text). textContent contourne
            # les légendes CSS-invisibles (width/height=0 mais texte présent dans le DOM).
            try:
                ql_nodes = el.find_elements(
                    "xpath",
                    "ancestor::fieldset[1]//legend[contains(@class,'qualification-text')]",
                )
                if ql_nodes:
                    q_txt = _norm(
                        ql_nodes[0].text
                        or ql_nodes[0].get_attribute("innerText")
                        or ql_nodes[0].get_attribute("textContent")
                        or ""
                    )
                    if q_txt and _is_question_text(q_txt):
                        question = q_txt
            except Exception:
                pass
            if not question and itype in ("text", "textarea"):
                try:
                    _el_id = (el.get_attribute("id") or "").strip()
                    if _el_id:
                        _lbl = driver.find_element("css selector", f'label[for="{_el_id}"]')
                        _lbl_txt = _norm(_lbl.text or _lbl.get_attribute("textContent") or "")
                        if _lbl_txt and _is_question_text(_lbl_txt):
                            question = _lbl_txt
                            log_debug("[DOM_DEBUG]", f"text_label_for_priority id={_el_id!r} question={question[:60]!r}")
                except Exception:
                    pass
            if not question and container:
                question = _extract_question_from_container(
                    container,
                    options=dropdown_options_for_question if itype == "dropdown" else [],
                ) or ""

            # Pattern spécifique
            # Pattern spécifique
            # Masqué
            # Pattern spécifique
            multi = False
            hint = None
            try:
                if itype == "dropdown" and container:
                    sels = container.find_elements("tag name", "select")
                    multi = bool(sels and len(sels) >= 2)
                    if multi:
                        hint = _dropdown_field_hint(driver, el)
                        field_labels = {"mois", "month", "année", "year", "jour", "day"}
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

            if itype in ("text", "textarea"):
                mriweb_grid_question = _extract_mriweb_grid_question_text(el)
                if mriweb_grid_question:
                    question = mriweb_grid_question

            if itype == "dropdown" and not question:
                question = _find_bootstrap_selectpicker_question_label(el) or ""

            # GfK mrIWeb: select.mrDropdown — span.mrQuestionText est un sibling DOM
            # du conteneur de choix, pas dans le même conteneur standard.
            if itype == "dropdown" and not question:
                try:
                    el_classes = (el.get_attribute("class") or "").lower()
                    if "mrdropdown" in el_classes:
                        scope_nodes = el.find_elements("xpath", "ancestor::form[1]")
                        scope = scope_nodes[0] if scope_nodes else None
                        q_spans = (
                            scope.find_elements("css selector", "span.mrQuestionText")
                            if scope else
                            driver.find_elements("css selector", "span.mrQuestionText")
                        )
                        opt_lc = {_norm_lc(o) for o in dropdown_options_for_question if o}
                        for q_span in q_spans:
                            txt = _norm(q_span.text or q_span.get_attribute("innerText") or "")
                            if txt and _is_question_text(txt) and _norm_lc(txt) not in opt_lc:
                                question = txt
                                log_debug("[DOM_CONTEXT]", f"mriweb_mrdropdown_fallback resolved question={question[:60]!r}")
                                break
                except Exception:
                    pass

            # Askia (AskiaExt.dll) : select.askia-live dont le td de label ne contient
            # que le sous-label court (ex: "Votre complémentaire santé :").
            # Le titre global ("Aujourd'hui, auprès de quel organisme...") est dans le
            # td ancêtre portant à la fois "askia-caption<N>" et "askia-question-label".
            # Ce td englobant n'est pas le td immédiat du select, donc _extract_question_from_container
            # le manque. Ce bloc remonte explicitement chercher ce titre et le concatène
            # au sous-label déjà trouvé.
            #
            # Gate DOM strict :
            # - itype == "dropdown"
            # - select porte la classe "askia-live"
            # - la question courante est non-vide mais courte (< 80 chars) — probable sous-label
            # Non-régression : si la question est déjà longue (titre global déjà capturé),
            # le bloc ne s'exécute pas.
            if (
                itype == "dropdown"
                and "askia-live" in _norm_lc(el.get_attribute("class") or "")
                and question
                and len(question) < 80
            ):
                try:
                    # Remonter au td qui porte à la fois "askia-caption" et "askia-question-label"
                    parent_label_tds = el.find_elements(
                        "xpath",
                        "ancestor::td["
                        "contains(@class,'askia-caption') and "
                        "contains(@class,'askia-question-label')"
                        "][1]"
                    )
                    if parent_label_tds:
                        parent_td = parent_label_tds[0]
                        # Extraire le textContent complet du td en excluant le contenu
                        # du span#indic (sous-titre instructionnel) et les inputs/selects.
                        parent_full_txt = _norm(
                            _pw_page(driver).evaluate(
                                """(td) => {
                                if (!td) return '';
                                const clone = td.cloneNode(true);
                                clone.querySelectorAll('input, select, textarea, script, style').forEach(n => n.remove());
                                const indic = clone.querySelector('#indic, span[id="indic"]');
                                if (indic) indic.remove();
                                return (clone.innerText || clone.textContent || '').replace(/\s+/g, ' ').trim();
                            }""", _handle(parent_td)
                            ) or ""
                        )
                        # Le titre global est valide s'il est substantiellement plus long
                        # que la question courante (sous-label seul).
                        if parent_full_txt and len(parent_full_txt) > len(question) + 10:
                            # Concaténer : titre global + sous-label (déjà dans question)
                            # Le sous-label peut être déjà présent dans parent_full_txt
                            # (certains rendus Askia l'incluent inline) — on déduplique.
                            if _norm_lc(question) not in _norm_lc(parent_full_txt):
                                question = _norm(f"{parent_full_txt} {question}")
                            else:
                                question = parent_full_txt
                            log_debug(
                                "[DOM_ASKIA_SELECT]",
                                f"parent_label_resolved name={el.get_attribute('name')!r} "
                                f"question={question[:80]!r}",
                            )
                except Exception:
                    pass


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
                    if is_debug():
                        _el_id = (el.get_attribute("id") or "").strip()
                        _el_name = (el.get_attribute("name") or "").strip()
                        log_debug("[SINGLES_SKIP]", f"validation_instruction itype={itype} id={_el_id!r} name={_el_name!r} question={question!r}")
                    continue

            if not question:
                if is_debug():
                    _el_id = (el.get_attribute("id") or "").strip()
                    _el_name = (el.get_attribute("name") or "").strip()
                    log_debug("[SINGLES_SKIP]", f"no_question itype={itype} id={_el_id!r} name={_el_name!r}")
                continue

            if itype in ("text", "textarea") and _is_auxiliary_text_for_choice_group(driver, el, container, question):
                log_debug("[DOM_DEBUG]", "skip_aux_text_with_choice_group")
                continue

            if itype in ("text", "textarea") and _is_open_ended_choice_companion(el, container):
                continue

            if itype in ("text", "textarea") and _is_angular_material_image_only_textarea_question(driver, el, question):
                log_info(
                    "[DOM_UNSUPPORTED]",
                    "angular_material_taimage_textarea_detected -> skip_block (question_dom_unreadable_image_only)",
                )
                continue

            if itype in ("text", "textarea") and _is_other_specify_choice_companion(driver, el, container, question):
                continue

            if itype in ("text", "textarea") and _is_decipher_dropdown_open_companion(container):
                log_debug("[DOM_DEBUG]", "skip_decipher_dropdown_open_companion")
                continue

            # Filtre interview-layout "Autre" : un input[type=text][role="option"] dans
            # div.choice-question__custom-field-container est le champ libre de la liste
            # de choix (options rendues en <button role="option">, pas en input natif).
            # Sans ce guard, _is_other_specify_choice_companion ne le détecte pas
            # (0 input radio/checkbox dans le conteneur) → bloc text parasite créé.
            # Guard DOM strict : les deux conditions doivent tenir simultanément.
            if itype in ("text", "textarea"):
                try:
                    _role_opt = _norm_lc(el.get_attribute("role") or "")
                    if _role_opt == "option":
                        _in_custom = _pw_page(driver).evaluate(
                            "(el) => el.closest('.choice-question__custom-field-container') !== null",
                            _handle(el),
                        )
                        if _in_custom:
                            log_debug("[DOM_DEBUG]", "skip_interview_layout_custom_text_field role=option")
                            continue
                except Exception:
                    pass

            # Champs "other/specify" attachés à une option radio/checkbox custom:
            # ne pas les remonter comme question autonome.
            if itype in ("text", "textarea"):
                try:
                    el_id_lc = _norm_lc(el.get_attribute("id") or "")
                    el_name_lc = _norm_lc(el.get_attribute("name") or "")
                    cls_lc = _norm_lc(el.get_attribute("class") or "")
                    looks_like_other = (
                        el_id_lc.endswith("_other")
                        or el_name_lc.endswith("_other")
                        or "__other" in el_id_lc
                        or "__other" in el_name_lc
                        or "answer__other" in cls_lc
                    )
                    if looks_like_other:
                        linked_to_choice = bool(_pw_page(driver).evaluate(
                            """(el) => {
                            if (!el) return false;
                            const wrappers = ['.cf-radio-answer', '.cf-checkbox-answer', '[role="radio"]', '[role="checkbox"]', '.cf-ranking-answer'];
                            for (const sel of wrappers) { if (el.closest(sel)) return true; }
                            const parent = el.parentElement;
                            if (!parent) return false;
                            return !!parent.querySelector('[role="radio"], [role="checkbox"], .cf-radio, .cf-checkbox');
                        }""", _handle(el)))
                        if linked_to_choice:
                            continue
                except Exception:
                    pass

            # Pattern spécifique
            if itype in ("text", "textarea"):                
                try:
                    # Material/mrIWeb: grille texte (table.mrGridTable) avec names
                    # de type `<var>_Q__N_QAnswer` => un seul bloc multi_text.
                    try:
                        mriweb_grids = el.find_elements("xpath", "ancestor::table[contains(@class,'mrGridTable')][1]")
                    except Exception:
                        mriweb_grids = []

                    if mriweb_grids:
                        grid = mriweb_grids[0]
                        grid_id = (grid.get_attribute("id") or "").strip()
                        if not grid_id:
                            try:
                                grid_id = _best_xpath_for_element(driver, grid) or ""
                            except Exception:
                                grid_id = ""

                        try:
                            grid_inputs = grid.find_elements("css selector", "input.mrEdit[type='text'][name]")
                        except Exception:
                            grid_inputs = []

                        mriweb_rows = []
                        for gi in grid_inputs:
                            try:
                                if _looks_like_system_field(gi):
                                    continue
                                nm = (gi.get_attribute("name") or "").strip()
                                mt = re.match(r"^(?P<prefix>.+)_Q__(?P<idx>\d+)_QAnswer$", nm)
                                if not mt:
                                    continue
                                mriweb_rows.append((int(mt.group("idx")), mt.group("prefix"), gi))
                            except Exception:
                                continue

                        if len(mriweb_rows) >= 2:
                            prefixes = {row[1] for row in mriweb_rows}
                            if len(prefixes) == 1:
                                prefix = next(iter(prefixes))
                                group_key = f"mriweb_grid:{grid_id}:{prefix}"
                                if group_key in seen_multi_text_groups:
                                    continue

                                mriweb_rows.sort(key=lambda row: row[0])
                                max_items = len(mriweb_rows)
                                multi_target_id = make_target_id("multi", group_key, question)

                                field_payloads = []
                                for _idx, _pref, fld in mriweb_rows:
                                    try:
                                        fid = (fld.get_attribute("id") or "").strip()
                                        fname = (fld.get_attribute("name") or "").strip()
                                        ftag = (fld.tag_name or "").strip().lower()
                                        fxp = _best_xpath_for_element(driver, fld)

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
                                            "meta": {
                                                "max_items": max_items,
                                                "multi_text": True,
                                                "mriweb_grid": True,
                                            },
                                        },
                                    )

                                    question_blocks.append(
                                        {
                                            "question": question,
                                            "itype": itype,
                                            "options": [],
                                            "max_select": max_items,
                                            "target_id": multi_target_id,
                                            "context": {
                                                "kind": "multi_text",
                                                "fields_count": len(field_payloads),
                                                "max_items": max_items,
                                                "name_prefix": prefix,
                                            },
                                        }
                                    )

                                    seen_multi_text_groups.add(group_key)
                                    continue

                    cont_id = (container.get_attribute("id") or "").strip()
                    nm = (el.get_attribute("name") or "").strip()

                    # prefix: "QA03:948176_1" -> "QA03:948176"
                    # also handles dot-notation: "ans1656.0.0" -> "ans1656.0"
                    prefix = nm
                    m_pref = re.match(r"^(.*)_(\d{1,3})$", nm)
                    if m_pref:
                        prefix = m_pref.group(1)
                    else:
                        m_dot = re.match(r"^(.*\.\d+)\.(\d{1,3})$", nm)
                        if m_dot:
                            prefix = m_dot.group(1)

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
                        peers = container.find_elements(
                            "css selector",
                            "input:not([type='radio']):not([type='checkbox']):not([type='hidden']):not([type='button']):not([type='submit']):not([type='reset']):not([type='file']):not([type='image']), textarea",
                        )
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
                                else:
                                    mm2 = re.match(r"^(.*\.\d+)\.(\d{1,3})$", pn)
                                    if mm2 and mm2.group(1) == prefix:
                                        same_prefix_count += 1

                        date_tokens = {
                            "month": ("month", "mois", "mm", "date_m", "dobmonth"),
                            "day": ("day", "jour", "dd", "date_d", "dobday"),
                            "year": ("year", "annee", "année", "yyyy", "yy", "date_y", "dobyear"),
                        }

                        def _field_blob(x):
                            try:
                                parts = [
                                    x.get_attribute("name") or "",
                                    x.get_attribute("id") or "",
                                    x.get_attribute("placeholder") or "",
                                    x.get_attribute("aria-label") or "",
                                ]
                                try:
                                    lbl = _find_associated_label(driver, x) or ""
                                    if lbl:
                                        parts.append(lbl)
                                except Exception:
                                    pass
                                return _norm_lc(" ".join(parts))
                            except Exception:
                                return ""

                        field_blobs = [_field_blob(f) for f in fields]
                        has_date_triplet = bool(
                            len(fields) >= 3
                            and any(any(tok in blob for tok in date_tokens["month"]) for blob in field_blobs)
                            and any(any(tok in blob for tok in date_tokens["day"]) for blob in field_blobs)
                            and any(any(tok in blob for tok in date_tokens["year"]) for blob in field_blobs)
                        )

                        date_group_key = f"multitext_date:{cont_id}" if has_date_triplet else ""
                        effective_group_key = date_group_key or group_key

                        if effective_group_key in seen_multi_text_groups:
                            continue

                        if has_one_per_box or same_prefix_count >= 2 or has_date_triplet:
                            if has_date_triplet:
                                log_debug(
                                    "[DOM_DATE_MULTI_TEXT]",
                                    f"detected date triplet fields={len(fields)} names={[((f.get_attribute('name') or '').strip()) for f in fields][:5]}",
                                )
                            max_items = min(3, len(fields)) if has_date_triplet else len(fields)
                            multi_target_id = make_target_id("multi", effective_group_key, question)

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
                                if has_date_triplet:
                                    _date_role_map = [
                                        ("month", date_tokens["month"], "Birth month (MM)"),
                                        ("day",   date_tokens["day"],   "Birth day (DD)"),
                                        ("year",  date_tokens["year"],  "Birth year (YYYY)"),
                                    ]
                                    _added = 0
                                    for _role, _toks, _q_label in _date_role_map:
                                        _matched = next(
                                            (
                                                (i, fp)
                                                for i, (fp, blob) in enumerate(zip(field_payloads, field_blobs))
                                                if any(tok in blob for tok in _toks)
                                            ),
                                            None,
                                        )
                                        if _matched is None:
                                            continue
                                        _idx, _fp = _matched
                                        _tid = make_target_id("date", f"{effective_group_key}:{_role}", question)
                                        register_target(
                                            _tid,
                                            {
                                                "kind": "single",
                                                "itype": "text",
                                                "question": _q_label,
                                                "xpath": _fp["xpath"],
                                                "alt_xpaths": _fp["alt_xpaths"],
                                                "tag": _fp["tag"],
                                                "name": _fp["name"],
                                                "id": _fp["id"],
                                                "frame_chain": frame_chain,
                                            },
                                        )
                                        question_blocks.append(
                                            {
                                                "question": _q_label,
                                                "itype": "text",
                                                "options": [],
                                                "max_select": 1,
                                                "target_id": _tid,
                                                "context": {"kind": "single"},
                                                "min_select": 1,
                                            }
                                        )
                                        _added += 1
                                    if _added:
                                        seen_multi_text_groups.add(effective_group_key)
                                        continue
                                else:
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

                                    seen_multi_text_groups.add(effective_group_key)
                                    continue
                except Exception:
                    pass

            # Pattern spécifique
            # Pattern spécifique
            # Pattern spécifique
            if itype == "dropdown":
                try:
                    if not multi and container:
                        sels = container.find_elements("tag name", "select")
                        if len(sels) >= 2:
                            multi = True
                    if multi:
                        if not hint:
                            hint = _dropdown_field_hint(driver, el)
                        if hint and hint.lower() not in (question or "").lower():
                            question = _norm(f"{question} , {hint}")
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
            elif itype in ("text", "textarea"):
                try:
                    in_mriweb_grid = bool(el.find_elements("xpath", "ancestor::table[contains(@class,'mrGridTable')][1]"))
                except Exception:
                    in_mriweb_grid = False
                if in_mriweb_grid:
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
                if is_debug():
                    log_debug("[SINGLES_SKIP]", f"duplicate_sig itype={itype} sig={sig!r}")
                continue
            seen_signatures.add(sig)

            options: List[str] = []
            if itype == "dropdown":
                options = dropdown_options_for_question

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
                "max_select": _compute_max_select(itype, options, _selection_signal_text(driver, el, question)),
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

        except Exception as _singles_exc:
            if is_debug():
                try:
                    _el_id = (el.get_attribute("id") or "").strip()
                    _el_name = (el.get_attribute("name") or "").strip()
                except Exception:
                    _el_id = _el_name = "?"
                log_debug(
                    "[SINGLES_SKIP]",
                    f"unhandled_exception id={_el_id!r} name={_el_name!r} exc={type(_singles_exc).__name__}: {_singles_exc}",
                )
            continue

    return question_blocks



# ================================================================================
# POINT D'ENTRÉE - ANALYZE DOM
# ================================================================================

# Kantar/mrIWeb: détection préalable des metaTypes non supportés (SEJson)
# ────────────────────────────────────────────────────────────────────────

_SEJSON_UNSUPPORTED_METATYPES: set = set()  # dragndrop now handled by _extract_nfield_dragndrop_blocks


def _detect_sejson_unsupported_metatype(driver) -> str:
    """
    Lit le script <script class="SEJson" type="application/json"> injecté par
    Kantar/mrIWeb et retourne le metaType non supporté si trouvé, sinon "".

    Déclencheur DOM-first : présence de script.SEJson + champ metaType dans
    _SEJSON_UNSUPPORTED_METATYPES. Aucun effet sur les pages sans ce script.
    """
    try:
        scripts = driver.find_elements("css selector", 'script.SEJson[type="application/json"]')
        for script in (scripts or []):
            raw = script.get_attribute("textContent") or script.get_attribute("innerHTML") or ""
            # Strip HTML comment wrapper: <!-- ... //-->
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
                meta_type = ((q.get("CustomProps") or {}).get("metaType") or "").lower()
                if meta_type in _SEJSON_UNSUPPORTED_METATYPES:
                    return meta_type
    except Exception:
        pass
    return ""


def _find_fullscreen_iframe_idx(driver) -> "int | None":
    """Retourne l'index (dans default_content) de la première iframe fullscreen overlay.

    Garde-fou DOM strict: style doit contenir position:fixed ET width:100% ET height:100%.
    Ne détecte pas les iframes partielles.
    """
    page = _pw_page(driver)
    try:
        frames = page.query_selector_all("iframe, frame")
    except Exception:
        return None
    for idx, frame in enumerate(frames):
        try:
            style = (frame.get_attribute("style") or "").lower().replace(" ", "")
            if "position:fixed" in style and "width:100%" in style and "height:100%" in style:
                return idx
        except Exception:
            continue
    return None


# ================================================================================

def analyze_dom(driver) -> List[Dict[str, Any]]:
    """
    Analyse le DOM et retourne une liste de QuestionBlock.
    Frame-aware: choisit automatiquement le meilleur contexte (default ou iframe) jusqu'a depth=DOM_FRAME_MAX_DEPTH (défaut=2).
    """
    clear_registry()

    _wait_for_survey_dom(driver)

    # Early exit: Kantar/mrIWeb page with unsupported metaType (e.g. dragndrop)
    _unsupported_meta = _detect_sejson_unsupported_metatype(driver)
    if _unsupported_meta:
        log_info("[DOM_ANALYZER]", f"sejson_metatype_unsupported={_unsupported_meta} -> skip extraction (dragndrop_unsupported)")
        return []

    max_depth = int(os.getenv("DOM_FRAME_MAX_DEPTH", "2") or "2")
    best_chain, _meta = _select_best_frame_chain(driver, max_depth=max_depth)
    if is_debug():
        runtime_rows = int(_meta.get("runtime_answer_rows_count", 0) or 0)
        runtime_wrappers = int(_meta.get("runtime_radio_wrappers_count", 0) or 0)
        runtime_sig_in_selected_context = runtime_rows >= 2 and runtime_wrappers >= 2
        log_debug("[DOM_CONTEXT_DEBUG]", f"runtime_signature rows={runtime_rows} wrappers={runtime_wrappers} in_selected_context={runtime_sig_in_selected_context}")
        log_debug("[DOM_CONTEXT_DEBUG]", f"analyze_dom selected_chain={best_chain} selected_ps_date_question={_meta.get('selected_ps_date_question_count', 0)} score={_meta.get('score', 0)}")
        log_debug("[DOM_CONTEXT_DEBUG]", f"analyze_dom stage=context_selected blocks_count=0 chain_len={len(best_chain or [])}")

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

    def _should_skip_focusvision_answers_list_groups(items: List[Dict[str, Any]]) -> bool:
        """
        Garde-fou DOM minimal:
        - si un bloc cardsort est déjà détecté
        - et si le cardsort courant expose au moins une carte avec atmost > 1
        alors on évite l'extracteur answers-list (table cachée) pour ne pas dupliquer
        des groupes checkbox non pilotables par l'UI cardsort.
        """
        if not any((b or {}).get("kind") == "cardsort" for b in (items or [])):
            return False

        try:
            cards = _pw_page(driver).query_selector_all("li.sq-cardsort-card[atmost]")
        except Exception:
            return False

        for card in cards or []:
            try:
                atmost_raw = _norm(card.get_attribute("atmost") or "")
                if atmost_raw and int(atmost_raw) > 1:
                    return True
            except Exception:
                continue

        return False

    def _drop_cardsort_when_mixed_with_other_blocks(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        has_cardsort = any((b or {}).get("kind") == "cardsort" for b in (items or []))
        has_non_cardsort = any((b or {}).get("kind") != "cardsort" for b in (items or []))
        if not (has_cardsort and has_non_cardsort):
            return items
        return [b for b in (items or []) if (b or {}).get("kind") != "cardsort"]

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
        if not _should_skip_focusvision_answers_list_groups(blocks):
            blocks.extend(_extract_focusvision_answers_list_groups(driver, frame_chain=chain))
            blocks = _drop_cardsort_when_mixed_with_other_blocks(blocks)
        blocks.extend(_extract_angular_material_radio_groups(driver, frame_chain=chain))
        blocks.extend(_extract_decipher_grid_select_blocks(driver, frame_chain=chain))

        if not blocks:
            blocks = _extract_qarts_hidden_answers_groups(driver, frame_chain=chain)
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
                if not _should_skip_focusvision_answers_list_groups(blocks):
                    blocks.extend(_extract_focusvision_answers_list_groups(driver))
                    blocks = _drop_cardsort_when_mixed_with_other_blocks(blocks)
                blocks.extend(_extract_angular_material_radio_groups(driver))
                blocks.extend(_extract_decipher_grid_select_blocks(driver, frame_chain=chain))

                if not blocks:
                    blocks = _extract_qarts_hidden_answers_groups(driver, frame_chain=chain)
                if not blocks:
                    blocks = _extract_decipher_answers_list_fallback(driver, frame_chain=chain)

    if is_debug():
        itypes = sorted({str((b or {}).get("itype") or "") for b in (blocks or []) if (b or {}).get("itype")})
        log_debug("[DOM_CONTEXT_DEBUG]", f"extracted_blocks count={len(blocks or [])} itypes={itypes}")

    # Iframe fullscreen overlay (ex. Tobii/sticky.ai consent modal)
    # Déclenché uniquement si aucun bloc trouvé via le chemin normal.
    if not blocks:
        fs_idx = _find_fullscreen_iframe_idx(driver)
        if fs_idx is not None:
            log_debug("[DOM_CONTEXT_DEBUG]", f"fullscreen_iframe detected idx={fs_idx} trying consent extraction")
            with switch_to_frame_chain(driver, [fs_idx]) as ok:
                if ok:
                    blocks = _extract_single_consent_checkbox_block(driver, [fs_idx])
                    if blocks:
                        log_info("[DOM_ANALYZER]", f"fullscreen_iframe_consent frame_idx={fs_idx} blocks={len(blocks)}")

    blocks = _dedupe_question_blocks(blocks)
    blocks = _merge_nfield_rowpicker_exclusive_radio(blocks)

    blocks = _prune_focusvision_fragmented_groups(blocks)
    blocks = _prune_focusvision_auxiliary_openended_singles(blocks)
    blocks = _prune_trailing_open_inline_singles(blocks)

    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        max_select = int(block.get("max_select", 1) or 1)
        itype = _norm_lc(block.get("itype") or "")
        question_text = block.get("question")
        options = [str(o) for o in (block.get("options") or []) if str(o).strip()]
        block["min_select"] = _compute_min_select(itype, question_text, options, max_select)

    # Pour les blocs qualtrics_choice_structure_checkbox avec matrix_row_index,
    # min_select doit être 1 : "Sélectionnez toutes les réponses qui s'appliquent"
    # est une instruction DOM libre, pas un minimum imposé. L'extracteur inclut le
    # texte complet de la question dans row_question, ce qui déclenche
    # has_explicit_multi_indicator → min_select = max_select (artefact).
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        ctx_b = block.get("context") if isinstance(block.get("context"), dict) else {}
        if (
            ctx_b.get("qualtrics_choice_structure_checkbox") is True
            and ctx_b.get("matrix_row_index") is not None
            and not ctx_b.get("cap_hard")
        ):
            block["min_select"] = 1

    summary_itypes = sorted({str((b or {}).get("itype") or "") for b in (blocks or []) if (b or {}).get("itype")})
    options_count = sum(len((b or {}).get("options") or []) for b in (blocks or []))
    first_question_len = len(((blocks or [{}])[0].get("question") or "")) if blocks else 0
    log_info("[DOM_CONTEXT]", f"extracted_blocks count={len(blocks or [])} itypes={summary_itypes} question_len={first_question_len} options_count={options_count}")

    if is_debug():
        log_debug("[DOM_CONTEXT_DEBUG]", f"analyze_dom stage=raw_extraction blocks_count={len(blocks or [])} sample={_blocks_summary_preview(blocks)}")
        log_debug("[DOM_CONTEXT_DEBUG]", f"analyze_dom stage=before_return blocks_count={len(blocks or [])} sample={_blocks_summary_preview(blocks)}")

    return blocks


def _merge_nfield_rowpicker_exclusive_radio(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Fusionne les paires Kantar/Nfield rowpicker: bloc checkbox DOM-container + radio exclusif.

    Conditions DOM strictes (les deux doivent être vraies dans le même fieldset ancêtre):
    - bloc checkbox: context.nfield_checkbox_name_prefix = "<prefix>-"
    - bloc radio: context.nfield_exclusive_radio=True, context.radio_name = "<prefix>"
    - même texte de question (normalisé)

    Résultat: le radio est absorbé dans le checkbox; ses options sont ajoutées à la fin
    et listées dans context.exclusive_options.
    """
    if not blocks:
        return blocks

    exclusive_radios: dict[str, Dict[str, Any]] = {}
    for b in blocks:
        if not isinstance(b, dict):
            continue
        ctx = b.get("context") if isinstance(b.get("context"), dict) else {}
        if ctx.get("nfield_exclusive_radio") is True:
            radio_name = _norm_lc(ctx.get("radio_name") or "")
            if radio_name:
                exclusive_radios[radio_name] = b

    if not exclusive_radios:
        return blocks

    absorbed: set[int] = set()
    result: list[Dict[str, Any]] = []

    for b in blocks:
        if not isinstance(b, dict):
            result.append(b)
            continue
        ctx = b.get("context") if isinstance(b.get("context"), dict) else {}
        itype = _norm_lc(b.get("itype") or "")

        if itype != "checkbox":
            result.append(b)
            continue

        prefix = _norm_lc(ctx.get("nfield_checkbox_name_prefix") or "")
        if not (prefix and prefix.endswith("-")):
            result.append(b)
            continue

        base = prefix[:-1]
        radio_block = exclusive_radios.get(base)
        if radio_block is None:
            result.append(b)
            continue

        cb_q = _norm_lc(b.get("question") or "")
        r_q = _norm_lc(radio_block.get("question") or "")
        if not (cb_q and r_q and cb_q == r_q):
            result.append(b)
            continue

        radio_options = [_norm(o) for o in (radio_block.get("options") or []) if _norm(o)]
        merged_options = list(b.get("options") or [])
        seen = {_norm_lc(o) for o in merged_options}
        for opt in radio_options:
            if _norm_lc(opt) not in seen:
                merged_options.append(opt)

        new_ctx = dict(ctx)
        new_ctx.pop("nfield_checkbox_name_prefix", None)
        new_ctx["exclusive_options"] = radio_options

        merged = dict(b)
        merged["options"] = merged_options
        merged["context"] = new_ctx

        absorbed.add(id(radio_block))
        result.append(merged)

        log_debug(
            "[DOM_GROUPING]",
            f"nfield_rowpicker_merge prefix={prefix} exclusive_opts={radio_options}",
        )

    return [b for b in result if id(b) not in absorbed]


def _dedupe_question_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Déduplique les blocs équivalents générés par plusieurs extracteurs.

    Stratégie principale (radio/checkbox):
    - même itype
    - même context.group_key (quand présent)
    - même signature d'options normalisées
    -> on garde le "meilleur" bloc.

    Compatibilité legacy:
    - fallback sur (question, itype, options) pour les autres types.
    """
    if not blocks:
        return blocks

    def _options_sig(block: Dict[str, Any]) -> tuple[str, ...]:
        return tuple(sorted(_norm((o or "")).lower() for o in (block.get("options") or []) if _norm(o)))

    def _dedup_signature(block: Dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
        itype = _norm((block.get("itype") or "")).lower()
        options_sig = _options_sig(block)
        context = (block.get("context") or {}) if isinstance(block.get("context"), dict) else {}
        group_key = _norm(((context.get("group_key")) or "")).lower()
        question = _norm((block.get("question") or "")).lower()

        # Decipher `i-question-table` text rows partagent le même texte parent,
        # mais chaque ligne est une sous-question indépendante (R1/R2/R3...).
        # On inclut une signature ligne-champ pour éviter de fusionner ces blocs.
        if itype == "text" and context.get("decipher_table_text_rows") is True:
            row_label = _norm((context.get("row_label") or "")).lower()
            field_name = _norm((context.get("name") or "")).lower()
            field_id = _norm((context.get("id") or "")).lower()
            row_key = f"decipher_row:{field_name}:{field_id}:{row_label}"
            return (itype, row_key, tuple())

        if itype in {"radio", "checkbox"} and group_key:
            return (itype, f"group_key:{group_key}", tuple())
        return (itype, f"question:{question}", options_sig)

    def _question_pollution_penalty(question: str) -> int:
        q = _norm((question or "")).lower()
        if not q:
            return 50
        tokens = [t for t in re.findall(r"[a-zà-ÿ0-9]+", q, flags=re.IGNORECASE) if t]
        if not tokens:
            return 50
        penalty = 0
        low_signal_tokens = {"radio", "checkbox", "input", "button"}
        for bad in low_signal_tokens:
            bad_count = sum(1 for t in tokens if t == bad)
            if bad_count > 1:
                penalty += (bad_count - 1) * 8
        unique_ratio = len(set(tokens)) / max(len(tokens), 1)
        if len(tokens) >= 10 and unique_ratio < 0.6:
            penalty += 12
        if len(tokens) >= 18:
            penalty += 8
        return penalty

    def _block_quality_score(block: Dict[str, Any]) -> tuple[int, int, int, int]:
        context = (block.get("context") or {}) if isinstance(block.get("context"), dict) else {}
        focusvision_priority = 1 if context.get("focusvision_answers_list") is True else 0
        pollution_score = -_question_pollution_penalty(_norm((block.get("question") or "")))
        target_score = 1 if _norm((block.get("target_id") or "")) else 0
        option_xpath_map = block.get("option_xpath_map")
        xpath_score = len(option_xpath_map) if isinstance(option_xpath_map, dict) else 0
        return (focusvision_priority, pollution_score, target_score, xpath_score)

    dedup_map: dict[tuple[str, str, tuple[str, ...]], Dict[str, Any]] = {}
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue

        sig = _dedup_signature(b)
        cur = dedup_map.get(sig)
        if cur is None:
            dedup_map[sig] = b
            continue

        sig_is_named_group = sig[1].startswith("group_key:")
        if sig_is_named_group:
            cur_opts = [o for o in (cur.get("options") or []) if _norm(o)]
            new_opts = [o for o in (b.get("options") or []) if _norm(o)]

            cur_ctx = (cur.get("context") or {}) if isinstance(cur.get("context"), dict) else {}
            new_ctx = (b.get("context") or {}) if isinstance(b.get("context"), dict) else {}
            cur_is_focusvision = cur_ctx.get("focusvision_answers_list") is True
            new_is_focusvision = new_ctx.get("focusvision_answers_list") is True

            # Garde-fou DOM-first FocusVision/Decipher answers-list:
            # quand un bloc dédié (focusvision_answers_list=True) collisionne avec
            # un bloc générique du même group_key, on conserve le bloc dédié tel quel.
            # Cela évite d'unionner des options polluées (ex: "{row} {col}") avec
            # les colonnes propres extraites depuis la grille group-by-row.
            if cur_is_focusvision != new_is_focusvision:
                if cur_is_focusvision and len(cur_opts) >= 2:
                    dedup_map[sig] = cur
                    continue
                if new_is_focusvision and len(new_opts) >= 2:
                    dedup_map[sig] = b
                    continue

            richer, other = (cur, b) if len(cur_opts) >= len(new_opts) else (b, cur)

            merged_options: list[str] = []
            seen_opt_keys: set[str] = set()
            for src in (richer, other):
                for opt in (src.get("options") or []):
                    opt_norm = _norm(opt)
                    if not opt_norm:
                        continue
                    opt_key = opt_norm.lower()
                    if opt_key in seen_opt_keys:
                        continue
                    seen_opt_keys.add(opt_key)
                    merged_options.append(opt_norm)

            cleaner = cur
            if _question_pollution_penalty(_norm((b.get("question") or ""))) < _question_pollution_penalty(
                _norm((cur.get("question") or ""))
            ):
                cleaner = b

            merged = dict(richer)
            merged["question"] = _norm((cleaner.get("question") or "")) or _norm((richer.get("question") or ""))
            merged["options"] = merged_options
            merged["max_select"] = _compute_max_select(
                _norm((merged.get("itype") or "")) or _norm((richer.get("itype") or "")),
                merged_options,
                merged.get("question") or richer.get("question"),
            )
            dedup_map[sig] = merged
            continue

        cur_score = _block_quality_score(cur)
        new_score = _block_quality_score(b)
        if new_score > cur_score:
            if is_debug():
                print(
                    f"[DOM_DEDUP_DEBUG] discard_duplicate keep=new sig={sig[:2]} "
                    f"old_score={cur_score} new_score={new_score}"
                )
            dedup_map[sig] = b
        elif is_debug():
            print(
                f"[DOM_DEDUP_DEBUG] discard_duplicate keep=current sig={sig[:2]} "
                f"old_score={cur_score} new_score={new_score}"
            )

    return list(dedup_map.values()) if dedup_map else blocks


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

    rich_by_type: dict[str, list[dict[str, Any]]] = {}
    for rb in rich_focusvision:
        r_t = _norm((rb.get("itype") or "")).lower()
        if r_t not in {"checkbox", "radio"}:
            continue
        rich_by_type.setdefault(r_t, []).append(rb)

    def _extract_base_name(group_key: str) -> str:
        gk = (group_key or "").strip()
        if not gk:
            return ""
        if ":name:" in gk:
            return gk.split(":name:", 1)[1].strip()
        return ""

    pruned: list[dict] = []
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue

        b_q = _norm((b.get("question") or "")).lower()
        b_t = _norm((b.get("itype") or "")).lower()
        b_opts = {_norm((o or "")).lower() for o in (b.get("options") or []) if _norm(o)}
        b_group_key = _norm(((b.get("context") or {}).get("group_key") or "")).strip()

        drop_fragment = False
        fragment_like = (
            b_t in {"checkbox", "radio"}
            and len(b_opts) <= 1
            and bool(b_group_key)
            and ":name:" not in b_group_key
        )
        if fragment_like:
            for rb in rich_by_type.get(b_t, []):
                r_opts = {_norm_lc(o or "") for o in (rb.get("options") or []) if _norm(o)}
                rb_group_key = _norm(((rb.get("context") or {}).get("group_key") or "")).strip()
                base_name = _extract_base_name(rb_group_key)

                # DOM-first: mapping fragment ans1025.0.X vers groupe riche checkbox:name:ans1025.0
                if base_name and b_group_key.startswith(f"{base_name}."):
                    if b_opts and b_opts.issubset(r_opts):
                        drop_fragment = True
                        break

                # Fallback legacy: match par texte si base_name absent/non détectable.
                if not base_name:
                    r_q = _norm((rb.get("question") or "")).lower()
                    if b_q == r_q and b_opts and b_opts.issubset(r_opts):
                        drop_fragment = True
                        break

        if not drop_fragment:
            pruned.append(b)

    return pruned


def _prune_focusvision_auxiliary_openended_singles(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Supprime les blocs single text/textarea auxiliaires liés aux options
    "Autre ... préciser" déjà détectées dans un groupe FocusVision answers-list.

    Deux cas couverts :
    1. Le textarea a un attribut `name` présent dans `aux_openended_names` du groupe.
    2. Le textarea n'a ni `name` ni `id` (cas Decipher MX Collapsible "Autre" sans name)
       et la page contient au moins un groupe focusvision_answers_list — dans ce cas,
       un textarea/text sans identifiant ne peut pas être une question principale.
    """
    aux_names: set[str] = set()
    has_focusvision_group = False
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue
        context = (b.get("context") or {}) if isinstance(b.get("context"), dict) else {}
        if context.get("focusvision_answers_list") is not True:
            continue
        has_focusvision_group = True
        for nm in (context.get("aux_openended_names") or []):
            nm_norm = _norm((nm or "")).strip()
            if nm_norm:
                aux_names.add(nm_norm)

    if not aux_names and not has_focusvision_group:
        return blocks

    pruned: list[dict] = []
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue
        itype = _norm((b.get("itype") or "")).lower()
        context = (b.get("context") or {}) if isinstance(b.get("context"), dict) else {}
        input_name = _norm((context.get("name") or "")).strip()
        input_id = _norm((context.get("id") or "")).strip()

        # Cas 1 : textarea nommé présent dans aux_openended_names
        drop_named = (
            itype in {"text", "textarea"}
            and bool(input_name)
            and input_name in aux_names
            and context.get("kind") == "single"
        )
        # Cas 2 : textarea sans name ni id sur une page FocusVision (OE Autre sans attributs)
        drop_nameless = (
            has_focusvision_group
            and itype in {"text", "textarea"}
            and not input_name
            and not input_id
            and context.get("kind") == "single"
        )
        if not (drop_named or drop_nameless):
            pruned.append(b)

    return pruned


def _prune_trailing_open_inline_singles(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Supprime les blocs text autonomes dont le name/id correspond à un champ texte
    embarqué dans .trailing-open/.openend-inline d'une option radio/checkbox.

    Déclenché uniquement quand au moins un groupe a `inline_openend_names` dans son contexte
    (posé par le chemin d'assemblage radio/checkbox lors de la détection .trailing-open).
    """
    inline_names: set[str] = set()
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue
        context = (b.get("context") or {}) if isinstance(b.get("context"), dict) else {}
        for nm in (context.get("inline_openend_names") or []):
            nm_norm = _norm((nm or "")).strip()
            if nm_norm:
                inline_names.add(nm_norm)

    if not inline_names:
        return blocks

    pruned: list[dict] = []
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue
        itype = _norm((b.get("itype") or "")).lower()
        context = (b.get("context") or {}) if isinstance(b.get("context"), dict) else {}
        if itype in {"text", "textarea"} and context.get("kind") == "single":
            input_name = _norm((context.get("name") or "")).strip()
            input_id = _norm((context.get("id") or "")).strip()
            if (input_name and input_name in inline_names) or (input_id and input_id in inline_names):
                log_debug("[DOM_CONTEXT]", f"prune_trailing_open_inline name={input_name!r} id={input_id!r}")
                continue
        pruned.append(b)
    return pruned