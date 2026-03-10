import unittest

from surveybot.Management.guards.sensitive_question_guard import is_sensitive_question


class SensitiveQuestionGuardTests(unittest.TestCase):
    def test_drag_drop_wording_is_not_sensitive(self):
        question = "Veuillez glisser le numéro 42 dans la zone vide"
        self.assertFalse(is_sensitive_question(question))

    def test_captcha_wording_is_sensitive(self):
        question = "Veuillez résoudre le captcha avant de continuer"
        self.assertTrue(is_sensitive_question(question))

    def test_webcam_possession_question_is_not_sensitive(self):
        question = "Avez-vous une webcam et êtes-vous prêt(e) à l'utiliser à l'occasion d'une recherche en ligne ?"
        self.assertFalse(is_sensitive_question(question))

    def test_webcam_permission_question_is_sensitive(self):
        question = "Veuillez autoriser l'accès à votre webcam pour continuer"
        self.assertTrue(is_sensitive_question(question))

    def test_camera_activation_question_is_sensitive(self):
        question = "Activez votre caméra avant de démarrer l'étude"
        self.assertTrue(is_sensitive_question(question))


if __name__ == "__main__":
    unittest.main()
