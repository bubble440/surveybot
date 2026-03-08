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


def test_parse_matrix_value_accepts_column_only_when_matrix_active_row_is_known():
    raw = "Q2 //// group_d83814e34163 //// En ligne, sur Amazon|En magasin, dans un supermarché ou un hypermarché //// matrix //// Où avez-vous acheté chacun de ces produits ?"
    constraints = {"Q2": 12}
    qid_meta = {
        "Q2": {
            "itype": "matrix",
            "target_id": "group_d83814e34163",
            "context": {
                "matrix_active_row": "créatine en poudre",
            },
        }
    }

    actions = parse_batch_response(raw, constraints=constraints, qid_meta=qid_meta)

    assert len(actions) == 2
    assert actions[0]["matrix_row_label"] == "créatine en poudre"
    assert actions[0]["matrix_col_label"] == "En ligne, sur Amazon"
    assert actions[1]["matrix_row_label"] == "créatine en poudre"
    assert actions[1]["matrix_col_label"] == "En magasin, dans un supermarché ou un hypermarché"


def test_parse_matrix_value_does_not_truncate_actions_when_constraint_is_per_row():
    raw = "Q1 //// group_rank //// Autres pays européens || 1|Le Maroc || 2 //// matrix //// Selon vous, d'où viennent les AVOCATS de meilleure qualité, et quel est le deuxième ?"
    constraints = {"Q1": 1}
    qid_meta = {
        "Q1": {
            "itype": "matrix",
            "target_id": "group_rank",
        }
    }

    actions = parse_batch_response(raw, constraints=constraints, qid_meta=qid_meta)

    assert len(actions) == 2
    assert actions[0]["matrix_row_label"] == "Autres pays européens"
    assert actions[0]["matrix_col_label"] == "1"
    assert actions[1]["matrix_row_label"] == "Le Maroc"
    assert actions[1]["matrix_col_label"] == "2"
