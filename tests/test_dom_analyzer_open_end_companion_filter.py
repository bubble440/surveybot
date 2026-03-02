from surveybot.Survey.dom_analyzer import _is_open_ended_choice_companion


class _FakeElement:
    def __init__(self, attrs=None):
        self._attrs = attrs or {}

    def get_attribute(self, name):
        return self._attrs.get(name, "")


class _FakeContainer:
    def __init__(self, choice_names):
        self._choices = [_FakeElement({"name": n}) for n in choice_names]

    def find_elements(self, by=None, value=None):
        return self._choices


def test_oe_field_linked_to_same_ans_stem_is_filtered():
    field = _FakeElement({"name": "oe10518.0", "id": "oe10518.0", "class": "input text-input oe oe-inline"})
    container = _FakeContainer(["ans10518.0.0", "ans10518.0.1"])

    assert _is_open_ended_choice_companion(field, container) is True


def test_oe_field_without_matching_choice_stem_is_not_filtered():
    field = _FakeElement({"name": "oe10518.0", "id": "oe10518.0", "class": "input text-input oe oe-inline"})
    container = _FakeContainer(["ans99999.0.0"])

    assert _is_open_ended_choice_companion(field, container) is False
