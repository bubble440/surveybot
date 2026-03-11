from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, attrs=None, by_selector=None):
        self._attrs = attrs or {}
        self._by_selector = by_selector or {}

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return list(self._by_selector.get(value or "", []))


def test_detects_mriweb_optout_checkbox_companion_for_text(monkeypatch):
    text_input = _FakeElement(attrs={"id": "_Q0", "type": "text"})
    container = _FakeElement(by_selector={"input#_Q0, textarea#_Q0": [text_input]})
    checkbox = _FakeElement(
        attrs={
            "name": "_QS2_XREF",
            "value": "REF",
            "isexclusive": "true",
            "openendid": "_Q0",
            "type": "checkbox",
        }
    )

    class _Driver:
        def find_elements(self, by=None, value=None):
            return []

    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: container)

    assert da._is_checkbox_optout_companion_for_text(
        _Driver(),
        [checkbox],
        ["Je ne souhaite pas répondre"],
    )


def test_keeps_single_checkbox_when_not_optout_companion(monkeypatch):
    checkbox = _FakeElement(
        attrs={
            "name": "consentCheckbox",
            "value": "1",
            "openendid": "",
            "type": "checkbox",
        }
    )

    class _Driver:
        def find_elements(self, by=None, value=None):
            return []

    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: None)

    assert da._is_checkbox_optout_companion_for_text(
        _Driver(),
        [checkbox],
        ["J'accepte la politique de confidentialité"],
    ) is False
