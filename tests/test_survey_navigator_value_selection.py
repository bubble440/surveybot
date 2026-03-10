from preselection import survey_navigator


class _FakeCard:
    def __init__(self, text: str, *, displayed: bool = True, enabled: bool = True, card_id: str = "x"):
        self.text = text
        self._displayed = displayed
        self._enabled = enabled
        self.id = card_id

    def is_displayed(self):
        return self._displayed

    def is_enabled(self):
        return self._enabled


def test_parse_reward_eur_supports_comma_and_dot():
    assert survey_navigator._parse_reward_eur("0,66 €") == 0.66
    assert survey_navigator._parse_reward_eur("€ 1.5") == 1.5


def test_parse_duration_min_supports_common_patterns():
    assert survey_navigator._parse_duration_min("22 min") == 22
    assert survey_navigator._parse_duration_min("6 minutes") == 6


def test_select_best_value_card_prefers_ratio(monkeypatch):
    cards = [
        _FakeCard("0,66 € 22 min", card_id="a"),
        _FakeCard("0,61 € 6 min", card_id="b"),
    ]
    monkeypatch.setattr(survey_navigator, "_find_survey_cards", lambda _driver: cards)

    best = survey_navigator._select_best_value_card(driver=None)

    assert best is cards[1]


def test_select_best_value_card_ignores_unparsable_or_unclickable(monkeypatch):
    cards = [
        _FakeCard("offre sans prix 10 min", card_id="a"),
        _FakeCard("0,50 € sans durée", card_id="b"),
        _FakeCard("0,90 € 9 min", displayed=False, card_id="c"),
    ]
    monkeypatch.setattr(survey_navigator, "_find_survey_cards", lambda _driver: cards)

    assert survey_navigator._select_best_value_card(driver=None) is None
