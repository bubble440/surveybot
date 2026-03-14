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
        self.rect = {"x": 0, "y": 0, "width": 100, "height": 30}
        self.tag_name = "div"

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

    def find_element(self, by, value):
        raise Exception("not found")




class _FakeActionChains:
    def __init__(self, driver):
        self._target = None

    def move_to_element(self, el):
        self._target = el
        return self

    def click_and_hold(self, el=None):
        if el is not None:
            self._target = el
        return self

    def pause(self, _seconds):
        return self

    def release(self, el=None):
        if el is not None:
            self._target = el
        return self

    def perform(self):
        if self._target is not None:
            self._target.clicked += 1
        return None

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


class _FailingActionChains(_FakeActionChains):
    def perform(self):
        raise Exception("action chain failed")




def test_iter_iframes_safe_includes_legacy_frame_elements():
    legacy_frame = _FakeElement(text="", attrs={"id": "mainFrame"})
    legacy_frame.tag_name = "frame"
    legacy_frame.rect = {"x": 0, "y": 0, "width": 1024, "height": 768}

    driver = _FakeDriver(css_elements={"iframe, frame": [legacy_frame]})

    frames = cta_handler._iter_iframes_safe(driver)

    assert len(frames) == 1
    assert frames[0] is legacy_frame


def test_iter_iframes_safe_keeps_legacy_frame_when_not_reported_visible():
    legacy_frame = _FakeElement(text="", attrs={"id": "mainFrame", "src": "survey/page"}, displayed=False)
    legacy_frame.tag_name = "frame"
    legacy_frame.rect = {"x": 0, "y": 0, "width": 0, "height": 0}

    driver = _FakeDriver(css_elements={"iframe, frame": [legacy_frame]})

    frames = cta_handler._iter_iframes_safe(driver)

    assert len(frames) == 1
    assert frames[0] is legacy_frame

def test_try_click_navigation_cta_detects_tabindex_suivant(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
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
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
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


def test_try_click_navigation_cta_skips_internal_task_carousel_arrows(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    carousel_arrow = _FakeElement(
        text="",
        attrs={"data-cy": "right-arrow", "aria-label": "next", "class": "_chevron-right"},
    )
    page_cta = _FakeElement(
        text="Suivant",
        attrs={"class": "_next-button", "data-cy": "next-button"},
    )
    counter = _FakeElement(text="3/12")

    driver = _FakeDriver(
        xpath_elements=[carousel_arrow, page_cta],
        css_elements={'p[data-cy="task-counter"]': [counter]},
    )

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert carousel_arrow.clicked == 0
    assert page_cta.clicked == 1


def test_try_click_navigation_cta_detects_oc_overlay_continue(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    oc_overlay = _FakeElement(
        text="",
        attrs={"id": "oc_in4", "class": "ocin", "onmousedown": "oc._.C[4].O.ToggSel();"},
    )
    oc_label = _FakeElement(text="CONTINUER")

    driver = _FakeDriver(
        xpath_elements=[oc_overlay],
        css_elements={"#oc_t4": [oc_label]},
    )

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert oc_overlay.clicked == 1


def test_press_click_release_uses_native_click_for_forsta_next_button(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FailingActionChains)

    el = _FakeElement(
        text=">>",
        attrs={"class": "cf-navigation__button cf-navigation-next"},
    )
    el.tag_name = "button"

    click_ok, release_sent = cta_handler._press_click_release(_FakeDriver(), el)

    assert click_ok is True
    assert release_sent is False
    assert el.clicked == 1


def test_press_click_release_uses_native_click_for_forsta_ok_button(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FailingActionChains)

    el = _FakeElement(
        text="OK",
        attrs={"class": "cf-navigation__button cf-navigation-ok"},
    )
    el.tag_name = "button"

    click_ok, release_sent = cta_handler._press_click_release(_FakeDriver(), el)

    assert click_ok is True
    assert release_sent is False
    assert el.clicked == 1


def test_click_with_intercept_returns_false_when_no_progress(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    el = _FakeElement(text="Suivant", attrs={"class": "next"})
    el.tag_name = "div"

    marker = {"url": "https://example.test/q1", "txt": "Question 1", "qNodes": 3}
    monkeypatch.setattr(cta_handler, "_dom_progress_marker", lambda _d: dict(marker))

    ok = cta_handler._click_with_intercept(_FakeDriver(), el)

    assert ok is False
    assert el.clicked == 2


def test_try_click_navigation_cta_prioritizes_forsta_real_next_button(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    wrapper = _FakeElement(
        text="Question … Suivant",
        attrs={"tabindex": "0", "class": "focus-wrapper"},
    )
    wrapper.tag_name = "div"

    forsta_next = _FakeElement(
        text=">>",
        attrs={"class": "cf-navigation__button cf-navigation-next", "aria-label": ""},
    )
    forsta_next.tag_name = "button"

    driver = _FakeDriver(
        xpath_elements=[wrapper],
        css_elements={"button.cf-navigation__button.cf-navigation-next, button.cf-navigation__button.cf-navigation-ok": [forsta_next]},
    )

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert forsta_next.clicked == 1
    assert wrapper.clicked == 0


def test_try_click_navigation_cta_prioritizes_forsta_ok_button(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    wrapper = _FakeElement(
        text="Question ... Suivant",
        attrs={"tabindex": "0", "class": "focus-wrapper"},
    )
    wrapper.tag_name = "div"

    forsta_ok = _FakeElement(
        text="OK",
        attrs={"class": "cf-navigation__button cf-navigation-ok", "aria-label": ""},
    )
    forsta_ok.tag_name = "button"

    driver = _FakeDriver(
        xpath_elements=[wrapper],
        css_elements={
            "button.cf-navigation__button.cf-navigation-next, button.cf-navigation__button.cf-navigation-ok": [forsta_ok]
        },
    )

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert forsta_ok.clicked == 1
    assert wrapper.clicked == 0

def test_try_click_navigation_cta_detects_consent_confirm_button(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    confirm_btn = _FakeElement(
        text="Confirmez",
        attrs={"id": "consent-button-confirm", "class": "consent-form-button"},
    )
    confirm_btn.tag_name = "button"

    driver = _FakeDriver(css_elements={"#consent-button-confirm": [confirm_btn]})

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert confirm_btn.clicked == 1


def test_try_click_navigation_cta_detects_icon_only_div_next(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    next_div = _FakeElement(
        text="",
        attrs={"id": "next", "class": "next arrow_on"},
    )
    next_div.tag_name = "div"
    next_div.rect = {"x": 1200, "y": 700, "width": 72, "height": 60}

    driver = _FakeDriver(css_elements={".footer #next, #next.next": [next_div]})

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert next_div.clicked == 1


def test_try_click_navigation_cta_encuesta_prefers_footer_next_over_done_button(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    encuesta_done = _FakeElement(
        text="SUIVANT",
        attrs={"class": "encuesta__done-button primary v-btn"},
    )
    encuesta_done.tag_name = "button"

    footer_next = _FakeElement(
        text="SUIVANT",
        attrs={"class": "ee__button--next primary v-btn"},
    )
    footer_next.tag_name = "button"

    driver = _FakeDriver(
        xpath_elements=[encuesta_done, footer_next],
        css_elements={
            "button.encuesta__done-button": [encuesta_done],
            "button.ee__button--next": [footer_next],
        },
    )

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert encuesta_done.clicked == 0
    assert footer_next.clicked == 1


def test_try_click_navigation_cta_detects_li_next_button_submitform(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    li_next = _FakeElement(
        text="Suivant",
        attrs={"id": "next", "class": "next-button", "onclick": "submitForm(''); return false;"},
    )
    li_next.tag_name = "li"

    driver = _FakeDriver(xpath_elements=[li_next])

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert li_next.clicked == 1


def test_try_click_navigation_cta_decipher_gridclick_clicks_widget_arrow(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    hidden_btn_continue = _FakeElement(
        text="",
        attrs={"id": "btn_continue", "class": "hidden"},
        displayed=False,
    )
    gridclick_marker = _FakeElement(text="", attrs={"class": "gridclick-container"})
    widget_arrow = _FakeElement(
        text="",
        attrs={"class": "nav-container ion-android-arrow-forward"},
    )
    widget_arrow.tag_name = "div"

    driver = _FakeDriver(
        css_elements={
            "input#btn_continue": [hidden_btn_continue],
            "div.gridclick-container": [gridclick_marker],
            "div.next-nav.active > div.nav-container[class*='ion-android-arrow-forward']": [widget_arrow],
        }
    )

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert widget_arrow.clicked == 1


def test_try_click_navigation_cta_decipher_gridclick_widget_not_ready_returns_false(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    hidden_btn_continue = _FakeElement(
        text="",
        attrs={"id": "btn_continue", "class": "hidden"},
        displayed=False,
    )
    gridclick_marker = _FakeElement(text="", attrs={"class": "gridclick-container"})
    generic_suivant = _FakeElement(
        text="Suivant",
        attrs={"tabindex": "0", "class": "generic-next"},
    )

    driver = _FakeDriver(
        xpath_elements=[generic_suivant],
        css_elements={
            "input#btn_continue": [hidden_btn_continue],
            "div.gridclick-container": [gridclick_marker],
            "div.next-nav.active > div.nav-container[class*='ion-android-arrow-forward']": [],
        },
    )

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is False
    assert generic_suivant.clicked == 0


def test_try_click_navigation_cta_detects_qualtrics_fake_next_button_span(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    hidden_input_next = _FakeElement(
        text="",
        attrs={"id": "NextButton", "type": "button", "aria-disabled": "true"},
        displayed=False,
    )
    fake_next_span = _FakeElement(
        text=">>",
        attrs={"id": "NextButton", "class": "fakeNextButton", "title": ">>"},
    )
    fake_next_span.tag_name = "span"

    driver = _FakeDriver(
        xpath_elements=[hidden_input_next, fake_next_span],
    )

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert hidden_input_next.clicked == 0
    assert fake_next_span.clicked == 1


def test_try_click_navigation_cta_detects_intellisurvey_empty_value_submit(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    intro_submit = _FakeElement(
        text="",
        attrs={
            "id": "contbtn",
            "name": "contbtn",
            "type": "submit",
            "value": "",
            "class": "i-button i-contbtn",
        },
    )
    intro_submit.tag_name = "input"

    driver = _FakeDriver(xpath_elements=[intro_submit])

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert intro_submit.clicked == 1


def test_try_click_navigation_cta_skips_inline_hidden_candidates(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    hidden_submit = _FakeElement(
        text="",
        attrs={
            "id": "contbtn",
            "name": "contbtn",
            "type": "submit",
            "class": "i-button i-contbtn",
            "style": "opacity: 0; visibility: hidden;",
        },
    )
    hidden_submit.tag_name = "input"

    driver = _FakeDriver(xpath_elements=[hidden_submit])

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is False
    assert hidden_submit.clicked == 0


def test_try_click_navigation_cta_prioritizes_mriweb_real_submit(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    vue_next = _FakeElement(
        text="Suivant",
        attrs={"id": "NextBtn", "class": "clickable NavBtn vClick"},
    )
    vue_next.tag_name = "span"

    mriweb_submit = _FakeElement(
        text="Suivant",
        attrs={"type": "submit", "name": "_NNext", "class": "mrNext", "value": "Suivant"},
    )
    mriweb_submit.tag_name = "input"

    driver = _FakeDriver(xpath_elements=[vue_next, mriweb_submit])

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is True
    assert mriweb_submit.clicked == 1
    assert vue_next.clicked == 0


def test_try_click_navigation_cta_ignores_zero_score_candidate(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    unknown_candidate = _FakeElement(
        text="",
        attrs={"id": "mystery", "class": ""},
    )
    unknown_candidate.tag_name = "div"

    driver = _FakeDriver(xpath_elements=[unknown_candidate])

    ok = cta_handler.try_click_navigation_cta(driver)

    assert ok is False
    assert unknown_candidate.clicked == 0


def test_try_click_navigation_cta_any_context_resets_to_default_content(monkeypatch):
    monkeypatch.setattr(cta_handler, "ActionChains", _FakeActionChains)
    monkeypatch.delenv("CTA_INTERCEPT_ONLY", raising=False)

    class _FrameAwareDriver:
        def __init__(self, frame_el, cta_el):
            self.current_url = "https://insights.ipsosinteractive.com/survey"
            self._frame = frame_el
            self._cta = cta_el
            self._ctx = "stale_inner_context"
            self.switch_to = self._Switch(self)

        class _Switch:
            def __init__(self, outer):
                self._outer = outer

            def default_content(self):
                self._outer._ctx = "default"

            def frame(self, fr):
                if fr is self._outer._frame:
                    self._outer._ctx = "mriweb_frame"
                else:
                    raise Exception("unknown frame")

        def find_elements(self, by, value):
            if by == By.CSS_SELECTOR and value == "iframe, frame":
                return [self._frame] if self._ctx == "default" else []

            if by == By.XPATH:
                return [self._cta] if self._ctx == "mriweb_frame" else []

            return []

        def execute_script(self, script, *args):
            if script == "return window.location.href":
                return self.current_url
            if script == "return document.body ? (document.body.innerText || '') : ''":
                return ""
            if script == "return document.querySelectorAll('input,textarea,select,button').length":
                return 1
            return None

    legacy_frame = _FakeElement(text="", attrs={"src": "/mrIWeb/mrIWeb.dll?mode=post"})
    legacy_frame.tag_name = "frame"
    legacy_frame.rect = {"x": 0, "y": 0, "width": 1024, "height": 768}

    mriweb_submit = _FakeElement(
        text="Suivant",
        attrs={"type": "submit", "name": "_NNext", "class": "mrNext", "value": "Suivant"},
    )
    mriweb_submit.tag_name = "input"

    driver = _FrameAwareDriver(frame_el=legacy_frame, cta_el=mriweb_submit)

    ok = cta_handler.try_click_navigation_cta_any_context(driver)

    assert ok is True
    assert mriweb_submit.clicked == 1
