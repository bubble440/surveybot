from surveybot.Survey.prompt_builder import build_batch_prompt
from surveybot.Survey.batch_response_parser import parse_batch_response


def test_batch_prompt_requires_pipe_for_multi_text_triplet():
    blocks = [
        {
            "question": "When were you born? Month Day Year",
            "itype": "text",
            "options": [],
            "max_select": 3,
            "target_id": "multi_ab9ddf03c44d",
            "context": {"kind": "multi_text"},
        }
    ]

    prompt = build_batch_prompt(blocks)

    assert "RÈGLE CHAMP MULTI-CASES" in prompt
    assert "EXACTEMENT max_select segments séparés par \"|\"" in prompt
    assert "CHAMP MULTI-CASES: fournir 3 valeurs séparées par |" in prompt


def test_parse_batch_response_normalizes_month_name_date_for_multi_text():
    raw = (
        "Q1 //// multi_xxx //// March 2, 2001 //// text //// "
        "When were you born? Month Day Year"
    )

    actions = parse_batch_response(raw, constraints={"Q1": 3})

    assert [a["value"] for a in actions] == ["03", "02", "2001"]


def test_parse_batch_response_does_not_invent_values_when_single_number_for_multi_text():
    raw = (
        "Q1 //// multi_xxx //// 25 //// text //// "
        "When were you born? Month Day Year"
    )

    actions = parse_batch_response(raw, constraints={"Q1": 3})

    assert len(actions) == 1
    assert actions[0]["value"] == "25"
