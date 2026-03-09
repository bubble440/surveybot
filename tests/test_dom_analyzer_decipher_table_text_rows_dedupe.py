from surveybot.Survey.dom_analyzer import _dedupe_question_blocks


def test_dedupe_keeps_distinct_decipher_table_text_rows():
    blocks = [
        {
            "question": "Y compris vous-même, combien de personnes vivent actuellement dans votre foyer ?",
            "itype": "text",
            "options": [],
            "target_id": "single_row_1",
            "context": {
                "kind": "single",
                "name": "QDCHILDRENR1",
                "id": "QDCHILDRENR1",
                "row_label": "Adultes ou enfants de 18 ans ou plus",
                "decipher_table_text_rows": True,
            },
        },
        {
            "question": "Y compris vous-même, combien de personnes vivent actuellement dans votre foyer ?",
            "itype": "text",
            "options": [],
            "target_id": "single_row_2",
            "context": {
                "kind": "single",
                "name": "QDCHILDRENR2",
                "id": "QDCHILDRENR2",
                "row_label": "Enfants de 12 à 18 ans",
                "decipher_table_text_rows": True,
            },
        },
        {
            "question": "Y compris vous-même, combien de personnes vivent actuellement dans votre foyer ?",
            "itype": "text",
            "options": [],
            "target_id": "single_row_3",
            "context": {
                "kind": "single",
                "name": "QDCHILDRENR3",
                "id": "QDCHILDRENR3",
                "row_label": "Enfants de 6 à 12 ans",
                "decipher_table_text_rows": True,
            },
        },
        {
            "question": "Y compris vous-même, combien de personnes vivent actuellement dans votre foyer ?",
            "itype": "text",
            "options": [],
            "target_id": "single_row_4",
            "context": {
                "kind": "single",
                "name": "QDCHILDRENR4",
                "id": "QDCHILDRENR4",
                "row_label": "Enfants de 0 à 6 ans",
                "decipher_table_text_rows": True,
            },
        },
    ]

    deduped = _dedupe_question_blocks(blocks)

    assert len(deduped) == 4
    assert {b["target_id"] for b in deduped} == {
        "single_row_1",
        "single_row_2",
        "single_row_3",
        "single_row_4",
    }
