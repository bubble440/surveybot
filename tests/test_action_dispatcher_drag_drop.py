from surveybot.Survey import action_dispatcher as ad


class _FakeImage:
    def __init__(self, alt, src):
        self._alt = alt
        self._src = src

    def get_attribute(self, name):
        if name == "alt":
            return self._alt
        if name == "src":
            return self._src
        return ""


class _FakeElement:
    def __init__(self, text="", attrs=None, images=None):
        self.text = text
        self._attrs = attrs or {}
        self._images = images or []

    def is_displayed(self):
        return True

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        if "img[alt=\"42\"]" in (value or ""):
            return [img for img in self._images if img.get_attribute("alt") == "42"]
        if value == "img":
            return self._images
        return []


class _FakeDriver:
    def __init__(self):
        self.title = _FakeElement("Veuillez déposer le numéro 42 dans la case vide:")
        self.drop_zone = _FakeElement()
        self.next_button = _FakeElement(attrs={"disabled": "true", "aria-disabled": "true", "class": "btn disabled"})
        self.draggables = [
            _FakeElement(images=[_FakeImage("42", "https://x/42.png")]),
        ]
        self.attempts = 0

    def find_element(self, by=None, value=None):
        if "#dropZoneList" in (value or ""):
            return self.drop_zone
        raise RuntimeError("not found")

    def find_elements(self, by=None, value=None):
        v = value or ""
        if "p.question-title" in v:
            return [self.title]
        if "[cdkdrag]" in v:
            return self.draggables
        if "Go to next question" in v:
            return [self.next_button]
        return []

    def execute_script(self, script, *_args):
        s = script or ""
        if "getBoundingClientRect" in s:
            self.attempts += 1
            if self.attempts == 2:
                self.next_button._attrs["disabled"] = ""
                self.next_button._attrs["aria-disabled"] = "false"
                self.next_button._attrs["class"] = "btn"
            return {
                "startX": 5,
                "startY": 5,
                "endX": 10,
                "endY": 10,
                "verified": True,
                "elementTag": "img",
                "elementId": "",
                "elementClass": "ng-star-inserted",
            }
        if "draggableInZone" in s:
            return False
        return None

    def execute_cdp_cmd(self, _name, _payload):
        return None


def test_handle_drag_drop_logic_attempt_budget_and_cta_once(monkeypatch):
    driver = _FakeDriver()
    calls = {"cta": 0}

    def _fake_cta(_driver):
        calls["cta"] += 1
        return True

    monkeypatch.setattr(ad.Survey.input_handler, "try_click_navigation_cta_any_context", _fake_cta)

    assert ad.handle_drag_drop_logic(driver) is True
    assert driver.attempts == 2
    assert calls["cta"] == 1
