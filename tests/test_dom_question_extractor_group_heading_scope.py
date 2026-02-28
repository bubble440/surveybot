import inspect

from surveybot.Survey import dom_question_extractor as dqe


def test_group_heading_extractor_uses_shared_group_scope_without_data_survey_uid():
    src = inspect.getsource(dqe._find_group_heading_text_near_element)

    assert "sharedGroupSelectors" in src
    assert "#profiler-choice" in src
    assert ".choice-list-full" in src
    assert "[data-survey-uid], fieldset, .choice-list-full" not in src
