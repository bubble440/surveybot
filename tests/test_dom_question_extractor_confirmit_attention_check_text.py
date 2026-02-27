from surveybot.Survey.dom_question_extractor import _extract_question_from_container


class _Container:
    def __init__(self, text: str):
        self.text = text


def test_extract_question_preserves_option_keyword_inside_instruction_sentence():
    container = _Container(
        """
        Cette question est un peu différente. Veuillez sélectionner la couleur violet parmi les options ci-dessous.
        Orange
        Vert
        Bleu
        Violet
        Jaune
        """
    )

    question = _extract_question_from_container(
        container,
        options=["Orange", "Vert", "Bleu", "Violet", "Jaune"],
    )

    assert "couleur violet" in question.lower()
    assert "veuillez" in question.lower()
