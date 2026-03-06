import pytest
from surveybot.Survey import dom_analyzer as da


class _FakeNode:
    def __init__(self, *, tag_name="div", text="", inner_text="", attrs=None):
        self.tag_name = tag_name
        self.text = text
        self._inner_text = inner_text or text
        self._attrs = attrs or {}
        self._find_elements_map = {}
        self._find_element_map = {}

    def set_find_elements(self, key, value):
        self._find_elements_map[key] = value

    def set_find_element(self, key, value):
        self._find_element_map[key] = value

    def get_attribute(self, name):
        if name == "innerText":
            return self._inner_text
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return self._find_elements_map.get((by, value), [])

    def find_element(self, by=None, value=None):
        key = (by, value)
        if key not in self._find_element_map:
            raise Exception("not found")
        return self._find_element_map[key]


class _FakeDriver:
    pass


def _build_textarea_fixture(*, heading_text="Commençons cette enquête !"):
    survey_scope = _FakeNode(tag_name="app-survey")
    heading = _FakeNode(tag_name="h1", text=heading_text)
    image = _FakeNode(tag_name="img", attrs={"class": "taImage"})

    textarea = _FakeNode(tag_name="textarea", attrs={"name": "selectedOptField"})
    textarea.set_find_elements(("xpath", "ancestor::mat-form-field[1]"), [_FakeNode(tag_name="mat-form-field")])
    textarea.set_find_element(
        ("xpath", "ancestor::*[self::app-survey or contains(@class,'survey-window') or contains(@class,'survey-section')][1]"),
        survey_scope,
    )

    survey_scope.set_find_elements(("css selector", "img.taImage, img[class*='taImage']"), [image])
    survey_scope.set_find_elements(("css selector", ".header-window h1, .header-window h2, h1[translate], h1, h2"), [heading])

    return _FakeDriver(), textarea


def test_angular_material_taimage_textarea_detected_when_question_equals_heading(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda _driver, _el: "Commençons cette enquête !")
    driver, textarea = _build_textarea_fixture()

    assert da._is_angular_material_image_only_textarea_question(
        driver,
        textarea,
        "Commençons cette enquête !",
    ) is True


def test_angular_material_taimage_textarea_not_detected_when_near_question_is_readable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda _driver, _el: "Quelles sont certaines des raisons... ?")
    driver, textarea = _build_textarea_fixture()

    assert da._is_angular_material_image_only_textarea_question(
        driver,
        textarea,
        "Commençons cette enquête !",
    ) is False
