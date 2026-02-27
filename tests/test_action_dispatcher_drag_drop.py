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
            _FakeElement(images=[_FakeImage("66", "https://x/66.png")]),
            _FakeElement(images=[_FakeImage("42", "https://x/42.png")]),
            _FakeElement(images=[_FakeImage("59", "https://x/59.png")]),
        ]

    def find_elements(self, by=None, value=None):
        v = value or ""
        if "p.question-title" in v:
            return [self.title]
        if "[cdkdrag]" in v:
            return self.draggables
        if "[cdkdroplist]" in v or "#dropZoneList" in v:
            return [self.drop_zone]
        if "Go to next question" in v:
            return [self.next_button]
        return []

    def execute_script(self, _script, *_args):
        if "js_pointer_drag_failed" in (_script or ""):
            return False
        if "mkMouse('mousedown'" in (_script or ""):
            self.next_button._attrs["disabled"] = ""
            self.next_button._attrs["aria-disabled"] = "false"
            self.next_button._attrs["class"] = "btn"
            return True
        return None


def test_handle_drag_drop_logic_enables_next():
    driver = _FakeDriver()

    assert ad.handle_drag_drop_logic(driver) is True
    assert driver.next_button.get_attribute("disabled") == ""
