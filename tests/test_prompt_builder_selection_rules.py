from surveybot.Survey.prompt_builder import (
    _has_explicit_multi_indicator,
    _tier_entry_option,
    _selection_rule_for_block,
    _explicit_exact_count_from_question,
    build_batch_prompt,
)


def test_detects_explicit_multi_indicators_fr_and_en():
    assert _has_explicit_multi_indicator("Vous pouvez sélectionner plusieurs réponses.")
    assert _has_explicit_multi_indicator("Cochez tout ce qui s’applique.")
    assert _has_explicit_multi_indicator("Select all that apply")


def test_selection_rule_is_exactly_one_without_explicit_multi_indicator_for_radio():
    block = {
        "question": "Quel est votre genre ?",
        "itype": "radio",
        "options": ["Homme", "Femme"],
        "max_select": 2,
    }
    assert _selection_rule_for_block(block) == "exactly_1"


def test_batch_prompt_applies_multi_1_to_3_rule_only_when_text_explicitly_indicates_multi():
    blocks = [
        {
            "question": "Vous pouvez donner autant de réponses que vous le souhaitez.",
            "itype": "checkbox",
            "options": ["A", "B", "C", "D"],
            "max_select": 16,
            "target_id": "group_multi",
        },
        {
            "question": "Quel est votre genre ?",
            "itype": "radio",
            "options": ["Homme", "Femme"],
            "max_select": 1,
            "target_id": "group_single",
        },
    ]

    prompt = build_batch_prompt(blocks)

    assert "selection_rule: MULTI explicite -> choisir 1 à 3 options" in prompt
    assert "selection_rule: choisir EXACTEMENT 1 option" in prompt
    assert "Ne déduis PAS le multi-choix depuis le provider/source" in prompt


def test_tier_entry_option_uses_first_choice_of_last_quarter():
    assert _tier_entry_option([str(i) for i in range(1, 10)])[0] == 7
    assert _tier_entry_option([str(i) for i in range(1, 9)])[0] == 6
    assert _tier_entry_option([str(i) for i in range(1, 5)])[0] == 3
    assert _tier_entry_option(["a"])[0] == 1
    assert _tier_entry_option(["a", "b"])[0] == 2
    assert _tier_entry_option(["a", "b", "c"])[0] == 3


def test_batch_prompt_enforces_tier_entry_option_for_category_range_questions():
    blocks = [
        {
            "question": "Dans quelle tranche de taille d'entreprise vous situez-vous ?",
            "itype": "radio",
            "options": [
                "1-9 employés",
                "10-49 employés",
                "50-99 employés",
                "100-249 employés",
                "250-499 employés",
                "500-999 employés",
                "1000-4999 employés",
                "5000-19999 employés",
                "20000+ employés",
            ],
            "max_select": 1,
            "target_id": "company_size",
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "selection_rule: TIER_ENTRY strict -> répondre EXACTEMENT avec '1000-4999 employés'" in prompt
    assert "allowed_values_strict: 1000-4999 employés" in prompt
    assert "Tu dois répondre EXACTEMENT avec l'un des libellés suivants : {1000-4999 employés}" in prompt


def test_batch_prompt_exposes_matrix_row_labels_for_llm_context():
    blocks = [
        {
            "question": "Vous avez changé de banque principale : qu’avez-vous transféré ?",
            "itype": "matrix",
            "options": ["Transféré vers Revolut", "Laissé chez Société Générale"],
            "max_select": 4,
            "target_id": "group_8cf616bcb0fc",
            "context": {
                "matrix_rows": [
                    "Épargne",
                    "Crédit consommation",
                ]
            },
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "contexte: Vous avez changé de banque principale : qu’avez-vous transféré ?" in prompt
    assert "sous_questions_matrix: Épargne | Crédit consommation" in prompt
    assert "RÈGLE SPÉCIALE MATRICES (itype=matrix)" in prompt
    assert "Crédit consommation || Transféré vers Revolut" in prompt
    assert "matrix_answer_format: row_label || col_label (row obligatoire, jamais col seule)" in prompt


def test_detects_explicit_exact_count_from_text_fr_and_en():
    assert _explicit_exact_count_from_question("Merci de sélectionner les deux réponses pertinentes") == 2
    assert _explicit_exact_count_from_question("Please select exactly 2 answers") == 2


def test_selection_rule_prefers_exact_count_for_checkbox_when_question_requests_two_choices():
    block = {
        "question": "Quels sont les deux animaux ? Merci de sélectionner les deux réponses pertinentes.",
        "itype": "checkbox",
        "options": ["Train", "Ours", "Canard"],
        "max_select": 5,
    }
    assert _selection_rule_for_block(block) == "exactly_2"


def test_batch_prompt_requires_exactly_two_values_for_exact_count_checkbox():
    blocks = [
        {
            "question": "Quels sont les deux animaux parmi les propositions suivantes ? Merci de sélectionner les deux réponses pertinentes.",
            "itype": "checkbox",
            "options": ["Train", "Ours", "Chaise", "Canard", "Piano"],
            "max_select": 2,
            "target_id": "group_animaux",
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "selection_rule: choisir EXACTEMENT 2 option(s), séparées par |" in prompt
