from surveybot.Survey import dom_classifier as dc


class _SwitchTo:
    def default_content(self):
        return None

    def frame(self, _frame):
        return None


class _FakeDriver:
    def __init__(self, cardsort_active: bool):
        self.cardsort_active = cardsort_active
        self.switch_to = _SwitchTo()

    def find_elements(self, by=None, value=None):
        # Pas d'iframes dans ce test
        return []

    def execute_script(self, script, *args):
        if ".sq-cardsort-bucket" in script and ".sq-cardsort-card" in script:
            return self.cardsort_active

        if "clone.querySelectorAll('script, style, noscript, template')" in script:
            return "Vous avez terminé ! Merci pour votre participation"

        if "td.clickableCell" in script and "choice-option" in script:
            return False

        return False


def test_cardsort_active_page_is_not_classified_as_end_screen():
    driver = _FakeDriver(cardsort_active=True)
    assert dc.is_end_screen(driver) is False


def test_end_screen_still_detected_without_cardsort_widget():
    driver = _FakeDriver(cardsort_active=False)
    assert dc.is_end_screen(driver) is True
