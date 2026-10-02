from __future__ import annotations

import unittest
from unittest.mock import patch

from Survey.action_validator import validate_actions
from Survey.autofix.dom_replay_shim import load_static_driver


TARGET_ID = "group_e52f6cc1915e"
ACTION = {"qid": "Q1", "target_id": TARGET_ID, "itype": "radio", "value": "Un homme"}
PAYLOAD = {
    "kind": "group",
    "itype": "radio",
    "group_key": "radio:name:q1001",
    "option_xpath_map": {"un homme": "//*[@id='q1001_a1']"},
}
POST_ACTION_HTML = """<html><body>
<div class="question radio_question" id="question1001">
  <div class="radio_q_text">Etes-vous...?</div>
  <div class="answer_options answer_options1001">
    <div class="option_radio"></div><div class="option_label"><span>Un homme</span></div>
    <input class="radioQT" type="checkbox" id="q1001_a1" name="q1001" value="1">
  </div>
  <div class="answer_options answer_options1001">
    <div class="option_radio"></div><div class="option_label"><span>Une femme</span></div>
    <input class="radioQT" type="checkbox" id="q1001_a2" name="q1001" value="2">
  </div>
  <div class="answer_options answer_options1001">
    <div class="option_radio"></div>
    <div class="option_label"><span>Autre, merci de préciser:
      <input class="input_field_radio" type="text" id="t1001_4"></span></div>
    <input class="radioQT" type="checkbox" id="q1001_a4" name="q1001" value="4">
  </div>
</div></body></html>"""


class QtNativeCheckedExclusionTests(unittest.TestCase):
    def test_live_radioqt_checked_without_ui_marker_keeps_dispatcher_failure(self) -> None:
        # La propriété checked peut être vraie sans attribut HTML checked ni sélection UI.
        with patch("Survey.action_validator.get_target", return_value=PAYLOAD), patch(
            "Survey.action_validator.is_checked", return_value=True
        ):
            report = validate_actions(
                [ACTION], dispatcher_success=False, driver=load_static_driver(POST_ACTION_HTML)
            )
        self.assertEqual(report, {
            "stage": "action", "ok": False, "actions_count": 1,
            "dispatcher_success": False,
            "issues": [{"failure_type": "dispatcher_reported_failure", "actions_count": 1}],
        })

    def test_captured_checked_without_ui_marker_is_also_excluded(self) -> None:
        facts = {f"target:{TARGET_ID}:option:un homme": {
            "tag": "input", "className": "radioQT", "checked": True, "visible": False,
        }}
        with patch("Survey.action_validator.get_target", return_value=PAYLOAD):
            report = validate_actions(
                [ACTION], dispatcher_success=False,
                driver=load_static_driver(POST_ACTION_HTML), captured_option_states=facts,
            )
        self.assertEqual(report["issues"], [
            {"failure_type": "dispatcher_reported_failure", "actions_count": 1},
        ])

    def test_radioqt_with_ui_marker_keeps_existing_false_negative_result(self) -> None:
        selected_html = POST_ACTION_HTML.replace(
            'class="option_radio"', 'class="option_radio input_on"', 1
        )
        with patch("Survey.action_validator.get_target", return_value=PAYLOAD), patch(
            "Survey.action_validator.is_checked", return_value=True
        ):
            report = validate_actions(
                [ACTION], dispatcher_success=False, driver=load_static_driver(selected_html)
            )
        self.assertEqual(report["issues"], [{
            "failure_type": "dispatcher_false_negative", "target_id": TARGET_ID,
            "itype": "radio", "value": "Un homme", "dom_signal": "checkbox_radio_checked",
        }])

    def test_other_radio_keeps_existing_native_checked_result(self) -> None:
        other_payload = {
            "kind": "group", "itype": "radio", "group_key": "radio:name:q1001",
            "option_xpath_map": {"un homme": "//*[@id='q1001_a1']"},
        }
        other_html = """<html><body><label>Un homme</label>
            <input type="radio" id="q1001_a1" name="q1001"></body></html>"""
        with patch("Survey.action_validator.get_target", return_value=other_payload), patch(
            "Survey.action_validator.is_checked", return_value=True
        ):
            report = validate_actions(
                [ACTION], dispatcher_success=False, driver=load_static_driver(other_html)
            )
        self.assertEqual(report["issues"][0]["dom_signal"], "checkbox_radio_checked")

    def test_checkboxqt_uses_the_same_scoped_exclusion(self) -> None:
        action = {"target_id": "group_checkbox", "itype": "checkbox", "value": "Option A"}
        payload = {
            "kind": "group", "itype": "checkbox", "group_key": "checkbox:name:q2001",
            "option_xpath_map": {"option a": "//*[@id='q2001_a1']"},
        }
        html = """<html><body><div class="question checkbox_question">
            <div class="answer_options"><div class="option_checkbox"></div>
            <div class="option_label">Option A</div>
            <input class="checkboxQT" type="checkbox" id="q2001_a1" name="q2001"></div>
            </div></body></html>"""
        with patch("Survey.action_validator.get_target", return_value=payload), patch(
            "Survey.action_validator.is_checked", return_value=True
        ):
            report = validate_actions(
                [action], dispatcher_success=False, driver=load_static_driver(html)
            )
        self.assertEqual(report["issues"], [
            {"failure_type": "dispatcher_reported_failure", "actions_count": 1},
        ])

    def test_dispatcher_success_contract_is_unchanged(self) -> None:
        with patch("Survey.action_validator.get_target", return_value=PAYLOAD):
            report = validate_actions(
                [ACTION], dispatcher_success=True, driver=load_static_driver(POST_ACTION_HTML)
            )
        self.assertEqual(report, {
            "stage": "action", "ok": True, "actions_count": 1,
            "dispatcher_success": True, "issues": [],
        })


if __name__ == "__main__":
    unittest.main()
