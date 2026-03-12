from selenium.webdriver.common.by import By

import Survey.action_dispatcher as ad


class _FakeSwitchTo:
    def default_content(self):
        return None


class _FakeNode:
    def __init__(self, *, kind: str, aria_disabled: str = "false"):
        self.kind = kind
        self.aria_disabled = aria_disabled
        self.click_count = 0
        self.selected = False

    @property
    def rect(self):
        return {"width": 40, "height": 20}

    @property
    def tag_name(self):
        return "div" if self.kind == "next" else "input"

    def is_displayed(self):
        return True

    def click(self):
        self.click_count += 1
        if self.kind in {"option", "cell"}:
            self.selected = True

    def is_selected(self):
        return self.selected

    def find_element(self, _by, _value):
        raise Exception("no child")

    def get_attribute(self, name):
        if name == "class":
            return ""
        if name == "id":
            return "opt-1" if self.kind == "option" else ""
        if name == "name":
            return "q6-r7-radio" if self.kind == "option" else ""
        if name == "type":
            return "radio" if self.kind == "option" else ""
        if name == "aria-disabled" and self.kind == "next":
            return self.aria_disabled
        if name == "aria-checked":
            return "true" if self.selected else "false"
        return ""


class _FakeDriver:
    def __init__(self, option_node, next_node, next_xpath):
        self.switch_to = _FakeSwitchTo()
        self._option_node = option_node
        self._next_node = next_node
        self._next_xpath = next_xpath

    def find_elements(self, by, value):
        if by != By.XPATH:
            return []
        if value == "//*[@id='opt-1']":
            return [self._option_node]
        if value == self._next_xpath:
            return [self._next_node]
        return []

    def execute_script(self, script, *args):
        if "getElementById" in script:
            return bool(self._option_node.selected)
        if "querySelector" in script:
            return bool(self._option_node.selected)
        return None


def _payload(next_xpath: str):
    return {
        "kind": "group",
        "itype": "radio",
        "frame_chain": [],
        "question": "Q6 row",
        "option_xpath_map": {"tipiak": "//*[@id='opt-1']"},
        "mx_vertical_carousel_next_xpath": next_xpath,
    }


def test_apply_by_target_id_advances_mx_vertical_carousel_after_radio_click(monkeypatch):
    next_xpath = "//div[@id='question_Q6']//div[contains(@class,'swiper-button-next')]"
    monkeypatch.setattr(ad, "get_target", lambda _tid: _payload(next_xpath))

    option = _FakeNode(kind="option")
    next_btn = _FakeNode(kind="next", aria_disabled="false")
    driver = _FakeDriver(option, next_btn, next_xpath)

    ok = ad._apply_by_target_id(driver, "tid-q6-r7", "radio", "Tipiak")

    assert ok is True
    assert option.selected is True
    assert next_btn.click_count == 1


def test_apply_by_target_id_does_not_advance_when_next_disabled(monkeypatch):
    next_xpath = "//div[@id='question_Q6']//div[contains(@class,'swiper-button-next')]"
    monkeypatch.setattr(ad, "get_target", lambda _tid: _payload(next_xpath))

    option = _FakeNode(kind="option")
    next_btn = _FakeNode(kind="next", aria_disabled="true")
    driver = _FakeDriver(option, next_btn, next_xpath)

    ok = ad._apply_by_target_id(driver, "tid-q6-r7", "radio", "Tipiak")

    assert ok is True
    assert next_btn.click_count == 0


class _FakeDecipherDriver(_FakeDriver):
    def __init__(self, option_node, cell_node, next_node, next_xpath):
        super().__init__(option_node, next_node, next_xpath)
        self._cell_node = cell_node

    def execute_script(self, script, *args):
        if "querySelector('.mx-stage .mx-collapsible-container')" in script:
            return False
        if "const cell = node.closest('.clickableCell');" in script and "hiddenInput" in script:
            return self._cell_node
        if "const inp = cell.querySelector" in script and "fir-hidden" in script:
            return bool(self._cell_node.selected)
        return super().execute_script(script, *args)


def test_apply_by_target_id_advances_mx_vertical_carousel_after_decipher_checkbox_click(monkeypatch):
    next_xpath = "//div[@id='question_Q6']//div[contains(@class,'swiper-button-next')]"

    payload = _payload(next_xpath)
    payload["itype"] = "checkbox"
    monkeypatch.setattr(ad, "get_target", lambda _tid: payload)

    option = _FakeNode(kind="option")
    cell = _FakeNode(kind="cell")
    next_btn = _FakeNode(kind="next", aria_disabled="false")
    driver = _FakeDecipherDriver(option, cell, next_btn, next_xpath)

    ok = ad._apply_by_target_id(driver, "tid-q6-r7", "checkbox", "Tipiak")

    assert ok is True
    assert cell.click_count == 1
    assert next_btn.click_count == 1
