from surveybot.Survey.dom_extractors_decipher import _logical_answers_list_group_name


def test_keeps_real_name_when_only_one_raw_name_exists():
    raw_names = {"ans10538.0.0"}

    out = _logical_answers_list_group_name("ans10538.0.0", raw_names)

    assert out == "ans10538.0.0"


def test_collapses_to_base_when_multiple_sibling_names_exist():
    raw_names = {"ans10518.0.0", "ans10518.0.1", "ans10518.0.2"}

    out = _logical_answers_list_group_name("ans10518.0.1", raw_names)

    assert out == "ans10518.0"
