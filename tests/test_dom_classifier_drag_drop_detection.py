from surveybot.Survey import dom_classifier as dc


class _FakeElement:
    def __init__(self, text="", displayed=True):
        self.text = text
        self._displayed = displayed

    def is_displayed(self):
        return self._displayed

    def get_attribute(self, name):
        return ""


class _FakeDriver:
    def __init__(self, title_text, drag_count=3, drop_count=1):
        self._title_text = title_text
        self._drag_count = drag_count
        self._drop_count = drop_count

    def find_elements(self, by=None, value=None):
        if "p.question-title" in (value or ""):
            return [_FakeElement(self._title_text)]
        if "[cdkdrag]" in (value or ""):
            return [_FakeElement() for _ in range(self._drag_count)]
        if "[cdkdroplist]" in (value or "") or "#dropZoneList" in (value or ""):
            return [_FakeElement() for _ in range(self._drop_count)]
        return []


def test_is_drag_drop_requires_instruction_and_dom_markers():
    driver = _FakeDriver("Veuillez déposer le numéro 42 dans la case vide:")
    assert dc.is_drag_drop(driver) is True


def test_is_drag_drop_rejects_when_no_instruction_verb():
    driver = _FakeDriver("Veuillez sélectionner le numéro 42")
    assert dc.is_drag_drop(driver) is False
