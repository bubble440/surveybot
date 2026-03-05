from surveybot.Survey.batch_response_parser import parse_batch_response


def test_cmix_simple_grid_other_specify_forces_negative_option_from_pattern():
    raw = "Q1 //// group_other //// Régulièrement //// radio //// Autre, préciser"
    actions = parse_batch_response(
        raw,
        constraints={"Q1": 1},
        qid_meta={
            "Q1": {
                "question": "Autre, préciser",
                "itype": "radio",
                "target_id": "group_other",
                "options": ["Toujours", "Régulièrement", "Jamais"],
                "context": {"cmix_simple_grid": True},
            }
        },
    )

    assert [a["value"] for a in actions] == ["Jamais"]


def test_cmix_simple_grid_other_specify_falls_back_to_last_option_when_no_never_match():
    raw = "Q1 //// group_other //// Often //// radio //// Other, specify"
    actions = parse_batch_response(
        raw,
        constraints={"Q1": 1},
        qid_meta={
            "Q1": {
                "question": "Other, specify",
                "itype": "radio",
                "target_id": "group_other",
                "options": ["Often", "Sometimes", "Rarely"],
                "context": {"cmix_simple_grid": True, "has_other_specify_input": True},
            }
        },
    )

    assert [a["value"] for a in actions] == ["Rarely"]


def test_cmix_simple_grid_regular_row_is_not_overridden():
    raw = "Q1 //// group_sport //// Régulièrement //// radio //// Football"
    actions = parse_batch_response(
        raw,
        constraints={"Q1": 1},
        qid_meta={
            "Q1": {
                "question": "Football",
                "itype": "radio",
                "target_id": "group_sport",
                "options": ["Toujours", "Régulièrement", "Jamais"],
                "context": {"cmix_simple_grid": True, "subquestion_name": "sports_01"},
            }
        },
    )

    assert [a["value"] for a in actions] == ["Régulièrement"]
