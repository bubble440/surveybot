from surveybot.Survey.batch_response_parser import (
    _selection_bounds_for_qid,
    parse_batch_response,
)


def test_selection_bounds_checkbox_uses_min_and_max_range():
    assert _selection_bounds_for_qid("Q1", raw_max=4, qmeta={"itype": "checkbox", "min_select": 2}) == (2, 4)
    assert _selection_bounds_for_qid("Q1", raw_max=4, qmeta={"itype": "checkbox"}) == (1, 4)
    assert _selection_bounds_for_qid("Q1", raw_max=4, qmeta={"itype": "radio", "min_select": 0}) == (1, 1)


def test_parser_truncates_when_too_many_values_for_qid():
    raw = "Q1 //// group_a //// A|B|C|D|E //// checkbox //// Question"
    actions = parse_batch_response(
        raw,
        constraints={"Q1": 2},
        qid_meta={
            "Q1": {
                "itype": "checkbox",
                "target_id": "group_a",
                "question": "Question",
                "options": ["A", "B", "C", "D", "E"],
                "min_select": 1,
                "max_select": 2,
            }
        },
    )

    assert [a["value"] for a in actions] == ["A", "B"]


def test_parser_pads_to_min_select_when_too_few_values_for_qid():
    raw = "Q1 //// group_a //// B //// checkbox //// Question"
    actions = parse_batch_response(
        raw,
        constraints={"Q1": 4},
        qid_meta={
            "Q1": {
                "itype": "checkbox",
                "target_id": "group_a",
                "question": "Question",
                "options": ["A", "B", "C", "D"],
                "min_select": 2,
                "max_select": 4,
            }
        },
    )

    assert [a["value"] for a in actions] == ["B", "A"]
