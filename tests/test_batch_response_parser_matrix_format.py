from surveybot.Survey.batch_response_parser import parse_batch_response


def test_parse_matrix_value_extracts_row_and_col_labels():
    raw = "Q1 //// group_8cf616bcb0fc //// Crédit consommation || Transféré vers Revolut //// radio //// Vous avez changé de banque"
    constraints = {"Q1": 1}
    qid_meta = {
        "Q1": {
            "itype": "matrix",
            "target_id": "group_8cf616bcb0fc",
        }
    }

    actions = parse_batch_response(raw, constraints=constraints, qid_meta=qid_meta)

    assert len(actions) == 1
    assert actions[0]["matrix_row_label"] == "Crédit consommation"
    assert actions[0]["matrix_col_label"] == "Transféré vers Revolut"
    assert actions[0]["value"] == "Transféré vers Revolut"


def test_parse_matrix_value_without_separator_is_dropped():
    raw = "Q1 //// group_8cf616bcb0fc //// Transféré vers Revolut //// radio //// Vous avez changé de banque"
    constraints = {"Q1": 1}
    qid_meta = {
        "Q1": {
            "itype": "matrix",
            "target_id": "group_8cf616bcb0fc",
        }
    }

    actions = parse_batch_response(raw, constraints=constraints, qid_meta=qid_meta)

    assert actions == []
