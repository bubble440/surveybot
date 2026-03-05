from surveybot.Survey.dom_selection_rules import compute_checkbox_max_select


def test_checkbox_max_select_respects_explicit_exact_count():
    question = "Quels sont les TROIS enjeux les plus importants auxquels la France est confrontée aujourd’hui ?"
    options = [str(i) for i in range(12)]

    assert compute_checkbox_max_select(options, question) == 3


def test_checkbox_max_select_unchanged_without_explicit_exact_count():
    question = "Quels enjeux sont importants aujourd'hui ?"
    options = [str(i) for i in range(12)]

    assert compute_checkbox_max_select(options, question) == 12


def test_checkbox_max_select_unchanged_for_select_all_that_apply():
    question = "Sélectionnez toutes les réponses qui s'appliquent."
    options = [str(i) for i in range(12)]

    assert compute_checkbox_max_select(options, question) == 12
