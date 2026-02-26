from surveybot.Survey import dom_analyzer as da


class _FakeNode:
    def __init__(self, tag_name="div", attrs=None, text="", container=None):
        self.tag_name = tag_name
        self._attrs = attrs or {}
        self.text = text
        self.container = container

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        value = value or ""
        if "ancestor::*" in value and self.container is not None:
            return [self.container]
        return []


class _FakeDriver:
    def __init__(self, choices, others, labels_by_id):
        self.choices = choices
        self.others = others
        self.labels_by_id = labels_by_id

    def find_elements(self, by=None, value=None):
        v = value or ""
        if "[role='radio']" in v or "[role='checkbox']" in v:
            return self.choices
        if "input:not([type='radio'])" in v:
            return self.others
        if "button" in v and "a[role='button']" in v:
            return []
        return []

    def find_element(self, by=None, value=None):
        if value in self.labels_by_id:
            return self.labels_by_id[value]
        raise Exception("not found")

    def execute_script(self, script, el):
        # utilisé seulement pour le skip des champs _other dans ce test
        return str(el.get_attribute("id") or "").endswith("_other")


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


def test_confirmit_aria_checkbox_extracts_options_and_skips_other_text(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_extract_ssi_confirmit_question", lambda *_: "")
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_args, **_kwargs: "À quel genre vous identifiez-vous ?")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: f"//*[@id='{el.get_attribute('id')}']")

    container = _FakeNode(
        tag_name="div",
        attrs={
            "id": "D2_content",
            "class": "cf-list",
            "aria-labelledby": "D2_title D2_text D2_instruction",
        },
    )

    choices = [
        _FakeNode("div", {"role": "checkbox", "id": "D2_1_control", "aria-labelledby": "D2_1_text"}, container=container),
        _FakeNode("div", {"role": "checkbox", "id": "D2_2_control", "aria-labelledby": "D2_2_text"}, container=container),
    ]
    other_text = _FakeNode("input", {"type": "text", "id": "D2_98_other", "class": "cf-radio-answer__other"}, text="")

    labels = {
        "D2_1_text": _FakeNode("div", text="Homme cisgenre"),
        "D2_2_text": _FakeNode("div", text="Femme cisgenre"),
    }

    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: container)

    driver = _FakeDriver(choices=choices, others=[other_text], labels_by_id=labels)
    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "checkbox"
    assert blocks[0]["options"] == ["Homme cisgenre", "Femme cisgenre"]
