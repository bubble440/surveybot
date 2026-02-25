import os
import sys
from pathlib import Path

from selenium.webdriver.common.by import By

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "surveybot"))

from surveybot.Survey import cta_handler


class _FakeElement:
    def __init__(self, text="", attrs=None, displayed=True, enabled=True):
        self.text = text
        self._attrs = attrs or {}
        self._displayed = displayed
        self._enabled = enabled
        self.clicked = 0

    def is_displayed(self):
        return self._displayed

    def is_enabled(self):
        return self._enabled

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def click(self):
        self.clicked += 1

    def find_elements(self, by, value):
        return []


class _FakeDriver:
    def __init__(self, xpath_elements=None, css_elements=None):
        self.current_url = "https://sondage.selityvs.fr/survey"
        self._xpath_elements = xpath_elements or []
        self._css_elements = css_elements or {}

    def find_elements(self, by, value):
        if by == By.XPATH:
            return self._xpath_elements
        if by == By.CSS_SELECTOR:
            return self._css_elements.get(value, [])
        return []

    def execute_script(self, script, *args):
        return None


def test_try_click_navigation_cta_detects_tabindex_suivant(monkeypatch):
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    suivant = _FakeElement(
        text="Suivant",
        attrs={"tabindex": "0", "class": "r-1i6wzkk r-lrvibr", "role": ""},
    )
    driver = _FakeDriver(xpath_elements=[suivant])

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert suivant.clicked == 1


def test_try_click_navigation_cta_skips_long_text_tabindex_wrapper(monkeypatch):
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    wrapper = _FakeElement(
        text="Quel âge as-tu ? Réponse libre. Suivant Politique de confidentialité",
        attrs={"tabindex": "0", "class": "r-13awgt0", "role": ""},
    )
    vrai_cta = _FakeElement(
        text="Suivant",
        attrs={"tabindex": "0", "class": "r-1i6wzkk", "role": ""},
    )
    driver = _FakeDriver(xpath_elements=[wrapper, vrai_cta])

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert wrapper.clicked == 0
    assert vrai_cta.clicked == 1


def test_click_with_intercept_does_not_fallback_to_real_click_when_arm_fails(monkeypatch):
    monkeypatch.setenv("CTA_INTERCEPT_ONLY", "1")

    el = _FakeElement(text="Suivant")
    driver = _FakeDriver()

    monkeypatch.setattr(cta_handler, "arm_interceptor", lambda _d: False)
    monkeypatch.setattr(
        cta_handler,
        "_probe_interceptor_state",
        lambda _d: {"hasState": False, "installed": False, "armed": False, "armedOk": False},
    )

    ok = cta_handler._click_with_intercept(driver, el)

    assert ok is False
    assert el.clicked == 0
