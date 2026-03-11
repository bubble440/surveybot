from Survey import dom_extractors_decipher as dd


class _FakeQuestion:
    def __init__(self, style: str):
        self._style = style
        self.find_element_calls = 0

    def get_attribute(self, name):
        if name == "style":
            return self._style
        return ""

    def find_element(self, by, value):
        self.find_element_calls += 1
        raise Exception("no answers")


class _FakeDriver:
    def __init__(self, questions):
        self._questions = questions

    def find_elements(self, by, value):
        return self._questions


def test_has_inline_display_none_detects_hidden_style():
    hidden = _FakeQuestion("color:red; display: none; opacity:1")
    visible = _FakeQuestion("color:red; opacity:1")

    assert dd._has_inline_display_none(hidden) is True
    assert dd._has_inline_display_none(visible) is False


def test_extract_focusvision_answers_list_groups_skips_inline_hidden_question():
    hidden_q = _FakeQuestion("display: none;")
    visible_q = _FakeQuestion("")
    driver = _FakeDriver([hidden_q, visible_q])

    blocks = dd._extract_focusvision_answers_list_groups(driver, frame_chain=[])

    assert blocks == []
    assert hidden_q.find_element_calls == 0
    assert visible_q.find_element_calls == 1
