from surveybot.Survey.dom_question_extractor import _group_key_for_choice, _compute_max_select


class _FakeChoice:
    def __init__(self, attrs, containers=None, displayed=True):
        self._attrs = attrs
        self._containers = containers or []
        self._displayed = displayed

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def is_displayed(self):
        return self._displayed

    def find_elements(self, by=None, value=None):
        if by == "xpath" and value and "ancestor::form[@id='mrForm' or @name='mrForm']" in value:
            for c in self._containers:
                if (c.get_attribute("id") or "") == "mrForm" or (c.get_attribute("name") or "") == "mrForm":
                    return [c]
            return []
        if by == "xpath" and value and value == "ancestor::fieldset[1]":
            for c in self._containers:
                if (getattr(c, "tag_name", "") or "").lower() == "fieldset":
                    return [c]
            return []
        if by == "xpath" and value and "ancestor::*[contains(@class,'mrQuestionTable')][1]" in value:
            for c in self._containers:
                cls = (c.get_attribute("class") or "").lower()
                if "mrquestiontable" in cls:
                    return [c]
            return []
        if by == "xpath" and value and "type-multi" in value and "question-" in value:
            return self._containers
        if by == "xpath" and value and "fieldset[contains(@class,'question-multiple')]" in value:
            return self._containers
        if by == "xpath" and value and ("role='listbox'" in value or "multi-select-container" in value):
            return self._containers
        if by == "xpath" and value and "self::fieldset or contains(@class,'mrQuestionTable')" in value:
            scoped = []
            for c in self._containers:
                cls = (c.get_attribute("class") or "").lower()
                if (getattr(c, "tag_name", "") or "").lower() == "fieldset" or "mrquestiontable" in cls:
                    scoped.append(c)
            return scoped
        return []


class _FakeContainer:
    def __init__(self, attrs, checkboxes=None, legends=None, tag_name="div"):
        self._attrs = attrs
        self._checkboxes = checkboxes or []
        self._legends = legends or []
        self.tag_name = tag_name

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        if by == "xpath" and value == ".//*[contains(@class,'mrQuestionTable') or contains(@class,'mrMultiple')]":
            cls = (self.get_attribute("class") or "").lower()
            if "mrquestiontable" in cls or "mrmultiple" in cls:
                return [self]
            return []
        if by == "xpath" and value == ".//input[@type='checkbox'][@name]":
            return self._checkboxes
        if by == "xpath" and value == ".//input[@type='checkbox']":
            return self._checkboxes
        if by == "xpath" and value == ".//legend[1]":
            return self._legends
        return []


class _FakeLegend:
    def __init__(self, text):
        self.text = text


def test_checkbox_group_key_normalizes_limesurvey_sq_suffix():
    el = _FakeChoice({"name": "863821X420X23041SQ001"})
    assert _group_key_for_choice(el, "checkbox") == "863821x420x23041"


def test_checkbox_group_key_normalizes_limesurvey_a_suffix():
    el = _FakeChoice({"name": "863821X420X23057A8"})
    assert _group_key_for_choice(el, "checkbox") == "863821x420x23057"


def test_radio_group_key_keeps_original_name_shape():
    el = _FakeChoice({"name": "ans139.0.0"})
    assert _group_key_for_choice(el, "radio") == "ans139.0.0"


def test_checkbox_group_key_uses_question_container_for_tivian_names():
    container = _FakeContainer({"class": "question question-121131 type-multi-121"})
    el = _FakeChoice({"name": "v_115"}, containers=[container])
    assert _group_key_for_choice(el, "checkbox") == "question_121131"


def test_checkbox_group_key_uses_listbox_container_when_name_missing():
    container = _FakeContainer({
        "role": "listbox",
        "class": "multi-select-container",
        "id": "ps-multi-select-1",
    })
    el = _FakeChoice({"name": ""}, containers=[container])
    assert _group_key_for_choice(el, "checkbox") == "dom:ps-multi-select-1|multi-select-container"


def test_checkbox_group_key_normalizes_decipher_dot_index_suffix():
    el = _FakeChoice({"name": "ans10518.0.11"})
    assert _group_key_for_choice(el, "checkbox") == "ans10518.0"


def test_checkbox_group_key_normalizes_yougov_question_multiple_suffixes():
    siblings = [
        _FakeChoice({"name": "w38-response-1"}),
        _FakeChoice({"name": "w38-response-2"}),
        _FakeChoice({"name": "w38-response-3"}),
    ]
    fieldset = _FakeContainer({"class": "question question-multiple"}, checkboxes=siblings)
    el = _FakeChoice({"name": "w38-response-2"}, containers=[fieldset])
    assert _group_key_for_choice(el, "checkbox") == "w38-response"


def test_compute_max_select_uses_explicit_exact_count_from_question_text():
    question = "Quels sont les deux animaux parmi les propositions suivantes ? Merci de sélectionner les deux réponses pertinentes."
    options = ["Train", "Ours", "Chaise", "Canard", "Piano"]
    assert _compute_max_select("checkbox", options, question) == 2


def test_compute_max_select_keeps_open_multi_when_no_exact_count_in_question_text():
    question = "Sélectionnez toutes les réponses qui s'appliquent."
    options = ["A", "B", "C", "D"]
    assert _compute_max_select("checkbox", options, question) == 3


def test_checkbox_group_key_uses_dom_container_when_checkbox_names_are_all_distinct():
    siblings = [
        _FakeChoice({"name": "_QQ1_Cr1"}),
        _FakeChoice({"name": "_QQ1_Cr2"}),
        _FakeChoice({"name": "_QQ1_Cr3"}),
    ]
    fieldset = _FakeContainer(
        {"id": "", "class": ""},
        checkboxes=siblings,
        legends=[_FakeLegend("Sélectionnez toutes les réponses appropriées")],
        tag_name="fieldset",
    )
    form = _FakeContainer({"id": "mrForm", "name": "mrForm"})
    el = _FakeChoice({"name": "_QQ1_Cr2"}, containers=[fieldset, form])
    assert _group_key_for_choice(el, "checkbox").startswith("dom_container:fieldset|")


def test_compute_max_select_forces_three_on_explicit_multi_checkbox():
    options = [str(i) for i in range(10)]
    assert _compute_max_select("checkbox", options, "Cochez tout ce qui s'applique") == 3


def test_compute_max_select_forces_three_on_explicit_multi_radio():
    options = [str(i) for i in range(5)]
    assert _compute_max_select("radio", options, "Plusieurs réponses possibles") == 3


def test_compute_max_select_keeps_existing_checkbox_default_without_multi_hint():
    options = [str(i) for i in range(10)]
    assert _compute_max_select("checkbox", options, "Rien n’indique multi") == 10
