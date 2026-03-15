import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "surveybot"))

from selenium.webdriver.common.by import By

from Survey import action_dispatcher as ad


class _FakeSwitchTo:
    def default_content(self):
        return None


class _FakeInput:
    tag_name = "input"

    def __init__(self):
        self._selected_calls = 0

    def is_selected(self):
        # 1er appel (pré-check): False; 2e appel (dans _dispatch_check_events): True
        # Régression: l'ancien garde idempotent stoppe alors le dispatch d'events.
        self._selected_calls += 1
        return self._selected_calls >= 2

    def get_attribute(self, name):
        if name == "type":
            return "checkbox"
        return ""


class _FakeLabel:
    tag_name = "label"
    rect = {"width": 10, "height": 10}

    def __init__(self, input_id):
        self._input_id = input_id

    def is_displayed(self):
        return True

    def get_attribute(self, name):
        if name == "for":
            return self._input_id
        if name == "class":
            return ""
        return ""

    def find_elements(self, by, value):
        if by == By.TAG_NAME and value == "a":
            return [object()]
        return []


class _FakeDriver:
    current_url = "https://surveyopinion.researchnow.com/survey"

    def __init__(self, label, inp):
        self._label = label
        self._input = inp
        self.switch_to = _FakeSwitchTo()
        self.dispatch_calls = 0

    def find_elements(self, by, value):
        if by == By.XPATH and value == "//label[@for='sstb_86_296151']":
            return [self._label]
        return []

    def find_element(self, by, value):
        if by == By.ID and value == "sstb_86_296151":
            return self._input
        raise Exception("not found")

    def execute_script(self, script, *args):
        # Décipher guard path -> absent
        if "closest('.clickableCell')" in script:
            return None
        if "const inp = arguments[0];" in script and "dispatchEvent(new Event('input'" in script:
            self.dispatch_calls += 1
            return None
        return None


def test_apply_by_target_id_label_anchor_dispatches_events_even_when_input_becomes_selected(monkeypatch):
    inp = _FakeInput()
    label = _FakeLabel("sstb_86_296151")
    driver = _FakeDriver(label, inp)

    monkeypatch.setattr(
        ad,
        "get_target",
        lambda _tid: {
            "kind": "single",
            "itype": "checkbox",
            "option_xpath_map": {"oui": "//label[@for='sstb_86_296151']"},
        },
    )

    ok = ad._apply_by_target_id(driver, "tid_dynata", "checkbox", "oui")

    assert ok is True
    assert driver.dispatch_calls == 1
