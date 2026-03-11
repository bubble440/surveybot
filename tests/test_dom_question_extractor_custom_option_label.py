import unicodedata
from surveybot.Survey.dom_question_extractor import _find_associated_label


class _FakeNode:
    def __init__(self, text="", attrs=None):
        self.text = text
        self._attrs = attrs or {}

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")


class _FakeInput:
    def __init__(self, attrs=None, custom_labels=None):
        self._attrs = attrs or {}
        self._custom_labels = custom_labels or []

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        if value == "ancestor::label":
            return []
        if value == "preceding-sibling::label[1] | following-sibling::label[1]":
            return []
        if value == "ancestor::*[contains(@class,'answer_options')][1]//*[contains(@class,'option_label')]":
            return self._custom_labels
        return []


class _FakeDriver:
    def find_element(self, by=None, value=None):
        raise Exception("not found")


def test_find_associated_label_reads_answer_options_custom_label():
    driver = _FakeDriver()
    option_label = _FakeNode(text="Autre, merci de préciser:")
    inp = _FakeInput(attrs={"id": "q1001_a4"}, custom_labels=[option_label])

    got = _find_associated_label(driver, inp)

    assert unicodedata.normalize("NFC", got) == "Autre, merci de préciser:"


class _FakeDriverWithScript(_FakeDriver):
    def execute_script(self, script, _el):
        if "radio-checkbox-wrapper" in script:
            return "Vin rouge"
        return ""


def test_find_associated_label_js_fallback_supports_radio_checkbox_wrapper_scope():
    driver = _FakeDriverWithScript()
    inp = _FakeInput(attrs={"id": "opt_1"}, custom_labels=[])

    got = _find_associated_label(driver, inp)

    assert unicodedata.normalize("NFC", got) == "Vin rouge"
