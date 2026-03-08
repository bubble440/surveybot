import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "surveybot"))

from Survey import survey_executor


class _FakeDriver:
    def __init__(self, payload, url="https://core-gp.firstinsight.com/gp/rank"):
        self._payload = payload
        self.current_url = url

    def execute_script(self, script, *args):
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
    assert reason == "image_only_wrapped_inputs"
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
    assert all(r == "dom_only_abort:image_only_wrapped_inputs" for r in calls)


def test_detect_image_only_unresolvable_dom_clickable_icons_positive():
    driver = _FakeDriver(
        {
            "project": "FR278423108S07",
            "image_only_groups": [],
            "clickable_image_only_groups": [
                {
                    "containerSig": "DIV|container_rs1|__flexgrid_row|",
                    "optionCount": 2,
                    "textlessCount": 2,
                    "hasImageNodeCount": 2,
                    "hasBackgroundImageCount": 0,
                }
            ],
            "has_question_hint": True,
            "clickable_visible_count": 2,
        }
    )

    is_match, reason, fingerprint = survey_executor._detect_image_only_unresolvable_dom(driver, question_blocks=[])

    assert is_match is True
    assert reason == "image_only_clickable_options"
    assert len(fingerprint) == 12


def test_detect_image_only_unresolvable_dom_clickable_icons_requires_question_hint():
    driver = _FakeDriver(
        {
            "project": "FR278423108S07",
            "image_only_groups": [],
            "clickable_image_only_groups": [
                {"containerSig": "DIV|container_rs1|__flexgrid_row|", "optionCount": 2, "textlessCount": 2}
            ],
            "has_question_hint": False,
            "clickable_visible_count": 2,
        }
    )

    is_match, reason, fingerprint = survey_executor._detect_image_only_unresolvable_dom(driver, question_blocks=[])

    assert is_match is False
    assert reason == ""
    assert fingerprint == ""


def test_budgeted_soft_restart_for_clickable_image_only_inputs(monkeypatch):
    driver = _FakeDriver(
        {
            "project": "FR278423108S07",
            "image_only_groups": [],
            "clickable_image_only_groups": [
                {"containerSig": "DIV|container_rs1|__flexgrid_row|", "optionCount": 2, "textlessCount": 2}
            ],
            "has_question_hint": True,
            "clickable_visible_count": 2,
        },
        url="https://sb.ktrmr.com/mrIWeb/mrIWeb.srf",
    )

    calls = []

    class _Guard:
        def request_survey_restart(self, reason):
            calls.append(reason)

    import Management.guards.runtime_guard as runtime_guard
    monkeypatch.setattr(runtime_guard, "get_guard", lambda: _Guard())

    result = survey_executor._budgeted_soft_restart_for_image_only_inputs(driver, question_blocks=[])

    assert result == "restarted"
    assert calls == ["dom_only_abort:image_only_clickable_options"]


def test_detect_image_only_unresolvable_dom_visual_challenge_instruction():
    driver = _FakeDriver(
        {
            "project": "FR278423108S07",
            "image_only_groups": [],
            "clickable_image_only_groups": [],
            "has_visual_challenge_instruction": True,
            "visible_img_count": 12,
            "visible_bg_tile_count": 0,
        }
    )

    is_match, reason, fingerprint = survey_executor._detect_image_only_unresolvable_dom(driver, question_blocks=[])

    assert is_match is True
    assert reason == "image_selection_challenge"
    assert len(fingerprint) == 12


def test_budgeted_soft_restart_visual_challenge_uses_explicit_reason(monkeypatch):
    driver = _FakeDriver(
        {
            "project": "FR278423108S07",
            "image_only_groups": [],
            "clickable_image_only_groups": [],
            "has_visual_challenge_instruction": True,
            "visible_img_count": 10,
            "visible_bg_tile_count": 0,
        },
        url="https://ex-plorsurvey.com/survey/selfserve/210e/260106",
    )

    calls = []

    class _Guard:
        def request_survey_restart(self, reason):
            calls.append(reason)

    import Management.guards.runtime_guard as runtime_guard
    monkeypatch.setattr(runtime_guard, "get_guard", lambda: _Guard())

    first = survey_executor._budgeted_soft_restart_for_image_only_inputs(driver, question_blocks=[])
    second = survey_executor._budgeted_soft_restart_for_image_only_inputs(driver, question_blocks=[])

    assert first == "restarted"
    assert second == "budget_exhausted"
    assert calls == ["dom_only_abort:image_selection_challenge"]


def test_detect_open_text_embedded_image_unresolvable_dom_positive():
    driver = _FakeDriver(
        {
            "textarea_count": 1,
            "other_text_input_count": 0,
            "radio_count": 0,
            "checkbox_count": 0,
            "select_count": 0,
            "ta_image_count": 1,
            "large_embedded_ta_image_count": 1,
        },
        url="https://edgesurvey.innovatemr.net/survey/qualitative_questions",
    )
    qbs = [{"itype": "textarea", "target_id": "single_ta_1", "question": ""}]

    is_match, reason, fingerprint = survey_executor._detect_open_text_embedded_image_unresolvable_dom(
        driver,
        qbs,
    )

    assert is_match is True
    assert reason == "open_text_embedded_image"
    assert len(fingerprint) == 12


def test_detect_open_text_embedded_image_unresolvable_dom_no_match_when_multiple_inputs():
    driver = _FakeDriver(
        {
            "textarea_count": 1,
            "other_text_input_count": 0,
            "radio_count": 1,
            "checkbox_count": 0,
            "select_count": 0,
            "ta_image_count": 1,
            "large_embedded_ta_image_count": 1,
        }
    )
    qbs = [{"itype": "textarea", "target_id": "single_ta_1", "question": ""}]

    is_match, reason, fingerprint = survey_executor._detect_open_text_embedded_image_unresolvable_dom(
        driver,
        qbs,
    )

    assert is_match is False
    assert reason == ""
    assert fingerprint == ""


def test_budgeted_soft_restart_for_open_text_embedded_image(monkeypatch):
    driver = _FakeDriver(
        {
            "textarea_count": 1,
            "other_text_input_count": 0,
            "radio_count": 0,
            "checkbox_count": 0,
            "select_count": 0,
            "ta_image_count": 1,
            "large_embedded_ta_image_count": 1,
        },
        url="https://edgesurvey.innovatemr.net/survey/qualitative_questions",
    )
    qbs = [{"itype": "textarea", "target_id": "single_ta_1", "question": ""}]

    calls = []

    class _Guard:
        def request_survey_restart(self, reason):
            calls.append(reason)

    import Management.guards.runtime_guard as runtime_guard
    monkeypatch.setattr(runtime_guard, "get_guard", lambda: _Guard())

    first = survey_executor._budgeted_soft_restart_for_open_text_embedded_image(driver, qbs)
    second = survey_executor._budgeted_soft_restart_for_open_text_embedded_image(driver, qbs)

    assert first == "restarted"
    assert second == "budget_exhausted"
    assert calls == ["dom_only_abort:open_text_embedded_image"]
