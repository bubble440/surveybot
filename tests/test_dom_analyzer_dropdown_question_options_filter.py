from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, tag_name="div", text="", attrs=None, options=None):
        self.tag_name = tag_name
        self.text = text
        self._attrs = attrs or {}
        self._options = options or []

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        if self.tag_name == "select" and value == "option":
            return self._options
        return []


class _FakeDriver:
    def __init__(self, select_el):
        self._select_el = select_el

    def find_elements(self, by=None, value=None):
        if value == "input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox']":
            return []
        if value == "button, a[role='button'], [role='button'], .sq-cardrating-button":
            return []
        if value == "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']":
            return [self._select_el]
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


def test_dropdown_question_extraction_passes_option_texts_to_container_extractor(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    option_placeholder = _FakeElement(tag_name="option", text="Sélectionnez une seule proposition...", attrs={"disabled": ""})
    option_a = _FakeElement(tag_name="option", text="Grand Est")
    option_b = _FakeElement(tag_name="option", text="Nouvelle Aquitaine")
    select_el = _FakeElement(
        tag_name="select",
        attrs={"id": "ans9592.0.0", "name": "ans9592.0.0"},
        options=[option_placeholder, option_a, option_b],
    )
    container = _FakeElement(
        tag_name="div",
        text="Dans quelle région vivez-vous ? Veuillez sélectionner une réponse. Sélectionnez une seule proposition... Grand Est Nouvelle Aquitaine",
    )

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_detect_itype", lambda _el: "dropdown")
    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: container)
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda *_: "//select[@id='ans9592.0.0']")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "")
    monkeypatch.setattr(da, "_find_associated_label", lambda *_: "")
    monkeypatch.setattr(da, "_extract_ssi_confirmit_question", lambda *_: "")

    captured = {"options": None}

    def _extract_question(_container, options):
        if _container is container:
            captured["options"] = options
            return "Dans quelle région vivez-vous ?"
        return ""

    monkeypatch.setattr(da, "_extract_question_from_container", _extract_question)

    blocks = da._analyze_dom_current_context(_FakeDriver(select_el=select_el))

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "dropdown"
    assert "dans quelle" in blocks[0]["question"].lower()
    assert "vivez-vous" in blocks[0]["question"].lower()
    assert len(captured["options"] or []) == 3
    assert "proposition" in (captured["options"][0] or "").lower()
    assert captured["options"][1:] == ["Grand Est", "Nouvelle Aquitaine"]
    assert len(blocks[0]["options"] or []) == 3
    assert "proposition" in (blocks[0]["options"][0] or "").lower()
    assert blocks[0]["options"][1:] == ["Grand Est", "Nouvelle Aquitaine"]


def test_dropdown_bootstrap_selectpicker_uses_zlabel_fallback_when_question_empty(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    option_a = _FakeElement(tag_name="option", text="Audi")
    option_b = _FakeElement(tag_name="option", text="BMW")
    select_el = _FakeElement(
        tag_name="select",
        attrs={"id": "k3wJy", "name": "k3wJy", "class": "selectpicker my-2 z-select"},
        options=[option_a, option_b],
    )

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_detect_itype", lambda _el: "dropdown")
    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: _FakeElement(tag_name="div", text=""))
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_find_associated_label", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_extract_ssi_confirmit_question", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda *_: "//select[@id='k3wJy']")
    monkeypatch.setattr(
        da,
        "_find_bootstrap_selectpicker_question_label",
        lambda _el: "Quelle est la marque de votre automobile (principal)actuelle?",
    )

    blocks = da._analyze_dom_current_context(_FakeDriver(select_el=select_el))

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "dropdown"
    assert blocks[0]["question"] == "Quelle est la marque de votre automobile (principal)actuelle?"
    assert blocks[0]["options"] == ["Audi", "BMW"]
