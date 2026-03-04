from surveybot.Survey.batch_response_parser import parse_batch_response


def test_parser_truncates_when_too_many_values_for_qid():
    raw = "Q1 //// group_a //// A|B|C|D //// checkbox //// Question"
    actions = parse_batch_response(
        raw,
        constraints={"Q1": 2},
        qid_meta={
            "Q1": {
                "itype": "checkbox",
                "target_id": "group_a",
                "question": "Question",
                "options": ["A", "B", "C", "D"],
            }
        },
    )

    assert [a["value"] for a in actions] == ["A", "B"]


def test_parser_pads_from_dom_options_when_too_few_values_for_qid():
    raw = "Q1 //// group_a //// B //// checkbox //// Question"
    actions = parse_batch_response(
        raw,
        constraints={"Q1": 3},
        qid_meta={
            "Q1": {
                "itype": "checkbox",
                "target_id": "group_a",
                "question": "Question",
                "options": ["A", "B", "C", "D"],
            }
        },
    )

    assert [a["value"] for a in actions] == ["B", "A", "C"]
