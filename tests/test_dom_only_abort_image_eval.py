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


def test_detect_image_only_unresolvable_dom_positive():
    driver = _FakeDriver(
        {
            "project": "FR278423108S07",
            "image_only_groups": [
                {
                    "groupKey": "radio::_Qpagers0rs1rs2rs3_Qrs1_C",
                    "optionCount": 2,
                    "imgHints": ["Homme.jpg", "Femme.jpg"],
                }
            ],
        },
        url="https://sb.ktrmr.com/mrIWeb/mrIWeb.srf",
    )

    is_match, reason, fingerprint = survey_executor._detect_image_only_unresolvable_dom(driver, question_blocks=[])

    assert is_match is True
    assert reason == "image_only_inputs"
    assert len(fingerprint) == 12


def test_detect_image_only_unresolvable_dom_skips_when_exploitable_radio_exists():
    driver = _FakeDriver(
        {
            "project": "FR278423108S07",
            "image_only_groups": [{"groupKey": "radio::g1", "optionCount": 2, "imgHints": []}],
        }
    )
    qbs = [{"itype": "radio", "options": [{"label": "A"}, {"label": "B"}]}]

    is_match, reason, fingerprint = survey_executor._detect_image_only_unresolvable_dom(driver, question_blocks=qbs)

    assert is_match is False
    assert reason == ""
    assert fingerprint == ""


def test_budgeted_soft_restart_for_image_only_inputs(monkeypatch):
    driver = _FakeDriver(
        {
            "project": "FR278423108S07",
            "image_only_groups": [{"groupKey": "radio::g1", "optionCount": 2, "imgHints": ["a.jpg"]}],
        },
        url="https://sb.ktrmr.com/mrIWeb/mrIWeb.srf",
    )

    calls = []

    class _Guard:
        def request_survey_restart(self, reason):
            calls.append(reason)

    import Management.guards.runtime_guard as runtime_guard
    monkeypatch.setattr(runtime_guard, "get_guard", lambda: _Guard())

    first = survey_executor._budgeted_soft_restart_for_image_only_inputs(driver, question_blocks=[])
    second = survey_executor._budgeted_soft_restart_for_image_only_inputs(driver, question_blocks=[])
    third = survey_executor._budgeted_soft_restart_for_image_only_inputs(driver, question_blocks=[])

    assert first == "restarted"
    assert second == "restarted"
    assert third == "budget_exhausted"
    assert len(calls) == 2
    assert all(r == "dom_only_abort:image_only_inputs" for r in calls)
