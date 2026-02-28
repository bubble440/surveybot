from surveybot.Survey.dom_question_extractor import _group_key_for_choice


class _FakeChoice:
    def __init__(self, attrs, containers=None):
        self._attrs = attrs
        self._containers = containers or []

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        if by == "xpath" and value and "type-multi" in value and "question-" in value:
            return self._containers
        return []


class _FakeContainer:
    def __init__(self, attrs):
        self._attrs = attrs

    def get_attribute(self, name):
        return self._attrs.get(name, "")


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
