import inspect

from surveybot.Survey import dom_question_extractor as dqe


def test_group_heading_extractor_prioritizes_fieldset_legend():
    src = inspect.getsource(dqe._find_group_heading_text_near_element)

    assert "ancestor::fieldset[1]/legend[1]" in src
    assert "_is_question_text(legend_text)" in src
