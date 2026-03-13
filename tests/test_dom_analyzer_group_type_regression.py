from surveybot.Survey import dom_analyzer as da


class _FakeInput:
    tag_name = "input"

    def __init__(self, attrs):
        self._attrs = attrs
        self._flags = attrs.get("_flags", {})

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        value = value or ""
        if " radio_question " in value and self._flags.get("radio_question"):
            return [object()]
        if " answer_options " in value and " option_radio " in value and self._flags.get("option_radio"):
            return [object()]
        return []


class _FakeDriver:
    def __init__(self, elements):
        self._elements = elements

    def find_elements(self, by=None, value=None):
        v = value or ""
        if "input[type='radio']" in v:
            return self._elements
        return []


class _FakeDriverWithRadioLayoutVisibility(_FakeDriver):
    def execute_script(self, script, *args):
        if ".radioLayout" in (script or ""):
            return True
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


def test_generic_grouping_keeps_radio_type_for_radio_name_groups(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: None)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "Vous vous identifiez comme… ?")

    labels = {
        "ans139.0.0": "Homme",
        "ans139.0.1": "Femme",
        "ans139.0.2": "Non binaire",
        "ans139.0.3": "Non listé",
    }
    monkeypatch.setattr(da, "_find_associated_label", lambda _driver, el: labels.get(el.get_attribute("id"), ""))

    driver = _FakeDriver(
        [
            _FakeInput({"type": "radio", "name": "ans139.0.0", "id": "ans139.0.0", "value": "0"}),
            _FakeInput({"type": "radio", "name": "ans139.0.0", "id": "ans139.0.1", "value": "1"}),
            _FakeInput({"type": "radio", "name": "ans139.0.0", "id": "ans139.0.2", "value": "2"}),
            _FakeInput({"type": "radio", "name": "ans139.0.0", "id": "ans139.0.3", "value": "3"}),
        ]
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"
    assert blocks[0]["max_select"] == 1
    assert blocks[0]["options"] == ["Homme", "Femme", "Non binaire", "Non listé"]
    assert (blocks[0].get("context") or {}).get("group_key") == "radio:name:ans139.0.0"


class _FakeContainer:
    tag_name = "div"

    def __init__(self, cls):
        self._cls = cls

    def get_attribute(self, name):
        if name == "class":
            return self._cls
        return ""

    def find_elements(self, by=None, value=None):
        return []


def test_generic_grouping_accepts_visible_question_container_when_input_not_displayed(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    container = _FakeContainer("row list-radio question-container")

    def _fake_visible(el):
        return isinstance(el, _FakeContainer)

    monkeypatch.setattr(da, "_is_actionable_visible", _fake_visible)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: container)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_: "Quel type de logement avez-vous?")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "")
    monkeypatch.setattr(da, "_find_associated_label", lambda _driver, el: "Oui" if el.get_attribute("id") == "r1" else "Non")

    driver = _FakeDriver(
        [
            _FakeInput({"type": "radio", "name": "q_lime_1", "id": "r1", "value": "1"}),
            _FakeInput({"type": "radio", "name": "q_lime_1", "id": "r2", "value": "2"}),
        ]
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"
    assert blocks[0]["options"] == ["Oui", "Non"]
    assert (blocks[0].get("context") or {}).get("group_key") == "radio:name:q_lime_1"


def test_generic_grouping_accepts_questioncontainer_with_visible_radiolayout_when_input_hidden(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    container = _FakeContainer("questionContainer")

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: False)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: container)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_: "À laquelle des options suivantes vous identifiez-vous ?")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "")
    monkeypatch.setattr(da, "_find_associated_label", lambda _driver, el: "Un homme" if el.get_attribute("id") == "r1" else "Une femme")

    driver = _FakeDriverWithRadioLayoutVisibility(
        [
            _FakeInput({"type": "radio", "name": "questions[2].selectedAnswer", "id": "r1", "value": "1"}),
            _FakeInput({"type": "radio", "name": "questions[2].selectedAnswer", "id": "r2", "value": "2"}),
        ]
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"
    assert blocks[0]["options"] == ["Un homme", "Une femme"]
    assert (blocks[0].get("context") or {}).get("group_key") == "radio:name:questions[2].selectedanswer"


def test_generic_grouping_recovers_question_from_group_heading_when_near_text_is_option(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: None)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "Oui")
    monkeypatch.setattr(
        da,
        "_find_group_heading_text_near_element",
        lambda *_: "J'autorise la collecte des données de profil",
    )

    labels = {"r1": "Oui", "r2": "Non"}
    monkeypatch.setattr(da, "_find_associated_label", lambda _driver, el: labels.get(el.get_attribute("id"), ""))

    driver = _FakeDriver(
        [
            _FakeInput({"type": "radio", "name": "single_choice_0.1", "id": "r1", "value": "296151"}),
            _FakeInput({"type": "radio", "name": "single_choice_0.1", "id": "r2", "value": "296153"}),
        ]
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"
    assert blocks[0]["options"] == ["Oui", "Non"]
    assert da._norm(blocks[0]["question"]) == da._norm("J'autorise la collecte des données de profil")


def test_metrix_radioqt_checkbox_pattern_is_grouped_as_radio(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: None)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "Etes-vous...?")

    labels = {
        "q1001_a1": "Un homme",
        "q1001_a2": "Une femme",
    }
    monkeypatch.setattr(da, "_find_associated_label", lambda _driver, el: labels.get(el.get_attribute("id"), ""))

    radioqt_flags = {"radio_question": True, "option_radio": True}
    driver = _FakeDriver(
        [
            _FakeInput({"type": "checkbox", "class": "radioQT", "name": "q1001", "id": "q1001_a1", "value": "1", "_flags": radioqt_flags}),
            _FakeInput({"type": "checkbox", "class": "radioQT", "name": "q1001", "id": "q1001_a2", "value": "2", "_flags": radioqt_flags}),
        ]
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"
    assert blocks[0]["max_select"] == 1
    assert (blocks[0].get("context") or {}).get("group_key") == "radio:name:q1001"
