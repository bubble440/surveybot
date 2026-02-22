import unittest

from selenium.webdriver.common.by import By

from surveybot.Management.guards.survey_difficulty_guard import detect_strict_survey


class _FakeElement:
    def __init__(self, displayed=True, width=100, height=30):
        self._displayed = displayed
        self.rect = {"width": width, "height": height}

    def is_displayed(self):
        return self._displayed

    @property
    def tag_name(self):
        return "div"

    def get_attribute(self, _name):
        return ""


class _FakeDriver:
    def __init__(self, text, by_selector=None, by_tag=None):
        self._text = text
        self._by_selector = by_selector or {}
        self._by_tag = by_tag or {}

    def execute_script(self, script, *args):
        if "document.body.innerText" in script:
            return self._text
        return ""

    def find_elements(self, by, value):
        if by == By.CSS_SELECTOR:
            return self._by_selector.get(value, [])
        if by == By.TAG_NAME:
            return self._by_tag.get(value, [])
        return []


class SurveyDifficultyGuardTests(unittest.TestCase):
    def test_streaming_question_is_not_flagged_as_audio_video(self):
        page_text = (
            "Parmi les chaînes premium ou services de streaming suivants... "
            "Amazon Prime Video Disney+ HBO Hulu Netflix Showtime"
        )
        driver = _FakeDriver(text=page_text)

        is_strict, reason = detect_strict_survey(driver)

        self.assertFalse(is_strict)
        self.assertIsNone(reason)

    def test_microphone_permission_is_flagged(self):
        page_text = "Veuillez autoriser le micro et la caméra pour commencer l'enregistrement"
        driver = _FakeDriver(text=page_text)

        is_strict, reason = detect_strict_survey(driver)

        self.assertTrue(is_strict)
        self.assertEqual(reason, "audio_capture")

    def test_watch_video_instruction_with_video_tag_is_flagged(self):
        page_text = "Please watch the video before answering"
        driver = _FakeDriver(text=page_text, by_tag={"video": [_FakeElement()]})

        is_strict, reason = detect_strict_survey(driver)

        self.assertTrue(is_strict)
        self.assertEqual(reason, "audio_video_required")


if __name__ == "__main__":
    unittest.main()
