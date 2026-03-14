from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, text="", attrs=None, by_selector=None):
        self.text = text
        self._attrs = attrs or {}
        self._by_selector = by_selector or {}

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return list(self._by_selector.get(value or "", []))

    def find_element(self, by=None, value=None):
        els = self.find_elements(by=by, value=value)
        if not els:
            raise Exception("not found")
        return els[0]


class _FakeDriver:
    def __init__(self, by_selector=None):
        self._by_selector = by_selector or {}

    def find_elements(self, by=None, value=None):
        return list(self._by_selector.get(value or "", []))


def _patch_extractors_before_generic(monkeypatch):
    for name in [
        "_extract_focusvision_cardsort_block",
        "_extract_walr_cardsort_block",
        "_extract_askandanswer_mobile_matrix_rows",
        "_extract_askandanswer_selection_list_questions",
        "_extract_rnw_ionicon_multi_choice_blocks",
        "_extract_cmix_simple_grid_question_blocks",
        "_extract_cmix_grid_question_blocks",
        "_extract_decipher_clickable_ranking_blocks",
        "_extract_table_matrix_radio_rows",
        "_extract_intellisurvey_table_matrix_blocks",
        "_extract_encuesta_matrix_blocks",
        "_extract_yougov_grid_text_question_blocks",
        "_extract_cmix_radio_question_blocks",
        "_extract_focusvision_answers_list_groups",
        "_extract_decipher_table_text_rows_blocks",
        "_extract_decipher_answers_list_fallback",
        "_extract_areyounet_matrix_blocks",
        "_extract_areyounet_switch_radio_blocks",
        "_extract_areyounet_switch_checkbox_blocks",
        "_extract_custom_testid_single_select_radio_blocks",
        "_extract_custom_testid_multi_select_checkbox_blocks",
        "_extract_angular_material_radio_groups",
        "_extract_runtime_answerrow_radio_blocks",
        "_extract_kantar_rowpicker_radio_blocks",
        "_extract_label_radio_list_blocks",
        "_extract_qualtrics_choice_structure_radio_blocks",
        "extract_sliderpoints_question_blocks",
        "_extract_single_consent_checkbox_block",
        "_extract_consent_modal_radio_block",
        "_extract_ipsos_slider_question_blocks",
        "_extract_confirmit_slider_grid_blocks",
        "_extract_cloudresearch_sentry_blocks",
        "_extract_purespectrum_date_dropdown_blocks",
        "_extract_ps_select_dropdown_blocks",
        "_extract_purespectrum_mobile_date_blocks",
        "_extract_collapsed_section_radio_rows",
        "_extract_jqm_lrw_collapsible_checkbox_rows",
        "_extract_jqm_lrw_collapsible_radio_rows",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_analyze_dom_extracts_button_choice_radio_block(monkeypatch):
    _patch_extractors_before_generic(monkeypatch)

    q_title = _FakeElement(text="Laquelle des affirmations suivantes vous décrit le mieux ?")

    choice_1_button = _FakeElement(
        attrs={"id": "o_Gt7yw8R0pmEhkJcp", "class": "choice"},
        by_selector={".choice__label": [_FakeElement(text="Je suis un être humain")]},
    )
    choice_2_button = _FakeElement(
        attrs={"id": "o_EKqI9RmIAGCc3A1N", "class": "choice"},
        by_selector={".choice__label": [_FakeElement(text="Je suis un robot ou un programme informatique")]},
    )

    choice_1 = _FakeElement(by_selector={"button.choice": [choice_1_button]})
    choice_2 = _FakeElement(by_selector={"button.choice": [choice_2_button]})

    options_root = _FakeElement(by_selector={"div.question-body-options__choice": [choice_1, choice_2]})

    driver = _FakeDriver(
        by_selector={
            "div.question-body-options__inner": [options_root],
            ".question-title__title": [q_title],
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)": [],
            "button, a[role='button'], [role='button'], .sq-cardrating-button": [],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"
    assert blocks[0]["options"] == [
        "Je suis un être humain",
        "Je suis un robot ou un programme informatique",
    ]
    assert (blocks[0].get("context") or {}).get("button_choice_radio") is True
    assert (blocks[0].get("context") or {}).get("group_key", "").startswith("button_choice_radio:")
