from selenium.webdriver.common.by import By

from surveybot.Survey import input_dropdown


class _FakeElement:
    def __init__(self, *, tag="div", attrs=None, text="", displayed=True):
        self.tag_name = tag
        self._attrs = attrs or {}
        self.text = text
        self._displayed = displayed
        self.clicked = 0

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")

    def is_displayed(self):
        return self._displayed

    def click(self):
        self.clicked += 1


class _FakeDriver:
    def __init__(self, menu_option):
        self._menu_option = menu_option
        self._ui_overlay_opened = None

    def find_elements(self, by, value):
        if by == By.TAG_NAME and value == "select":
            return []
        if by == By.XPATH and "@data-id='months6'" in value:
            return [self._menu_option]
        if by in (By.CSS_SELECTOR, By.XPATH):
            return []
        return []

    def execute_script(self, _script, *_args):
        return None


def test_select_option_with_hint_bootstrap_select_anchor_click(monkeypatch):
    month_select = _FakeElement(tag="select", attrs={"id": "months6", "class": "form-control bs-select-hidden"})
    month_anchor = _FakeElement(tag="a", text="Janvier")
    driver = _FakeDriver(month_anchor)

    def _fake_open_dropdown_generic(_driver, hint=None, context_hint=None):
        _driver._ui_overlay_opened = {
            "type": "dropdown",
            "native": True,
            "hint": hint or "",
            "anchor": month_select,
        }
        return True

    monkeypatch.setattr(input_dropdown, "open_dropdown_generic", _fake_open_dropdown_generic)

    ok = input_dropdown.select_option_with_hint(driver, "Janvier", field_hint="Mois")

    assert ok is True
    assert month_anchor.clicked == 1
    assert driver._ui_overlay_opened is None
