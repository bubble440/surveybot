from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, attrs=None, text=""):
        self._attrs = attrs or {}
        self.text = text

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")


class _FakeContainer:
    def __init__(self, choices):
        self._choices = choices

    def find_elements(self, by=None, value=None):
        if "input[type='radio']" in (value or ""):
            return self._choices
        return []


class _Driver:
    def __init__(self, other_context):
        self.other_context = other_context

    def execute_script(self, *_args, **_kwargs):
        return self.other_context


def test_other_specify_text_companion_is_filtered_when_question_duplicates_parent(monkeypatch):
    text_field = _FakeElement({"type": "text", "name": "Q138"})
    choices = [
        _FakeElement({"type": "radio", "name": "Q137", "value": "1"}, text="Orange"),
        _FakeElement({"type": "radio", "name": "Q137", "value": "2"}, text="Autre"),
        _FakeElement({"type": "radio", "name": "Q137", "value": "3"}, text="Sosh"),
    ]
    container = _FakeContainer(choices)

    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_args, **_kwargs: "Quel est votre fournisseur d’accès Internet ?")
    monkeypatch.setattr(da, "_find_associated_label", lambda _driver, el: el.text)

    result = da._is_other_specify_choice_companion(
        _Driver(other_context=True),
        text_field,
        container,
        "Quel est votre fournisseur d’accès Internet ? Orange Autre Sosh",
    )

    assert result is True


def test_real_text_question_is_not_filtered_without_other_context(monkeypatch):
    text_field = _FakeElement({"type": "text", "name": "Q200"})
    choices = [
        _FakeElement({"type": "radio", "name": "Q137", "value": "1"}, text="Oui"),
        _FakeElement({"type": "radio", "name": "Q137", "value": "2"}, text="Non"),
    ]
    container = _FakeContainer(choices)

    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_args, **_kwargs: "Avez-vous internet ?")
    monkeypatch.setattr(da, "_find_associated_label", lambda _driver, el: el.text)

    result = da._is_other_specify_choice_companion(
        _Driver(other_context=False),
        text_field,
        container,
        "Quel est votre âge ?",
    )

    assert result is False
