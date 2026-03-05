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
        if "input[type='radio']" in value:
            return [
                _FakeNode("input", {"type": "radio", "id": "r1", "name": "gender"}, text="Male", container=self),
                _FakeNode("input", {"type": "radio", "id": "r2", "name": "gender"}, text="Female", container=self),
            ]
        if "input[type='text'], textarea" in value:
            return []
        if "ancestor::*" in value and self.container is not None:
            return [self.container]
        return []


class _FakeDriver:
    def __init__(self, text_input):
        self.text_input = text_input

    def find_elements(self, by=None, value=None):
        v = value or ""
        if "input[type='radio']" in v and "input[type='checkbox']" in v:
            return [
                _FakeNode("input", {"type": "radio", "id": "r1", "name": "gender"}, text="Male"),
                _FakeNode("input", {"type": "radio", "id": "r2", "name": "gender"}, text="Female"),
            ]
        if "input:not([type='radio'])" in v:
            return [self.text_input]
        if "button" in v and "a[role='button']" in v:
            return []
        return []

    def execute_script(self, *_args, **_kwargs):
        return False


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


def test_skip_auxiliary_text_input_when_mixed_with_choice_group(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    container = _FakeNode(
        "div",
        attrs={"id": "q1", "class": "question-block"},
        text="Choose exactly 1 option Male Female",
    )
    text_input = _FakeNode(
        "input",
        attrs={"type": "text", "placeholder": "Enter your answer here", "name": "aux_gender"},
        text="",
        container=container,
    )

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_detect_itype", lambda el: "text" if el is text_input else "radio")
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_extract_ssi_confirmit_question", lambda *_: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: f"//*[@id='{el.get_attribute('id') or 'aux'}']")
    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: container)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_args, **_kwargs: "Choose exactly 1 option")
    monkeypatch.setattr(da, "_find_associated_label", lambda _driver, el: el.text or "")

    driver = _FakeDriver(text_input=text_input)
    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"


def test_keep_real_text_question_with_own_label_even_if_choices_exist(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    container = _FakeNode(
        "div",
        attrs={"id": "q2", "class": "question-block"},
        text="Choose exactly 1 option Male Female",
    )
    text_input = _FakeNode(
        "input",
        attrs={"type": "text", "placeholder": "", "required": "required", "name": "age"},
        text="",
        container=container,
    )

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_detect_itype", lambda el: "text" if el is text_input else "radio")
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_extract_ssi_confirmit_question", lambda *_: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: f"//*[@id='{el.get_attribute('id') or 'txt'}']")
    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: container)

    def _extract_q(_container, options=None):
        return "Choose exactly 1 option" if options else "What is your age?"

    monkeypatch.setattr(da, "_extract_question_from_container", _extract_q)

    def _label(_driver, el):
        if el is text_input:
            return "Age"
        return el.text or ""

    monkeypatch.setattr(da, "_find_associated_label", _label)

    driver = _FakeDriver(text_input=text_input)
    blocks = da._analyze_dom_current_context(driver)

    assert any(b["itype"] == "radio" for b in blocks)
    assert any(b["itype"] == "text" for b in blocks)


def test_skip_inline_other_text_with_own_label_inside_choice_option(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    container = _FakeNode(
        "div",
        attrs={"id": "question1001", "class": "question radio_question"},
        text="Pour commencer... Etes-vous...?",
    )
    text_input = _FakeNode(
        "input",
        attrs={"type": "text", "id": "t1001_4", "name": "t1001_4", "required": "required"},
        text="",
        container=container,
    )

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_detect_itype", lambda el: "text" if el is text_input else "radio")
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_extract_ssi_confirmit_question", lambda *_: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: f"//*[@id='{el.get_attribute('id') or 'aux'}']")
    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: container)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_args, **_kwargs: "Pour commencer... Etes-vous...?")

    def _label(_driver, el):
        if el is text_input:
            return "Autre, merci de préciser:"
        return el.text or ""

    monkeypatch.setattr(da, "_find_associated_label", _label)

    class _DriverInline(_FakeDriver):
        def execute_script(self, script, *args, **kwargs):
            if "optionRoot" in script:
                return True
            return False

    driver = _DriverInline(text_input=text_input)
    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"


def test_skip_inline_other_text_when_js_probe_unavailable(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    class _OptionRoot(_FakeNode):
        def find_elements(self, by=None, value=None):
            value = value or ""
            if "input[type='radio'], input[type='checkbox']" in value:
                return [_FakeNode("input", {"type": "checkbox", "id": "q1001_a4"})]
            return []

    option_root = _OptionRoot("div", attrs={"class": "answer_options answer_options1001"})
    container = _FakeNode(
        "div",
        attrs={"id": "question1001", "class": "question radio_question"},
        text="Pour commencer... Etes-vous...?",
    )

    class _InlineTextInput(_FakeNode):
        def find_elements(self, by=None, value=None):
            value = value or ""
            if "ancestor::*" in value:
                return [option_root, container]
            return []

    text_input = _InlineTextInput(
        "input",
        attrs={"type": "text", "id": "t1001_4", "name": "t1001_4", "required": "required"},
        text="",
        container=container,
    )

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_detect_itype", lambda el: "text" if el is text_input else "radio")
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_extract_ssi_confirmit_question", lambda *_: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: f"//*[@id='{el.get_attribute('id') or 'aux'}']")
    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: container)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_args, **_kwargs: "Pour commencer... Etes-vous...?")

    def _label(_driver, el):
        if el is text_input:
            return "Autre, merci de préciser:"
        return el.text or ""

    monkeypatch.setattr(da, "_find_associated_label", _label)

    class _DriverNoInlineJs(_FakeDriver):
        def execute_script(self, *_args, **_kwargs):
            raise RuntimeError("js unavailable")

    driver = _DriverNoInlineJs(text_input=text_input)
    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"
