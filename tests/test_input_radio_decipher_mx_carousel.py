from selenium.webdriver.common.by import By

from Survey import input_radio as ir


class _FakeScope:
    def __init__(self, stage=None):
        self._stage = stage

    def find_element(self, by, value):
        if by == By.XPATH and "mx-carouselapp-container" in value and self._stage is not None:
            return self._stage
        raise Exception("not found")


class _FakeDriver:
    def __init__(self, click_result=None, active_after="r2"):
        self.click_result = click_result
        self.active_after = active_after
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append((script, args))
        if "scale_not_found" in script:
            return self.click_result
        if "swiper-slide-active" in script:
            return self.active_after
        return None


class _WaitOK:
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, cond):
        if not cond(self.driver):
            raise Exception("condition not met")
        return True


class _WaitTimeout:
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, cond):
        raise Exception("timeout")


def test_click_decipher_mx_carousel_radio_requires_mx_stage(monkeypatch):
    monkeypatch.setattr(ir, "find_questions_container", lambda *_: _FakeScope(stage=None))
    driver = _FakeDriver(click_result={"ok": True})

    assert ir.click_decipher_mx_carousel_radio(driver, "Non", "Le bâtiment") is False


def test_click_decipher_mx_carousel_radio_clicks_scale_when_stage_present(monkeypatch):
    monkeypatch.setattr(ir, "find_questions_container", lambda *_: _FakeScope(stage=object()))
    monkeypatch.setattr(ir, "WebDriverWait", _WaitOK)
    driver = _FakeDriver(click_result={"ok": True, "itemCount": 1, "activeBefore": "r1"})

    assert ir.click_decipher_mx_carousel_radio(driver, "Non", "Le bâtiment") is True


def test_click_decipher_mx_carousel_radio_fails_when_autonext_does_not_advance(monkeypatch):
    monkeypatch.setattr(ir, "find_questions_container", lambda *_: _FakeScope(stage=object()))
    monkeypatch.setattr(ir, "WebDriverWait", _WaitTimeout)
    driver = _FakeDriver(click_result={"ok": True, "itemCount": 4, "activeBefore": "r1"})

    assert ir.click_decipher_mx_carousel_radio(driver, "Non", "Le bâtiment") is False
