from surveybot.Survey.prompt_builder import (
    _has_explicit_multi_indicator,
    _selection_rule_for_block,
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
