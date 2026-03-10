from selenium.webdriver.common.by import By

from surveybot.Survey import action_dispatcher as ad


class _FakeSwitchTo:
    def default_content(self):
        return None

    def frame(self, _frame):
        return None


class _FakeInput:
    tag_name = "input"

    def __init__(self, *, selected: bool = False):
        self.selected = selected
        self.click_count = 0

    @property
    def rect(self):
        return {"width": 20, "height": 20}

    def click(self):
        self.click_count += 1
        self.selected = True

    def is_selected(self):
        return self.selected

    def is_displayed(self):
        return True

    def find_element(self, _by, _value):
        raise Exception("no child")

    def get_attribute(self, name):
        attrs = {
            "id": "opt-3",
            "name": "grp-1",
            "type": "radio",
            "class": "",
            "aria-checked": "true" if self.selected else "false",
        }
        return attrs.get(name, "")


class _FakeDriver:
    def __init__(self, el):
        self.switch_to = _FakeSwitchTo()
        self._el = el

    def find_elements(self, by, value):
        if by == By.XPATH and value == "//*[@id='opt-3']":
            return [self._el]
        return []

    def find_element(self, by, value):
        if by == By.ID and value == "opt-3":
            return self._el
        raise Exception("not found")

    def execute_script(self, script, *args):
        if "checked=true" in script and args:
            args[0].selected = True
            return None
        if "getElementById" in script:
            return self._el.selected
        if "querySelector" in script:
            return self._el.selected
        return None


class _FakeDecipherClickableCell:
    tag_name = "div"

    def __init__(self, inp, *, mark_selected_on_click=True):
        self._inp = inp
        self._mark_selected_on_click = mark_selected_on_click
        self.click_count = 0

    @property
    def rect(self):
        return {"width": 60, "height": 20}

    def click(self):
        self.click_count += 1
        if self._mark_selected_on_click:
            self._inp.selected = True

    def is_displayed(self):
        return True

    def get_attribute(self, name):
        if name == "class":
            return "element clickableCell"
        return ""


class _FakeDecipherLabel:
    tag_name = "label"

    @property
    def rect(self):
        return {"width": 100, "height": 18}

    def click(self):
        raise Exception("label not interactable")

    def is_displayed(self):
        return True

    def find_element(self, _by, _value):
        raise Exception("no child")

    def get_attribute(self, name):
        if name == "for":
            return "opt-3"
        return ""


class _FakeDecipherDriver:
    def __init__(self, *, mark_selected_on_click=True):
        self.switch_to = _FakeSwitchTo()
        self._input = _FakeInput(selected=False)
        self._label = _FakeDecipherLabel()
        self._cell = _FakeDecipherClickableCell(
            self._input,
            mark_selected_on_click=mark_selected_on_click,
        )

    def find_elements(self, by, value):
        if by == By.XPATH and value == "//*[@id='opt-3']":
            return [self._label]
        return []

    def find_element(self, by, value):
        if by == By.ID and value == "opt-3":
            return self._input
        raise Exception("not found")

    def execute_script(self, script, *args):
        if "node.closest('.clickableCell')" in script:
            return self._cell
        if "const cell = arguments[0];" in script and "inp.checked" in script:
            return self._input.selected
        if "checked=true" in script and args:
            args[0].selected = True
            return None
        if "getBoundingClientRect" in script:
            return {"width": 100, "height": 20}
        if "getElementById" in script:
            return self._input.selected
        if "querySelector" in script:
            return self._input.selected
        return None


def test_apply_by_target_id_option_map_matches_typographic_apostrophe(monkeypatch):
    payload = {
        "kind": "group",
        "itype": "radio",
        "question": "CMIX",
        "option_xpath_map": {"j'y ai joue en ligne et en point de vente": "//*[@id='opt-3']"},
        "frame_chain": [],
    }
    monkeypatch.setattr(ad, "get_target", lambda _tid: payload)

    el = _FakeInput()
    driver = _FakeDriver(el)

    ok = ad._apply_by_target_id(
        driver,
        "tid-cmix",
        "radio",
        "J’y ai joue en ligne et en point de vente",
    )

    assert ok is True
    assert el.selected is True


def test_fold_norm_lc_normalizes_typographic_apostrophe():
    assert ad._fold_norm_lc("J’y ai joué") == ad._fold_norm_lc("J'y ai joue")


def test_apply_by_target_id_option_map_matches_frequency_unit_when_unique(monkeypatch):
    payload = {
        "kind": "group",
        "itype": "radio",
        "question": "CMIX frequency",
        "option_xpath_map": {
            "Au moins une fois par jour": "//*[@id='opt-day']",
            "Au moins une fois par semaine": "//*[@id='opt-week']",
            "Plusieurs fois par mois": "//*[@id='opt-month']",
            "Moins souvent/Jamais": "//*[@id='opt-never']",
        },
        "frame_chain": [],
    }
    monkeypatch.setattr(ad, "get_target", lambda _tid: payload)

    el = _FakeInput()
    driver = _FakeDriver(el)

    def _find_elements(by, value):
        if by == By.XPATH and value == "//*[@id='opt-week']":
            return [el]
        return []

    driver.find_elements = _find_elements

    ok = ad._apply_by_target_id(
        driver,
        "tid-cmix-frequency",
        "radio",
        "Plusieurs fois par semaine",
    )

    assert ok is True
    assert el.selected is True


def test_apply_by_target_id_checkbox_already_selected_skips_click(monkeypatch):
    payload = {
        "kind": "group",
        "itype": "checkbox",
        "question": "FocusVision hidden answers-list",
        "option_xpath_map": {"directement sur le site web de la marque": "//*[@id='opt-3']"},
        "frame_chain": [],
    }
    monkeypatch.setattr(ad, "get_target", lambda _tid: payload)

    el = _FakeInput(selected=True)
    driver = _FakeDriver(el)

    ok = ad._apply_by_target_id(
        driver,
        "tid-focusvision-checkbox",
        "checkbox",
        "Directement sur le site Web de la marque",
    )

    assert ok is True
    assert el.selected is True
    assert el.click_count == 0


def test_apply_by_target_id_decipher_fir_hidden_clicks_clickable_cell(monkeypatch):
    payload = {
        "kind": "group",
        "itype": "checkbox",
        "question": "Decipher hidden answers-list",
        "option_xpath_map": {"des barres de cereales": "//*[@id='opt-3']"},
        "frame_chain": [],
    }
    monkeypatch.setattr(ad, "get_target", lambda _tid: payload)

    driver = _FakeDecipherDriver(mark_selected_on_click=True)

    ok = ad._apply_by_target_id(driver, "tid-decipher", "checkbox", "Des barres de céréales")

    assert ok is True
    assert driver._cell.click_count == 1
    assert driver._input.selected is True


def test_apply_by_target_id_decipher_fir_hidden_requires_dom_state(monkeypatch):
    payload = {
        "kind": "group",
        "itype": "checkbox",
        "question": "Decipher hidden answers-list",
        "option_xpath_map": {"des barres de cereales": "//*[@id='opt-3']"},
        "frame_chain": [],
    }
    monkeypatch.setattr(ad, "get_target", lambda _tid: payload)

    driver = _FakeDecipherDriver(mark_selected_on_click=False)

    ok = ad._apply_by_target_id(driver, "tid-decipher", "checkbox", "Des barres de céréales")

    assert ok is False
    assert driver._cell.click_count == 1
    assert driver._input.selected is False
