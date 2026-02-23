import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "surveybot"))

from Survey import survey_executor


class _FakeDriver:
    def __init__(self, payload, url="https://core-gp.firstinsight.com/gp/rank"):
        self._payload = payload
        self.current_url = url

    def execute_script(self, script):
        return self._payload


def test_detect_rate_rank_image_eval_dom_positive():
    driver = _FakeDriver(
        {
            "button_texts": ["N’AIME PAS", "AIME"],
            "has_product_page": True,
            "has_rate_rank_hint": False,
            "has_visual_media_hint": True,
        }
    )

    is_match, reason = survey_executor._detect_rate_rank_image_eval_dom(driver)

    assert is_match is True
    assert reason == "rate_rank_image_eval_pair_buttons"


def test_budgeted_dom_only_abort_image_eval_once_then_exhausted(monkeypatch):
    driver = _FakeDriver(
        {
            "button_texts": ["N’AIME PAS", "AIME"],
            "has_product_page": True,
            "has_rate_rank_hint": True,
            "has_visual_media_hint": True,
        }
    )

    calls = []

    class _Guard:
        def request_survey_restart(self, reason):
            calls.append(reason)

    import Management.guards.runtime_guard as runtime_guard
    monkeypatch.setattr(runtime_guard, "get_guard", lambda: _Guard())

    first = survey_executor._budgeted_dom_only_abort_for_image_eval(driver)
    second = survey_executor._budgeted_dom_only_abort_for_image_eval(driver)

    assert first == "restarted"
    assert second == "budget_exhausted"
    assert len(calls) == 1
    assert calls[0].startswith("dom_only_abort_image_eval:")
