from surveybot.Survey.dom_analyzer import _dedupe_question_blocks


def test_dedupe_prefers_focusvision_when_group_key_and_options_match():
    polluted = {
        "itype": "radio",
        "question": "Vous vous identifiez comme... ? radio radio radio radio",
        "options": ["Homme", "Femme", "Non binaire", "Non listé"],
        "target_id": "group_bad",
        "context": {"kind": "group", "group_key": "radio:name:ans139.0.0"},
    }
    clean_focusvision = {
        "itype": "radio",
        "question": "Vous vous identifiez comme... ?",
        "options": ["Homme", "Femme", "Non binaire", "Non listé"],
        "target_id": "group_good",
        "context": {
            "kind": "group",
            "group_key": "radio:name:ans139.0.0",
            "focusvision_answers_list": True,
        },
    }

    out = _dedupe_question_blocks([polluted, clean_focusvision])

    assert len(out) == 1
    assert out[0]["target_id"] == "group_good"


def test_dedupe_prefers_less_polluted_question_without_focusvision_flag():
    polluted = {
        "itype": "radio",
        "question": "Q radio radio radio radio radio radio radio radio radio",
        "options": ["A", "B"],
        "target_id": "t1",
        "context": {"group_key": "radio:name:q1"},
    }
    cleaner = {
        "itype": "radio",
        "question": "Q",
        "options": ["A", "B"],
        "target_id": "t2",
        "context": {"group_key": "radio:name:q1"},
    }

    out = _dedupe_question_blocks([polluted, cleaner])

    assert len(out) == 1
    assert out[0]["target_id"] == "t2"
