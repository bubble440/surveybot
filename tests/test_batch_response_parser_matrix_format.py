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


def test_parse_matrix_value_accepts_matrix_itype_from_llm():
    raw = "Q1 //// group_046fe0b2d616 //// Amazon Prime Video || Très favorable //// matrix //// Dans quelle mesure..."
    constraints = {"Q1": 1}
    qid_meta = {
        "Q1": {
            "itype": "matrix",
            "target_id": "group_046fe0b2d616",
        }
    }

    actions = parse_batch_response(raw, constraints=constraints, qid_meta=qid_meta)

    assert len(actions) == 1
    assert actions[0]["itype"] == "matrix"
    assert actions[0]["matrix_row_label"] == "Amazon Prime Video"
    assert actions[0]["matrix_col_label"] == "Très favorable"


def test_parse_matrix_value_supports_multiple_columns_for_same_row():
    raw = "Q1 //// group_d83814e34163 //// Whey protéines || En ligne, sur Amazon|En magasin, chez Decathlon, Intersport ou  Fitness Boutique //// matrix //// Où avez-vous acheté chacun de ces produits ?"
    constraints = {"Q1": 12}
    qid_meta = {
        "Q1": {
            "itype": "matrix",
            "target_id": "group_d83814e34163",
        }
    }

    actions = parse_batch_response(raw, constraints=constraints, qid_meta=qid_meta)

    assert len(actions) == 2
    assert actions[0]["matrix_row_label"] == "Whey protéines"
    assert actions[0]["matrix_col_label"] == "En ligne, sur Amazon"
    assert actions[1]["matrix_row_label"] == "Whey protéines"
    assert actions[1]["matrix_col_label"] == "En magasin, chez Decathlon, Intersport ou  Fitness Boutique"


def test_parse_matrix_value_accepts_repeated_row_pair_compat_mode():
    raw = "Q1 //// group_d83814e34163 //// Whey protéines || En ligne, sur Amazon || Whey protéines || En magasin, chez Decathlon, Intersport ou  Fitness Boutique //// matrix //// Où avez-vous acheté chacun de ces produits ?"
    constraints = {"Q1": 12}
    qid_meta = {
        "Q1": {
            "itype": "matrix",
            "target_id": "group_d83814e34163",
        }
    }

    actions = parse_batch_response(raw, constraints=constraints, qid_meta=qid_meta)

    assert len(actions) == 2
    assert actions[0]["matrix_row_label"] == "Whey protéines"
    assert actions[1]["matrix_row_label"] == "Whey protéines"
