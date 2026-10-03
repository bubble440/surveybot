from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from Survey.action_fix_hook import (
    ActionFixOutcome, observe_action_fix_successes, run_action_fix_hook,
)
from Survey.action_validator import validate_actions
from Survey.autofix.dom_replay_shim import load_static_driver
from Survey.autofix.failure_replay import _run_pipeline
from Survey.autofix.replay_browser import _action_outcome, _compare_validation


TARGET_ID = "group_e52f6cc1915e"
ACTION = {"qid": "Q1", "target_id": TARGET_ID, "itype": "radio", "value": "Un homme"}
# Forme du registre produite par dom_analyzer et DOM du case action QT réel ;
# la propriété native checked=True existe dans runtime_state.json, pas dans ce HTML.
PAYLOAD = {
    "kind": "group",
    "itype": "radio",
    "group_key": "radio:name:q1001",
    "question": "Question de référence",
    "option_xpath_map": {
        "un homme": "(//*[@id='q1001_a1']/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1]//*[contains(concat(' ', normalize-space(@class), ' '), ' option_radio ')][1] | //*[@id='q1001_a1']/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1])",
        "une femme": "(//*[@id='q1001_a2']/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1]//*[contains(concat(' ', normalize-space(@class), ' '), ' option_radio ')][1] | //*[@id='q1001_a2']/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1])",
        "autre, merci de préciser:": "(//*[@id='q1001_a4']/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1]//*[contains(concat(' ', normalize-space(@class), ' '), ' option_radio ')][1] | //*[@id='q1001_a4']/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1])",
    },
    "frame_chain": [],
}
POST_ACTION_HTML = """<html><body>
<div class="question radio_question" id="question1001">
  <div class="radio_q_text">Question de référence</div>
  <div class="answer_options answer_options1001">
    <div class="option_radio"><img class="radio_image" src="hidden.png"></div>
    <div class="option_label"><span>Un homme</span></div>
    <input class="radioQT" type="checkbox" id="q1001_a1" name="q1001" value="1" style="">
  </div>
  <div class="answer_options answer_options1001">
    <div class="option_radio"><img class="radio_image" src="hidden.png"></div>
    <div class="option_label"><span>Une femme</span></div>
    <input class="radioQT" type="checkbox" id="q1001_a2" name="q1001" value="2">
  </div>
  <div class="answer_options answer_options1001">
    <div class="option_radio"><img class="radio_image" src="hidden.png"></div>
    <div class="option_label"><span>Autre, merci de préciser:
      <input class="input_field_radio" type="text" id="t1001_4"></span></div>
    <input class="radioQT" type="checkbox" id="q1001_a4" name="q1001" value="4">
  </div>
</div></body></html>"""


class QtNativeCheckedExclusionTests(unittest.TestCase):
    def _report_after_external_success(self, html: str, *, dispatcher_success: bool = True,
                                       page_like: bool = False, unreadable: bool = False,
                                       nested_capture: bool = False) -> dict:
        static = load_static_driver(html)
        driver = SimpleNamespace(
            query_selector=static.query_selector,
            query_selector_all=(Mock(side_effect=RuntimeError("DOM illisible"))
                                if unreadable else static.query_selector_all),
        ) if page_like or unreadable else static
        fix = SimpleNamespace(
            fix_id="fix_qt_reference",
            condition=SimpleNamespace(
                required_selectors=("div.question.radio_question",), excluded_selectors=(),
            ),
            handler=lambda *_: ActionFixOutcome.HANDLED_SUCCESS,
        )
        registry = SimpleNamespace(candidates=lambda **_: (fix,))
        with (
            patch("Survey.action_fix_hook.is_cta_intercept_only", return_value=False),
            patch("Survey.action_validator.get_target", return_value=PAYLOAD),
            patch("Survey.action_validator.is_checked", return_value=True),
            observe_action_fix_successes(),
        ):
            if nested_capture:
                with observe_action_fix_successes():
                    outcome = run_action_fix_hook(driver, ACTION, registry=registry)
            else:
                outcome = run_action_fix_hook(driver, ACTION, registry=registry)
            self.assertIs(outcome, ActionFixOutcome.HANDLED_SUCCESS)
            return validate_actions([ACTION], dispatcher_success=dispatcher_success, driver=driver)

    def test_external_success_without_qt_ui_marker_is_reported_on_both_drivers(self) -> None:
        for page_like in (False, True):
            with self.subTest(page_like=page_like):
                report = self._report_after_external_success(POST_ACTION_HTML, page_like=page_like)
                self.assertEqual(report["issues"], [{
                    "action_index": 0, "qid": "Q1",
                    "failure_type": "action_fix_success_unconfirmed",
                    "target_id": TARGET_ID, "itype": "radio", "value": "Un homme",
                    "dom_signal": "qt_visual_marker_absent",
                }])
                self.assertFalse(report["ok"])
        self.assertEqual(
            self._report_after_external_success(POST_ACTION_HTML, nested_capture=True)["issues"][0]["failure_type"],
            "action_fix_success_unconfirmed",
        )

    def test_external_success_with_qt_ui_marker_has_no_issue(self) -> None:
        for before, after in (
            ('class="option_radio"', 'class="option_radio input_on"'),
            ('class="option_label"', 'class="option_label input_label_on"'),
        ):
            with self.subTest(marker=after):
                selected_html = POST_ACTION_HTML.replace(before, after, 1)
                report = self._report_after_external_success(selected_html)
                self.assertEqual(report["issues"], [])
                self.assertTrue(report["ok"])

    def test_external_success_with_indeterminate_qt_structure_abstains(self) -> None:
        unknown_html = POST_ACTION_HTML.replace(
            '<div class="option_radio"><img class="radio_image" src="hidden.png"></div>',
            '', 1,
        )
        self.assertEqual(self._report_after_external_success(unknown_html)["issues"], [])
        self.assertEqual(self._report_after_external_success(
            POST_ACTION_HTML, unreadable=True
        )["issues"], [])

    def test_historical_success_and_dispatcher_failure_keep_their_reports(self) -> None:
        with patch("Survey.action_validator.get_target", return_value=PAYLOAD):
            historical = validate_actions(
                [ACTION], dispatcher_success=True, driver=load_static_driver(POST_ACTION_HTML)
            )
        self.assertEqual(historical, {
            "stage": "action", "ok": True, "actions_count": 1,
            "dispatcher_success": True, "issues": [],
        })
        failure = self._report_after_external_success(POST_ACTION_HTML, dispatcher_success=False)
        self.assertEqual(failure, {
            "stage": "action", "ok": False, "actions_count": 1,
            "dispatcher_success": False,
            "issues": [{"failure_type": "dispatcher_reported_failure", "actions_count": 1}],
        })

    def test_passive_static_replay_rechecks_recorded_fix_success_against_dom(self) -> None:
        original = self._report_after_external_success(POST_ACTION_HTML)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            (artifacts / "actions_requested.json").write_text(
                json.dumps([ACTION]), encoding="utf-8"
            )
            with patch("Survey.action_validator.get_target", return_value=PAYLOAD):
                missing = _run_pipeline(
                    load_static_driver(POST_ACTION_HTML), "synthetic_qt", "action",
                    "post_action_dom.html", artifacts, original,
                    ["action_fix_success_unconfirmed"], blocks=[],
                )
                selected_html = POST_ACTION_HTML.replace(
                    'class="option_radio"', 'class="option_radio input_on"', 1
                )
                selected = _run_pipeline(
                    load_static_driver(selected_html), "synthetic_qt", "action",
                    "post_action_dom.html", artifacts, original,
                    ["action_fix_success_unconfirmed"], blocks=[],
                )
        self.assertEqual(missing.verdict, "REPRODUIT")
        self.assertEqual(selected.verdict, "NON_REPRODUIT")

    def test_unconfirmed_success_uses_existing_inconclusive_outcome(self) -> None:
        original = {
            "issues": [{"failure_type": "dispatcher_reported_failure", "actions_count": 1}],
        }
        after = self._report_after_external_success(POST_ACTION_HTML)
        comparison = _compare_validation(original, after, {}, [PAYLOAD])
        self.assertEqual(_action_outcome(comparison, True)["outcome"], "NON_CONCLUANT")
        same_issue = _compare_validation(after, after, {}, [PAYLOAD])
        self.assertEqual(_action_outcome(same_issue, True)["outcome"], "NON_CONCLUANT")

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
