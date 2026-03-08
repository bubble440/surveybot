from selenium.webdriver.common.by import By

from Survey import input_checkbox as ic


class _FakeScope:
    def __init__(self, labels=None):
        self._labels = labels or []

    def find_elements(self, by, value):
        if by == By.XPATH and value.startswith(".//label"):
            return self._labels
        return []


class _FakeLabel:
    def __init__(self, fid):
        self._fid = fid

    def get_attribute(self, name):
        if name == "for":
            return self._fid
        return ""


class _FakeCheckbox:
    def __init__(self):
        self.selected = False

    def get_attribute(self, name):
        if name == "type":
            return "checkbox"
        return ""

    def click(self):
        self.selected = True


class _FakeDriver:
    def __init__(self, js_result=None, carousel_js_result=None, checkbox=None):
        self._js_result = js_result
        self._carousel_js_result = carousel_js_result
        self._checkbox = checkbox
        self.find_elements_called = False

    def execute_script(self, script, *args):
        if "#mx-stage-" in script and "mx-carouselapp-scale" in script:
            return self._carousel_js_result
        if "div.answer_options" in script and "radioQT" in script:
            return self._js_result
        return None

    def find_element(self, by, value):
        if by == By.ID and self._checkbox and value == "q1001_a1":
            return self._checkbox
        raise Exception("not found")


def test_click_checkbox_by_label_uses_radioqt_answer_options_wrapper(monkeypatch):
    js_marker = {"clicked": True}
    driver = _FakeDriver(js_result=js_marker)

    monkeypatch.setattr(ic, "find_context_container", lambda _driver, _ctx: _FakeScope())

    result = ic.click_checkbox_by_label(driver, "Un homme", context_hint="Etes-vous")

    assert result == js_marker


def test_click_checkbox_by_label_falls_back_to_standard_label_for(monkeypatch):
    checkbox = _FakeCheckbox()
    driver = _FakeDriver(js_result=None, checkbox=checkbox)
    scope = _FakeScope(labels=[_FakeLabel("q1001_a1")])

    monkeypatch.setattr(ic, "find_context_container", lambda _driver, _ctx: scope)
    monkeypatch.setattr(ic, "scroll_into_view", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ic, "force_checkbox_events", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ic, "is_checked", lambda el: bool(el.selected))

    result = ic.click_checkbox_by_label(driver, "Un homme", context_hint="Etes-vous")

    assert result is checkbox
    assert checkbox.selected is True


def test_click_checkbox_by_label_uses_decipher_mx_carousel_when_dom_guard_matches(monkeypatch):
    js_marker = {"carousel_clicked": True}
    driver = _FakeDriver(carousel_js_result=js_marker)

    monkeypatch.setattr(ic, "find_context_container", lambda _driver, _ctx: _FakeScope())

    result = ic.click_checkbox_by_label(driver, "Baskets", context_hint="Vous-même")

    assert result == js_marker


def test_click_checkbox_by_label_skips_mx_carousel_path_when_guard_not_matched(monkeypatch):
    js_marker = {"clicked": True}
    driver = _FakeDriver(js_result=js_marker, carousel_js_result=None)

    monkeypatch.setattr(ic, "find_context_container", lambda _driver, _ctx: _FakeScope())

    result = ic.click_checkbox_by_label(driver, "Un homme", context_hint="Etes-vous")

    assert result == js_marker
