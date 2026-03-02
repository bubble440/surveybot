from surveybot.Survey.dom_analyzer import (
    _prune_focusvision_auxiliary_openended_singles,
    _prune_focusvision_fragmented_groups,
)


def test_prune_focusvision_fragmented_groups_removes_single_option_fragments():
    q = "Travaillez-vous ... plusieurs réponses"
    rich = {
        "itype": "checkbox",
        "question": q,
        "options": ["Agriculture", "Santé", "Aucune de ces réponses"],
        "context": {"focusvision_answers_list": True, "group_key": "checkbox:name:ans1025.0"},
    }
    fragments = [
        {"itype": "checkbox", "question": q, "options": ["Agriculture"], "context": {"group_key": "checkbox:name:ans1025.0.6"}},
        {"itype": "checkbox", "question": q, "options": ["Santé"], "context": {"group_key": "checkbox:name:ans1025.0.3"}},
    ]
    unrelated = {
        "itype": "radio",
        "question": "Autre question",
        "options": ["Oui", "Non"],
        "context": {"group_key": "radio:name:q1"},
    }

    out = _prune_focusvision_fragmented_groups([*fragments, rich, unrelated])

    assert rich in out
    assert unrelated in out
    assert fragments[0] not in out
    assert fragments[1] not in out


def test_prune_focusvision_fragmented_groups_no_rich_marker_no_prune():
    q = "Même texte"
    b1 = {"itype": "checkbox", "question": q, "options": ["A"], "context": {"group_key": "checkbox:name:x.1"}}
    b2 = {"itype": "checkbox", "question": q, "options": ["A", "B"], "context": {"group_key": "checkbox:name:x"}}

    out = _prune_focusvision_fragmented_groups([b1, b2])

    assert out == [b1, b2]


def test_prune_focusvision_auxiliary_openended_singles_removes_matching_text_single():
    rich = {
        "itype": "radio",
        "question": "Quelle était votre ancienne banque principale ?",
        "options": ["Banque Populaire", "BforBank", "Je ne suis client d’aucune banque"],
        "context": {
            "focusvision_answers_list": True,
            "group_key": "radio:name:ans10221.0.0",
            "aux_openended_names": ["oe10221.1", "oe10221.2"],
        },
    }
    aux_text = {
        "itype": "text",
        "question": "Quelle était votre ancienne banque principale ? ...",
        "context": {"kind": "single", "name": "oe10221.1", "id": "oe10221.1"},
    }
    unrelated_text = {
        "itype": "text",
        "question": "Votre âge",
        "context": {"kind": "single", "name": "age", "id": "age"},
    }

    out = _prune_focusvision_auxiliary_openended_singles([rich, aux_text, unrelated_text])

    assert rich in out
    assert aux_text not in out
    assert unrelated_text in out
