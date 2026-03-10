from preselection import question_analyzer


def test_force_non_for_hardware_question_with_non_option():
    question = "Avez-vous une caméra ou un microphone pour cette étude ?"
    options = ["Oui", "Non"]

    assert question_analyzer._should_force_non_for_hardware_question(question, options)


def test_no_force_non_when_activation_word_present():
    question = "Autorisez-vous l'activation de la webcam pendant l'entretien ?"
    options = ["Oui", "Non"]

    assert not question_analyzer._should_force_non_for_hardware_question(question, options)


def test_no_force_non_when_non_option_missing():
    question = "Avez-vous une webcam disponible ?"
    options = ["Oui", "Peut-être"]

    assert not question_analyzer._should_force_non_for_hardware_question(question, options)


def test_no_force_non_for_non_hardware_question():
    question = "Quelle est votre tranche d'âge ?"
    options = ["18-24", "25-34", "Non"]

    assert not question_analyzer._should_force_non_for_hardware_question(question, options)
