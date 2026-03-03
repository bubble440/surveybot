from surveybot.Survey.batch_response_parser import sanitize_actions


def test_checkbox_bulk_policy_selects_90_percent_non_exclusive_first_dom_order():
    actions = [
        {
            "qid": "Q1",
            "target_id": "group_1",
            "value": "Entreprise ou service de marketing",
            "itype": "checkbox",
            "context": "Travaillez-vous...",
            "raw": "Q1 //// group_1 //// Entreprise ou service de marketing //// checkbox //// Travaillez-vous...",
        }
    ]

    qid_meta = {
        "Q1": {
            "question": "Travaillez-vous... Veuillez sélectionner toutes les réponses pertinentes.",
            "itype": "checkbox",
            "max_select": 9,
            "target_id": "group_1",
            "options": [
                "Agence de relations publiques",
                "Entreprise ou service de marketing",
                "Enseignement",
                "Agence de publicité",
                "Transport",
                "Fabricant, distributeur, grossiste ou détaillant",
                "Installations sportives ou de loisirs",
                "Société ou service d’études de marché",
                "Aucune de ces propositions",
            ],
        }
    }

    cleaned = sanitize_actions(actions, qid_meta=qid_meta)

    assert len(cleaned) == 8
    assert all(a["itype"] == "checkbox" for a in cleaned)
    assert [a["value"] for a in cleaned] == qid_meta["Q1"]["options"][:8]
    assert "Aucune de ces propositions" not in [a["value"] for a in cleaned]


def test_checkbox_bulk_policy_does_not_apply_without_explicit_multi_hint():
    actions = [
        {
            "qid": "Q1",
            "target_id": "group_1",
            "value": "Option B",
            "itype": "checkbox",
            "context": "Question générique",
            "raw": "Q1 //// group_1 //// Option B //// checkbox //// Question générique",
        }
    ]

    qid_meta = {
        "Q1": {
            "question": "Choisissez une option.",
            "itype": "checkbox",
            "max_select": 4,
            "target_id": "group_1",
            "options": ["Option A", "Option B", "Aucune de ces propositions"],
        }
    }

    cleaned = sanitize_actions(actions, qid_meta=qid_meta)

    assert cleaned == actions
