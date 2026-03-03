from surveybot.Survey import dom_classifier as dc


class _FakeYouGovDriver:
    def execute_script(self, script, *args):
        if "clone.querySelectorAll('script, style, noscript, template')" in script:
            return "veuillez choisir une réponse consentement"
        if 'fieldset.question-single' in script and 'YouGov single-choice question layout' in script:
            return True
        return False


def test_yougov_question_page_is_not_classified_as_consent_screen():
    driver = _FakeYouGovDriver()
    assert dc.is_consent_screen(driver) is False
