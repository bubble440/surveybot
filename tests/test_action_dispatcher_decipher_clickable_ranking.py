from selenium.webdriver.common.by import By

from surveybot.Survey import action_dispatcher as ad


class _FakeSwitchTo:
    def default_content(self):
        return None

    def frame(self, _frame):
        return None


class _FakeElement:
    tag_name = "div"

    def __init__(self):
        self.click_count = 0

    def click(self):
        self.click_count += 1

    def is_displayed(self):
        return True

    def get_attribute(self, _name):
        return ""


class _FakeDriver:
    def __init__(self, element):
        self.switch_to = _FakeSwitchTo()
        self._el = element
        self.scripts = []

    def find_elements(self, by, value):
        if by == By.XPATH and value == "//*[@id='statement_4']":
            return [self._el]
        return []

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if "getBoundingClientRect" in script:
            return {"width": 100, "height": 20}
        if "node.closest ? node.closest('.customItem')" in script:
            return True
        return None


def test_apply_by_target_id_decipher_clickable_ranking_single_click(monkeypatch):
    payload = {
        "kind": "group",
        "itype": "checkbox",
        "question": "Question ranking",
        "option_xpath_map": {"option a": "//*[@id='statement_4']"},
        "frame_chain": [],
        "decipher_clickable_ranking": True,
    }
    monkeypatch.setattr(ad, "get_target", lambda _tid: payload)

    el = _FakeElement()
    driver = _FakeDriver(el)

    ok = ad._apply_by_target_id(driver, "tid-ranking", "checkbox", "Option A")

    assert ok is True
    assert el.click_count == 1
    assert any("node.closest ? node.closest('.customItem')" in s for s in driver.scripts)
    assert all("inp.checked = true" not in s for s in driver.scripts)
