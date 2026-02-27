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

    def find_element(self, by=None, value=None):
        raise Exception("not found")


class _FakeDriver:
    def __init__(self, buttons, other_inputs):
        self._buttons = buttons
        self._other_inputs = other_inputs

    def find_elements(self, by=None, value=None):
        v = value or ""
        if "input[type='radio']" in v:
            return []
        if "button, a[role='button'], [role='button'], .sq-cardrating-button" == v:
            return self._buttons
        if "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']" == v:
            return self._other_inputs
        return []

    def execute_script(self, script, *args):
        return None


class _ButtonWithAncestor(_FakeElement):
    def __init__(self, text, local_container):
        super().__init__(tag_name="button", text=text)
        self._local_container = local_container

    def find_element(self, by=None, value=None):
        return self._local_container


class _FakeDriverWithButtonCluster(_FakeDriver):
    def __init__(self, buttons, other_inputs, shared_container):
        super().__init__(buttons, other_inputs)
        self._shared_container = shared_container

    def execute_script(self, script, *args):
        if "nonNav >= 2" in (script or ""):
            return self._shared_container
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
        "_extract_purespectrum_mobile_date_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_analyze_dom_extracts_binary_button_group_as_single_select(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    container = _FakeElement(tag_name="section", attrs={"id": "q1", "class": "question"})
    female = _FakeElement(tag_name="button", text="Female")
    male = _FakeElement(tag_name="button", text="Male")
    free_text = _FakeElement(tag_name="input", attrs={"type": "text", "name": "single_other"})

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: container)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda *_: "//fake")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "Are you....")

    def _question_from_container(_container, options):
        return "Are you...." if options else "Female Male"

    monkeypatch.setattr(da, "_extract_question_from_container", _question_from_container)

    driver = _FakeDriver(buttons=[female, male], other_inputs=[free_text])

    blocks = da._analyze_dom_current_context(driver)

    radio_blocks = [b for b in blocks if b.get("itype") in ("radio", "button")]
    assert len(radio_blocks) == 1
    assert radio_blocks[0]["itype"] == "radio"
    assert radio_blocks[0]["options"] == ["Female", "Male"]
    assert radio_blocks[0]["max_select"] == 1
    assert radio_blocks[0]["question"] != "Female Male"


def test_analyze_dom_groups_buttons_with_shared_dom_host_when_local_ancestor_is_too_narrow(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    shared_container = _FakeElement(tag_name="div", attrs={"id": "", "class": ""})
    local_a = _FakeElement(tag_name="div", attrs={"id": "", "class": ""})
    local_b = _FakeElement(tag_name="div", attrs={"id": "", "class": ""})

    female = _ButtonWithAncestor("Female", local_a)
    male = _ButtonWithAncestor("Male", local_b)
    free_text = _FakeElement(tag_name="input", attrs={"type": "text", "name": "single_other"})

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: None)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda *_: "//fake")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "Are you....")

    def _question_from_container(_container, options):
        return "Are you...." if options else "Female Male"

    monkeypatch.setattr(da, "_extract_question_from_container", _question_from_container)

    driver = _FakeDriverWithButtonCluster(
        buttons=[female, male],
        other_inputs=[free_text],
        shared_container=shared_container,
    )

    blocks = da._analyze_dom_current_context(driver)

    radio_blocks = [b for b in blocks if b.get("itype") in ("radio", "button")]
    assert len(radio_blocks) == 1
    assert radio_blocks[0]["options"] == ["Female", "Male"]
    assert radio_blocks[0]["question"] == "Are you...."
