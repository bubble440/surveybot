from surveybot.Survey.dom_analyzer import _prune_focusvision_fragmented_groups


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
