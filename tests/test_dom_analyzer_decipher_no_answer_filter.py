from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, tag_name="div", text="", attrs=None, container=None, displayed=True):
        self.tag_name = tag_name
        self.text = text
        self._attrs = attrs or {}
        self._container = container
        self._displayed = displayed

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")

    def is_displayed(self):
        return self._displayed

    def find_elements(self, by=None, value=None):
        # Keep defaults empty: most analyzer XPath probes should simply miss.
        return []


class _FakeDriver:
    def __init__(self, choice_el, number_el):
        self._choice_el = choice_el
        self._number_el = number_el

    def find_elements(self, by=None, value=None):
        if value == "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)":
            return [self._choice_el]
        if value == "button, a[role='button'], [role='button'], .sq-cardrating-button":
            return []
        if value == "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']":
            return [self._number_el]
        return []

    def execute_script(self, *_args, **_kwargs):
        return None


def _patch_non_generic_extractors(monkeypatch):
    for name in [
        "_extract_focusvision_cardsort_block",
        "_extract_walr_cardsort_block",
        "_extract_askandanswer_mobile_matrix_rows",
        "_extract_askandanswer_selection_list_questions",
        "_extract_rnw_ionicon_multi_choice_blocks",
        "_extract_cmix_simple_grid_question_blocks",
        "_extract_table_matrix_radio_rows",
        "_extract_cmix_radio_question_blocks",
        "_extract_ipsos_slider_question_blocks",
        "_extract_areyounet_matrix_blocks",
        "_extract_areyounet_switch_radio_blocks",
        "_extract_areyounet_switch_checkbox_blocks",
        "_extract_cloudresearch_sentry_blocks",
        "_extract_custom_testid_single_select_radio_blocks",
        "_extract_custom_testid_multi_select_checkbox_blocks",
        "_extract_single_consent_checkbox_block",
        "_extract_purespectrum_mobile_date_blocks",
        "_extract_focusvision_answers_list_groups",
        "_extract_decipher_answers_list_fallback",
        "extract_sliderpoints_question_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_decipher_no_answer_checkbox_is_ignored_when_collecting_generic_choices(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    container = _FakeElement(
        tag_name="div",
        text="Quel âge avez-vous ? Veuillez saisir votre âge exact.",
        attrs={"class": "question number label_Q4a"},
    )

    no_answer_checkbox = _FakeElement(
        tag_name="input",
        attrs={
            "type": "checkbox",
            "id": "_v2_na_Q4a.r99",
            "name": "_v2_na_Q4a.r99",
            "class": "input no-answer checkbox fir-hidden",
        },
        container=container,
    )
    age_input = _FakeElement(
        tag_name="input",
        attrs={
            "type": "number",
            "id": "ans9837.0.0",
            "name": "ans9837.0.0",
            "class": "input text-input",
        },
        container=container,
    )

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_nearest_question_container", lambda el: getattr(el, "_container", None))
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_a, **_k: "Quel âge avez-vous ?")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_find_associated_label", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_extract_ssi_confirmit_question", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda *_a, **_k: "//input[@id='ans9837.0.0']")

    def _fake_detect_itype(el):
        if el is no_answer_checkbox:
            return "checkbox"
        if el is age_input:
            return "text"
        return "unknown"

    monkeypatch.setattr(da, "_detect_itype", _fake_detect_itype)

    blocks = da._analyze_dom_current_context(_FakeDriver(no_answer_checkbox, age_input))

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "text"
