import unittest

from surveybot.Management.guards.sensitive_question_guard import is_sensitive_question


class SensitiveQuestionGuardTests(unittest.TestCase):
    def test_drag_drop_wording_is_not_sensitive(self):
        question = "Veuillez glisser le numéro 42 dans la zone vide"
        self.assertFalse(is_sensitive_question(question))

    def test_captcha_wording_is_sensitive(self):
        question = "Veuillez résoudre le captcha avant de continuer"
        self.assertTrue(is_sensitive_question(question))


if __name__ == "__main__":
    unittest.main()
