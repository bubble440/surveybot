from surveybot.Survey import dom_analyzer as da


def test_dedupe_same_group_key_merges_options_and_keeps_clean_question():
    group_key = "checkbox:name:ans10518.0"
    blocks = [
        {
            "question": "Pourquoi avez-vous changé de banque principale ? Vous pouvez choisir plusieurs réponses parmi celles proposées Un élément déclencheur personnel:",
            "itype": "checkbox",
            "options": [
                "Prix trop élevé",
                "Qualité du service insuffisante",
                "Autre événement personnel - préciser",
                "Autre - préciser",
            ],
            "max_select": 4,
            "target_id": "group_rich",
            "context": {"kind": "group", "group_key": group_key},
        },
        {
            "question": "Pourquoi avez-vous changé de banque principale ?",
            "itype": "checkbox",
            "options": [
                "Prix trop élevé",
                "Qualité du service insuffisante",
            ],
            "max_select": 2,
            "target_id": "group_clean",
            "context": {"kind": "group", "group_key": group_key},
        },
    ]

    deduped = da._dedupe_question_blocks(blocks)

    assert len(deduped) == 1
    block = deduped[0]
    assert block["target_id"] == "group_rich"
    assert da._norm(block["question"]) == da._norm("Pourquoi avez-vous changé de banque principale ?")
    assert [da._norm(o) for o in block["options"]] == [
        da._norm("Prix trop élevé"),
        da._norm("Qualité du service insuffisante"),
        da._norm("Autre événement personnel - préciser"),
        da._norm("Autre - préciser"),
    ]
    assert block["max_select"] == 4


def test_dedupe_named_group_prefers_focusvision_block_without_option_union():
    group_key = "radio:name:ans899.0.0"
    blocks = [
        {
            "question": "Whey protéines en poudre",
            "itype": "radio",
            "options": [
                "Une fois par semaine ou plus",
                "Toutes les 2 semaines",
            ],
            "max_select": 1,
            "target_id": "group_focusvision",
            "context": {
                "kind": "group",
                "group_key": group_key,
                "focusvision_answers_list": True,
            },
        },
        {
            "question": "Whey protéines en poudre",
            "itype": "radio",
            "options": [
                "Whey protéines en poudre Une fois par semaine ou plus",
                "Whey protéines en poudre Toutes les 2 semaines",
            ],
            "max_select": 1,
            "target_id": "group_generic",
            "context": {"kind": "group", "group_key": group_key},
        },
    ]

    deduped = da._dedupe_question_blocks(blocks)

    assert len(deduped) == 1
    block = deduped[0]
    assert block["target_id"] == "group_focusvision"
    assert [da._norm(o) for o in block["options"]] == [
        da._norm("Une fois par semaine ou plus"),
        da._norm("Toutes les 2 semaines"),
    ]
