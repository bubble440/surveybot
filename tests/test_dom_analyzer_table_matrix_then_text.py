from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, tag_name="div", text="", attrs=None):
        self.tag_name = tag_name
        self.text = text
        self._attrs = attrs or {}

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return []


class _FakeDriver:
    def __init__(self, radios, others):
        self._radios = radios
        self._others = others

    def find_elements(self, by=None, value=None):
        value = value or ""
        if value == "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)":
            return list(self._radios)
        if value == "button, a[role='button'], [role='button'], .sq-cardrating-button":
            return []
        if value == "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']":
            return list(self._others)
        return []

    def execute_script(self, *_args, **_kwargs):
        return False


def _patch_extractors(monkeypatch):
    for name in [
        "_extract_focusvision_cardsort_block",
        "_extract_walr_cardsort_block",
        "_extract_askandanswer_mobile_matrix_rows",
        "_extract_askandanswer_selection_list_questions",
        "_extract_rnw_ionicon_multi_choice_blocks",
        "_extract_cmix_simple_grid_question_blocks",
        "_extract_cmix_grid_question_blocks",
        "_extract_decipher_clickable_ranking_blocks",
        "_extract_intellisurvey_table_matrix_blocks",
        "_extract_encuesta_matrix_blocks",
        "_extract_decipher_table_text_rows_blocks",
        "_extract_yougov_grid_text_question_blocks",
        "_extract_cmix_radio_question_blocks",
        "_extract_ipsos_slider_question_blocks",
        "_extract_confirmit_slider_grid_blocks",
        "_extract_confirmit_cf_desktop_grid_blocks",
        "_extract_confirmit_cf_hrs_single_blocks",
        "_extract_groupcaliber_rating_row_blocks",
        "_extract_confirmit_cf_carousel_blocks",
        "_extract_areyounet_matrix_blocks",
        "_extract_areyounet_switch_radio_blocks",
        "_extract_areyounet_switch_checkbox_blocks",
        "_extract_cloudresearch_sentry_blocks",
        "_extract_custom_testid_single_select_radio_blocks",
        "_extract_runtime_answerrow_radio_blocks",
        "_extract_runtime_dropdown_blocks",
        "_extract_kantar_rowpicker_radio_blocks",
        "_extract_label_radio_list_blocks",
        "_extract_qualtrics_choice_structure_radio_blocks",
        "_extract_qualtrics_choice_structure_checkbox_blocks",
        "_extract_questmindshare_chatbot_blocks",
        "_extract_custom_testid_multi_select_checkbox_blocks",
        "_extract_single_consent_checkbox_block",
        "_extract_consent_modal_radio_block",
        "_extract_ps_select_dropdown_blocks",
        "_extract_purespectrum_date_dropdown_blocks",
        "_extract_purespectrum_mobile_date_blocks",
        "_extract_collapsed_section_radio_rows",
        "_extract_jqm_lrw_collapsible_checkbox_rows",
        "_extract_jqm_lrw_collapsible_radio_rows",
        "_extract_savanta_jqm_carousel_block",
        "_extract_button_choice_radio_blocks",
        "_extract_focusvision_answers_list_groups",
        "_extract_angular_material_radio_groups",
        "_extract_decipher_grid_select_blocks",
        "_extract_decipher_answers_list_fallback",
        "extract_sliderpoints_question_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_table_matrix_blocks_do_not_short_circuit_following_text_single(monkeypatch):
    _patch_extractors(monkeypatch)

    matrix_block = {
        "question": "Q matrice | Tablette",
        "itype": "radio",
        "options": ["Possede", "Access", "Je n'utilise pas"],
        "max_select": 1,
        "target_id": "matrix_row_target",
        "context": {
            "kind": "group",
            "group_key": "table_matrix_radio:name:164634x7474x331670a",
            "table_matrix_radio": True,
        },
    }
    monkeypatch.setattr(da, "_extract_table_matrix_radio_rows", lambda *a, **k: [matrix_block])

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_group_key_for_choice", lambda el, _itype: (el.get_attribute("name") or "").strip())
    monkeypatch.setattr(
        da,
        "_detect_itype",
        lambda el: "radio" if (el.tag_name == "input" and (el.get_attribute("type") or "").lower() == "radio") else "text",
    )
    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: _FakeElement(tag_name="div", attrs={"class": "question-container"}))
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_a, **_k: "Question texte 331671")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_find_associated_label", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_extract_ssi_confirmit_question", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda *_a, **_k: "//*[@id='answer164634X7474X331671']")
    monkeypatch.setattr(da, "_is_auxiliary_text_for_choice_group", lambda *_a, **_k: False)
    monkeypatch.setattr(da, "_is_open_ended_choice_companion", lambda *_a, **_k: False)
    monkeypatch.setattr(da, "_is_angular_material_image_only_textarea_question", lambda *_a, **_k: False)
    monkeypatch.setattr(da, "_is_other_specify_choice_companion", lambda *_a, **_k: False)

    radios = [
        _FakeElement(tag_name="input", attrs={"type": "radio", "name": "164634X7474X331670A", "id": "r1"}),
        _FakeElement(tag_name="input", attrs={"type": "radio", "name": "164634X7474X331670A", "id": "r2"}),
    ]
    text_input = _FakeElement(
        tag_name="input",
        attrs={"type": "text", "name": "164634X7474X331671", "id": "answer164634X7474X331671"},
    )

    blocks = da._analyze_dom_current_context(_FakeDriver(radios=radios, others=[text_input]))

    assert len(blocks) == 2
    assert any((b.get("context") or {}).get("group_key") == "table_matrix_radio:name:164634x7474x331670a" for b in blocks)
    assert any((b.get("itype") == "text" and (b.get("context") or {}).get("name") == "164634X7474X331671") for b in blocks)
    assert not any((b.get("context") or {}).get("group_key") == "radio:name:164634X7474X331670A" for b in blocks)
