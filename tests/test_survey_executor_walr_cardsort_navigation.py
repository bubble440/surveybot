from surveybot.Survey import survey_executor


class _FakeDriver:
    def __init__(self, has_cardsort_buttons: bool):
        self._has_cardsort_buttons = has_cardsort_buttons

    def find_elements(self, by=None, value=None):
        if value == "#cardSortContainer button.answer-button" and self._has_cardsort_buttons:
            return [object()]
        return []


def test_skip_post_actions_navigation_when_walr_context_flag_present():
    driver = _FakeDriver(has_cardsort_buttons=False)
    question_blocks = [{"context": {"walr_cardsort": True}}]

    assert survey_executor._should_skip_post_actions_navigation(driver, question_blocks) is True


def test_skip_post_actions_navigation_when_cardsort_dom_present_without_context_flag():
    driver = _FakeDriver(has_cardsort_buttons=True)
    question_blocks = [{"context": {"group_key": "radio:name:q1"}}]

    assert survey_executor._should_skip_post_actions_navigation(driver, question_blocks) is True


def test_do_not_skip_post_actions_navigation_without_walr_signal():
    driver = _FakeDriver(has_cardsort_buttons=False)
    question_blocks = [{"context": {"group_key": "radio:name:q1"}}]

    assert survey_executor._should_skip_post_actions_navigation(driver, question_blocks) is False
