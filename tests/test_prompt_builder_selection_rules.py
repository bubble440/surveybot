from surveybot.Survey.prompt_builder import (
    _has_explicit_multi_indicator,
    _selection_rule_for_block,
    _tier_entry_option,
    _explicit_exact_count_from_question,
    build_batch_prompt,
    expand_question_blocks_for_batch,
)


def test_detects_explicit_multi_indicators_fr_and_en():
    assert _has_explicit_multi_indicator("Vous pouvez sélectionner plusieurs réponses.")
    assert _has_explicit_multi_indicator("Vous pouvez choisir plusieurs réponses parmi celles proposées")
    assert _has_explicit_multi_indicator("Cochez tout ce qui s’applique.")
    assert _has_explicit_multi_indicator("Select all that apply")


def test_selection_rule_reads_multi_instruction_from_context_when_question_is_plain():
    block = {
        "question": "Pourquoi avez-vous changé de banque principale ?",
        "itype": "checkbox",
        "options": ["A", "B", "C"],
        "context": {
            "instruction_text": "Vous pouvez choisir plusieurs réponses parmi celles proposées",
        },
    }

    assert _selection_rule_for_block(block) == "multi_1_to_3"


def test_batch_prompt_applies_per_qid_range_rule_without_using_max_as_exact_count():
    blocks = [
        {
            "question": "Vous pouvez donner autant de réponses que vous le souhaitez.",
            "itype": "checkbox",
            "options": ["A", "B", "C", "D"],
            "max_select": 16,
            "min_select": 1,
            "target_id": "group_multi",
        },
        {
            "question": "Quel est votre genre ?",
            "itype": "radio",
            "options": ["Homme", "Femme"],
            "max_select": 1,
            "min_select": 1,
            "target_id": "group_single",
        },
    ]

    prompt = build_batch_prompt(blocks)

    assert "selection_rule: Pour QID=Q1, renvoyer entre 1 et 16 valeur(s) séparée(s) par |" in prompt
    assert "selection_rule: Pour QID=Q2, renvoyer EXACTEMENT 1 valeur" in prompt
    assert "Si plusieurs valeurs sont nécessaires, les séparer UNIQUEMENT par \"|\"." in prompt


def test_tier_entry_option_uses_first_choice_of_last_quarter_for_non_frequency_scales():
    assert _tier_entry_option([str(i) for i in range(1, 10)])[0] == 7
    assert _tier_entry_option([str(i) for i in range(1, 9)])[0] == 6
    assert _tier_entry_option([str(i) for i in range(1, 5)])[0] == 3
    assert _tier_entry_option(["a"])[0] == 1
    assert _tier_entry_option(["a", "b"])[0] == 2
    assert _tier_entry_option(["a", "b", "c"])[0] == 3


def test_tier_entry_option_prefers_top_third_for_frequency_scales():
    options = [
        "Plusieurs fois par jour",
        "Tous les jours",
        "La plupart des jours (4 à 6 fois par semaine)",
        "Plusieurs fois par semaine (2-3 fois)",
        "Environ une fois par semaine",
        "Plusieurs fois ce mois-ci",
        "Une fois ce mois-ci",
        "Je n'en ai pas acheté/bu au cours du mois dernier",
        "Je ne sais pas/je ne m’en souviens pas",
    ]

    k, picked = _tier_entry_option(options)

    assert 1 <= k <= 3
    assert picked in options[:3]


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
    assert "matrix_answer_format: row_label || col_label (single) ; row_label || col1|col2|col3 (matrix checkbox multi-colonnes ; row obligatoire, jamais col seule)" in prompt


def test_batch_prompt_exposes_decipher_table_text_row_label_only_when_flagged():
    blocks = [
        {
            "question": "Y compris vous-même, combien de personnes vivent actuellement dans votre foyer ?",
            "itype": "text",
            "options": [],
            "target_id": "single_8d704689c6ca",
            "context": {
                "row_label": "Adultes ou enfants de 18 ans ou plus",
                "decipher_table_text_rows": True,
            },
        },
        {
            "question": "Question texte standard",
            "itype": "text",
            "options": [],
            "target_id": "single_standard",
            "context": {
                "row_label": "Ce libellé ne doit pas apparaître",
                "decipher_table_text_rows": False,
            },
        },
    ]

    prompt = build_batch_prompt(blocks)

    assert "row_label: Adultes ou enfants de 18 ans ou plus" in prompt
    assert "row_label: Ce libellé ne doit pas apparaître" not in prompt


def test_expand_question_blocks_for_batch_splits_matrix_rows_into_distinct_entries():
    blocks = [
        {
            "question": "Où avez-vous acheté chacun de ces produits ?",
            "itype": "matrix",
            "options": ["En ligne", "En magasin"],
            "max_select": 12,
            "target_id": "group_matrix",
            "context": {
                "matrix_rows": ["Whey", "Créatine"],
            },
        }
    ]

    expanded = expand_question_blocks_for_batch(blocks)

    assert len(expanded) == 2
    assert expanded[0]["context"]["matrix_active_row"] == "Whey"
    assert expanded[1]["context"]["matrix_active_row"] == "Créatine"
    assert expanded[0]["max_select"] == 1
    assert expanded[1]["max_select"] == 1


def test_expand_question_blocks_for_batch_keeps_already_scoped_matrix_unchanged():
    blocks = [
        {
            "question": "Test matrix row active",
            "itype": "matrix",
            "options": ["A", "B"],
            "max_select": 1,
            "target_id": "group_matrix_active",
            "context": {
                "matrix_rows": ["Ligne 1", "Ligne 2"],
                "matrix_active_row": "Ligne 1",
            },
        }
    ]

    expanded = expand_question_blocks_for_batch(blocks)

    assert len(expanded) == 1
    assert expanded[0]["context"]["matrix_active_row"] == "Ligne 1"


def test_batch_prompt_matrix_active_row_requires_column_only_value():
    blocks = [
        {
            "question": "Où avez-vous acheté chacun de ces produits ?",
            "itype": "matrix",
            "options": ["En ligne, sur Amazon", "En magasin, dans un supermarché"],
            "max_select": 12,
            "target_id": "group_matrix_active",
            "context": {
                "matrix_rows": ["Whey protéines", "créatine en poudre"],
                "matrix_active_row": "créatine en poudre",
            },
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "matrix_active_row_value_rule: valeur DOIT contenir UNIQUEMENT la/les colonne(s), sans row_label" in prompt
    assert "selection_rule: Pour QID=Q1, renvoyer entre 1 et 12 valeur(s) colonne(s) séparée(s) par | pour matrix_active_row." in prompt


def test_detects_explicit_exact_count_from_text_fr_and_en():
    assert _explicit_exact_count_from_question("Merci de sélectionner les deux réponses pertinentes") == 2
    assert _explicit_exact_count_from_question("Please select exactly 2 answers") == 2


def test_batch_prompt_requires_exactly_two_values_for_exact_count_checkbox():
    blocks = [
        {
            "question": "Quels sont les deux animaux parmi les propositions suivantes ? Merci de sélectionner les deux réponses pertinentes.",
            "itype": "checkbox",
            "options": ["Train", "Ours", "Chaise", "Canard", "Piano"],
            "max_select": 2,
            "min_select": 2,
            "target_id": "group_animaux",
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "selection_rule: Pour QID=Q1, renvoyer EXACTEMENT 2 valeur(s) séparée(s) par |" in prompt


def test_batch_prompt_requires_four_digit_year_for_birth_year_questions():
    blocks = [
        {
            "question": "Quelle est votre année de naissance ?",
            "itype": "text",
            "options": [],
            "max_select": 1,
            "min_select": 1,
            "target_id": "single_birth_year",
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "réponds UNIQUEMENT avec une année sur 4 chiffres (YYYY), jamais un âge" in prompt



def test_batch_prompt_forces_accept_on_consent_modal_radio():
    blocks = [
        {
            "question": "Merci de répondre à cette question",
            "itype": "radio",
            "options": [
                "JE CONSENS et continue l'enquête",
                "JE NE CONSENS PAS et quitte l'enquête",
            ],
            "max_select": 1,
            "target_id": "group_consent",
            "context": {"consent_modal_radio": True},
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "selection_rule: CONSENT_ACCEPT strict" in prompt
    assert "allowed_values_strict: JE CONSENS et continue l'enquête" in prompt


def test_batch_prompt_forces_self_for_household_decision_maker_question_fr():
    blocks = [
        {
            "question": "Au sein de votre foyer qui serait le plus susceptible de choisir l’application de paris sportif ou de poker à utiliser ?",
            "itype": "radio",
            "options": [
                "Principalement moi",
                "Principalement quelqu'un d'autre",
            ],
            "max_select": 1,
            "min_select": 1,
            "target_id": "group_household_dm",
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "selection_rule: HOUSEHOLD_DECISION_MAKER_SELF strict -> répondre EXACTEMENT avec 'Principalement moi'" in prompt
    assert "allowed_values_strict: Principalement moi" in prompt


def test_batch_prompt_does_not_force_household_rule_without_self_option():
    blocks = [
        {
            "question": "Au sein de votre foyer, qui décide le plus souvent des abonnements ?",
            "itype": "radio",
            "options": [
                "Mon conjoint",
                "Quelqu'un d'autre",
            ],
            "max_select": 1,
            "min_select": 1,
            "target_id": "group_household_dm_no_self",
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "HOUSEHOLD_DECISION_MAKER_SELF" not in prompt
