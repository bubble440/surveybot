from preselection import question_analyzer


class _FakeDriver:
    def __init__(self):
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append((script, args))
        if script == "return arguments[0].outerHTML":
            return "<div>popup html</div>"
        if script == "return document.documentElement.outerHTML":
            return "<html>full dom</html>"
        raise AssertionError(f"Unexpected script: {script}")


class _FakeWait:
    def __init__(self, driver, timeout):
        self.driver = driver
        self.timeout = timeout

    def until(self, condition):
        self.driver.condition = condition
        return object()


def test_extract_popup_html_uses_popup_wrapper_selector(monkeypatch):
    driver = _FakeDriver()

    monkeypatch.setattr(question_analyzer, "WebDriverWait", _FakeWait)
    monkeypatch.setattr(
        question_analyzer.EC,
        "presence_of_element_located",
        lambda locator: ("presence_of_element_located", locator),
    )

    html = question_analyzer.extract_popup_html(driver)

    assert html == "<div>popup html</div>"
    assert driver.condition == (
        "presence_of_element_located",
        (question_analyzer.By.CSS_SELECTOR, "[data-test-id='ps-popup-content-wrapper']"),
    )


def test_extract_popup_html_falls_back_to_full_dom_on_failure(monkeypatch):
    driver = _FakeDriver()

    class _FailingWait:
        def __init__(self, driver, timeout):
            pass

        def until(self, condition):
            raise RuntimeError("no popup")

    monkeypatch.setattr(question_analyzer, "WebDriverWait", _FailingWait)

    html = question_analyzer.extract_popup_html(driver)

    assert html == "<html>full dom</html>"
