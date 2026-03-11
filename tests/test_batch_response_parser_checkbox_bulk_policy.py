import datetime

from surveybot.Survey.batch_response_parser import sanitize_actions


def test_checkbox_bulk_policy_caps_selection_to_max_select_first_dom_order():
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
            "max_select": 3,
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

    assert len(cleaned) == 3
    assert all(a["itype"] == "checkbox" for a in cleaned)
    assert [a["value"] for a in cleaned] == qid_meta["Q1"]["options"][:3]
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


def test_sanitize_birth_year_text_converts_age_to_four_digit_year():
    actions = [
        {
            "qid": "Q1",
            "target_id": "single_007c206eb4da",
            "value": "25",
            "itype": "text",
            "context": "Quelle est votre année de naissance ? Saisissez une réponse numérique.",
            "raw": "Q1 //// single_007c206eb4da //// 25 //// text //// Quelle est votre année de naissance ?",
        }
    ]

    qid_meta = {
        "Q1": {
            "question": "Quelle est votre année de naissance ?",
            "itype": "text",
            "max_select": 1,
            "min_select": 1,
            "target_id": "single_007c206eb4da",
            "options": [],
        }
    }

    cleaned = sanitize_actions(actions, qid_meta=qid_meta)

    assert cleaned[0]["value"] == str(datetime.datetime.utcnow().year - 25)
    assert "sanitized_birth_year" in cleaned[0]["raw"]


def test_checkbox_bulk_policy_prefers_negative_exclusive_for_short_sector_screener():
    actions = [
        {
            "qid": "Q1",
            "target_id": "group_1",
            "value": "Agence de publicité",
            "itype": "checkbox",
            "context": "Travaillez-vous...",
            "raw": "Q1 //// group_1 //// Agence de publicité //// checkbox //// Travaillez-vous...",
        }
    ]

    qid_meta = {
        "Q1": {
            "question": "Travaillez-vous ou une personne de votre foyer travaille-t-elle pour une entreprise des secteurs d’activité suivants ? Veuillez sélectionner toutes les réponses pertinentes.",
            "itype": "checkbox",
            "max_select": 8,
            "target_id": "group_1",
            "options": [
                "Entreprise ou service de marketing",
                "Société ou service d’études de marché",
                "Fabrication de boissons gazeuses",
                "Agence de relations publiques",
                "Journalisme",
                "Fabrication de produits d’hygiène",
                "Agence de publicité",
                "Aucune de ces propositions",
            ],
        }
    }

    cleaned = sanitize_actions(actions, qid_meta=qid_meta)

    assert len(cleaned) == 1
    assert cleaned[0]["itype"] == "checkbox"
    assert cleaned[0]["value"] == "Aucune de ces propositions"
