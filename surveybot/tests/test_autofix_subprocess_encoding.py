from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Survey.autofix.failure_diagnosis import _attempt_real_dispatch_replay
from Survey.autofix.live_validator import _ACTION_RUNNER_SCRIPT as _LIVE_ACTION_RUNNER_SCRIPT
from Survey.autofix.patch_replay import (
    _ACTION_RUNNER_SCRIPT as _PATCH_ACTION_RUNNER_SCRIPT,
    PreconditionResult, replay_patch,
)
from Survey.autofix.patch_commit import _find_existing_case_commit
from Survey.autofix.replay_browser import _ACTION_REPLAY_EXECUTE_SCRIPTS, _capture_dispatcher_steps, _safe_target_log
from Survey.autofix.replay_browser import _dom_fact_from_raw, _sanitize_classes
from Survey.autofix.replay_browser import summarize_action_target_shapes, summarize_requested_option_dom_facts
from Survey.autofix.static_validator import _run
from Survey.log_utils import log_debug, log_info


class _FakeElement:
    """Simule un ElementHandle Playwright : evaluate() renvoie un objet figé."""

    def __init__(self, raw) -> None:
        self._raw = raw

    def evaluate(self, _script: str):
        return self._raw


class _RaisingElement:
    def evaluate(self, _script: str):
        raise RuntimeError("private boom")


class _FakePage:
    """Simule une Page Playwright : query_selector("xpath=...") résout depuis une
    table fixe et journalise chaque sélecteur interrogé (pour vérifier qu'une
    seule option — la demandée — est jamais interrogée)."""

    def __init__(self, elements_by_xpath: dict) -> None:
        self._elements = elements_by_xpath
        self.queried: list = []

    def query_selector(self, selector: str):
        self.queried.append(selector)
        return self._elements.get(selector[len("xpath="):])


class AutofixSubprocessEncodingTests(unittest.TestCase):
    def _synthetic_patch_replay(self, stage: str, after: dict):
        pre = PreconditionResult(
            satisfied=True, case_id="synthetic_case", stage=stage,
            worktree={"worktree_path": "synthetic_worktree", "branch": "synthetic_branch", "base_sha": "abc"},
            diagnosis={"replay": {"verdict": "REPRODUIT"},
                       "real_dispatch_replay": {"validation_comparison": {"outcome": "BUG_PERSISTANT"}}},
        )
        with (
            patch("Survey.autofix.patch_replay.check_preconditions", return_value=pre),
            patch("Survey.autofix.patch_replay._resolve_worktree_package_root",
                  return_value=(Path("synthetic_package"), None)),
            patch("Survey.autofix.patch_replay._run_replay_subprocess", return_value=(after, None)),
        ):
            return replay_patch(
                failure_case_dir="synthetic_case", diagnosis_dir="synthetic_diagnosis",
                worktree_manifest_path="synthetic_worktree.json",
                validation_static_path="synthetic_validation.json",
            )

    def test_patch_replay_marks_only_unverifiable_handler_failure_inconclusive(self) -> None:
        comparison = {"outcome": "BUG_PERSISTANT", "verdict": "REPRODUIT", "reasons": []}
        selected = "action_fix selected fix_id=fix_radio_qt"
        handler_failure = "action_fix verdict=HANDLED_FAILURE reason=handler_returned_failure"
        base = {"status": "FAILURE", "execute_scripts": False, "validation_comparison": comparison,
                "dispatcher_steps": [selected, handler_failure]}
        result = self._synthetic_patch_replay("action", base)
        self.assertEqual(result.outcome, "NON_CONCLUANT")
        self.assertFalse(result.patch_validated)
        self.assertEqual(result.after_replay["validation_comparison"], comparison)
        self.assertEqual(result.as_dict()["outcome_reason"],
                         "correctif d'action non vérifiable sans les scripts de la page")

        unchanged = (
            {**base, "dispatcher_steps": [selected, "action_fix verdict=HANDLED_FAILURE reason=handler_exception"]},
            {**base, "dispatcher_steps": [selected, "action_fix verdict=HANDLED_FAILURE reason=invalid_result"]},
            {**base, "dispatcher_steps": [selected, "action_fix verdict=HANDLED_FAILURE reason=post_handler_timeout"]},
            {**base, "dispatcher_steps": [handler_failure]},
            {**base, "dispatcher_steps": [selected, "strategy=target_id attempted", handler_failure]},
            {**base, "dispatcher_steps": [selected, "action_fix verdict=HANDLED_FAILURE"]},
            {**base, "dispatcher_steps": [selected, "action_fix verdict=HANDLED_FAILURE reason=unknown"]},
            {**base, "dispatcher_steps": [selected, "action_fix verdict=HANDLED_SUCCESS"]},
            {**base, "dispatcher_steps": [selected, handler_failure,
                                           "action_fix selected fix_id=fix_second",
                                           "action_fix verdict=HANDLED_FAILURE reason=handler_exception"]},
            {**base, "dispatcher_steps": [selected, handler_failure,
                                           "action_fix selected fix_id=fix_second"]},
            {**base, "dispatcher_steps": None},
            {**base, "dispatcher_steps": [selected, handler_failure, "capture=truncated"]},
            {**base, "dispatcher_steps": [selected, handler_failure] + ["apply ok=false"] * 23},
            {**base, "execute_scripts": True},
            {key: value for key, value in base.items() if key != "execute_scripts"},
            {**base, "status": "SUCCESS"},
            {**base, "validation_comparison": {"outcome": "NON_CONCLUANT"}},
        )
        for index, after in enumerate(unchanged):
            with self.subTest(index=index):
                result = self._synthetic_patch_replay("action", after)
                self.assertEqual(result.outcome, after["validation_comparison"]["outcome"])
                self.assertFalse(result.patch_validated)
                self.assertNotIn("outcome_reason", result.as_dict())

        extraction = self._synthetic_patch_replay("extraction", {
            "verdict": "REPRODUIT", "execute_scripts": False,
            "dispatcher_steps": [selected, handler_failure],
        })
        self.assertEqual(extraction.outcome, "BUG_PERSISTANT")
        self.assertNotIn("outcome_reason", extraction.as_dict())

    def test_action_target_shapes_follow_registry_group_payload_and_remove_literals(self) -> None:
        def payload(target: str) -> dict:
            locator = (
                f'(//*[@id="{target}"]/ancestor::*[contains(concat(\' \', normalize-space(@class), \' \'), '
                "' answer_options ')][1]//*[contains(concat(' ', normalize-space(@class), ' '), "
                "' option_radio ')][1])"
            )
            return {
                "kind": "group", "itype": "radio", "group_key": "radio:name:dom:private_group",
                "question": "private question", "frame_chain": [2],
                "option_xpath_map": {"private label A": locator, "private label B": locator.replace(target, target + "2")},
            }
        summary = summarize_action_target_shapes({"target_secret": payload("private_id")})
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["group_key_shape"], "radio:name:dom:<GROUP>")
        self.assertEqual(summary[0]["frame_depth"], 1)
        self.assertEqual(summary[0]["options_count"], 2)
        self.assertEqual(summary[0]["locator_shapes"][0]["count"], 2)
        shape = summary[0]["locator_shapes"][0]["shape"]
        self.assertIn("answer_options", shape)
        self.assertIn("option_radio", shape)
        self.assertIn("<LITERAL>", shape)
        for secret in ("private question", "private label", "private_group", "private_id", "target_secret"):
            self.assertNotIn(secret, str(summary))

        checkbox = {
            "kind": "group", "itype": "checkbox", "group_key": "checkbox:name:private_group",
            "frame_chain": [], "option_xpath_map": {
                "private answer": "(//input[@type='checkbox' and @name='private_name' "
                "and @value='private_value' and @href='https://private.example'])[1]"
            },
        }
        checkbox_summary = summarize_action_target_shapes({"private_target": checkbox})
        self.assertEqual(checkbox_summary[0]["group_key_shape"], "checkbox:name:<GROUP>")
        self.assertEqual(checkbox_summary[0]["locator_shapes"][0]["shape"],
                         "(//input[@type=<LITERAL> and @name=<LITERAL> "
                         "and @value=<LITERAL> and @href=<LITERAL>])[1]")
        for secret in ("private answer", "private_name", "private_value", "private.example", "private_target"):
            self.assertNotIn(secret, str(checkbox_summary))

    def test_action_target_shape_limits_abandon_capture(self) -> None:
        base = {"kind": "group", "itype": "checkbox", "group_key": "checkbox:name:private",
                "frame_chain": [], "option_xpath_map": {"private value": '//*[@id="private"]'}}
        self.assertIsNone(summarize_action_target_shapes({str(i): base for i in range(9)}))
        self.assertIsNone(summarize_action_target_shapes({"one": {**base, "option_xpath_map": {
            str(i): f'//*[@id="private_{i}"]/{"div/" * i}input' for i in range(9)
        }}}))
        self.assertIsNone(summarize_action_target_shapes({"one": {**base, "option_xpath_map": {
            "private value": '//*[@id="' + "x" * 520 + '"]'
        }}}))
        self.assertIsNone(summarize_action_target_shapes({"one": {**base, "option_xpath_map": {
            str(i): f'//*[@id="private_{i}"]/ancestor::' + "div/" * 100 + f"input[{i}]"
            for i in range(8)
        }}}))

    def test_registry_shape_read_failure_is_optional(self) -> None:
        class UnreadableTargets(dict):
            def values(self):
                raise RuntimeError("private value")

        self.assertIsNone(summarize_action_target_shapes(UnreadableTargets({"target_secret": None})))

    def test_dom_fact_classes_are_sanitized_bounded_and_never_raise(self) -> None:
        self.assertEqual(
            _sanitize_classes(["radioQT123", "option_radio", "bad class", "x" * 40, "a", "b", "c"]),
            ["radioQT<N>", "option_radio", "a"],
        )
        self.assertEqual(_sanitize_classes(None), [])
        self.assertEqual(_sanitize_classes("not-a-list"), [])
        self.assertEqual(_sanitize_classes([]), [])
        self.assertEqual(_sanitize_classes([123, None, ""]), [])

    def test_dom_fact_from_raw_is_a_closed_validated_shape(self) -> None:
        self.assertIsNone(_dom_fact_from_raw(None))
        self.assertIsNone(_dom_fact_from_raw("not-a-dict"))
        self.assertIsNone(_dom_fact_from_raw({"tag": 123, "visible": True, "width": 1, "height": 1}))
        self.assertIsNone(_dom_fact_from_raw({"tag": "input", "visible": "yes", "width": 1, "height": 1}))
        self.assertIsNone(_dom_fact_from_raw({"tag": "input", "visible": True, "width": "1", "height": 1}))
        self.assertIsNone(_dom_fact_from_raw({"tag": "input", "visible": True, "width": True, "height": 1}))

        # input_type hors de la liste fermée, ou balise non "input" : ramené à None,
        # élément quand même conservé (pas d'abandon pour ce seul champ).
        non_closed = _dom_fact_from_raw(
            {"tag": "input", "input_type": "private_weird", "visible": True, "width": 1, "height": 1, "classes": []}
        )
        self.assertEqual(non_closed, {"tag": "input", "input_type": None, "classes": [],
                                       "visible": True, "width": 1, "height": 1})
        non_input = _dom_fact_from_raw(
            {"tag": "span", "input_type": "radio", "visible": True, "width": 1, "height": 1, "classes": []}
        )
        self.assertIsNone(non_input["input_type"])

        valid = _dom_fact_from_raw(
            {"tag": "input", "input_type": "RADIO", "visible": False, "width": 0.4, "height": -1,
             "classes": ["stable_class"]}
        )
        self.assertEqual(valid, {"tag": "input", "input_type": "radio", "classes": ["stable_class"],
                                  "visible": False, "width": 0, "height": 0})

    def _group_payload(self, xpath_by_label: dict, *, frame_chain=None) -> dict:
        return {
            "kind": "group", "itype": "radio", "frame_chain": frame_chain,
            "option_xpath_map": dict(xpath_by_label),
        }

    def _write_actions(self, case_dir: Path, actions: list) -> None:
        (case_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (case_dir / "artifacts" / "actions_requested.json").write_text(json.dumps(actions), encoding="utf-8")

    def test_requested_option_dom_facts_measures_only_requested_option_and_its_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            self._write_actions(case_dir, [{"target_id": "t1", "itype": "radio", "value": "private Oui", "qid": "q1"}])
            payload = self._group_payload({
                "private Oui": "//input[@id='radio_oui']", "private Non": "//input[@id='radio_non']",
            })
            raw = {
                "target": {"tag": "input", "input_type": "radio", "classes": ["input_radioQT2"],
                           "visible": False, "width": 0, "height": 0},
                "siblings": [
                    {"tag": "span", "input_type": None, "classes": ["option_radio"],
                     "visible": True, "width": 16, "height": 16},
                    {"tag": "span", "input_type": None, "classes": ["option_label", "input_label_on"],
                     "visible": True, "width": 120, "height": 20},
                ],
            }
            page = _FakePage({"//input[@id='radio_oui']": _FakeElement(raw)})
            result = summarize_requested_option_dom_facts(page, case_dir, {"t1": payload})
        self.assertEqual(result, [{
            "element": {"tag": "input", "input_type": "radio", "classes": ["input_radioQT<N>"],
                        "visible": False, "width": 0, "height": 0},
            "siblings": [
                {"tag": "span", "input_type": None, "classes": ["option_radio"],
                 "visible": True, "width": 16, "height": 16},
                {"tag": "span", "input_type": None, "classes": ["option_label", "input_label_on"],
                 "visible": True, "width": 120, "height": 20},
            ],
        }])
        self.assertEqual(page.queried, ["xpath=//input[@id='radio_oui']"])
        self.assertNotIn("private", str(result))

    def test_requested_option_dom_facts_bounds_targets_and_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            actions = [{"target_id": f"t{i}", "value": "Oui"} for i in range(5)]
            self._write_actions(case_dir, actions)
            targets = {
                f"t{i}": self._group_payload({"Oui": f"//input[@id='radio_{i}']"}) for i in range(5)
            }
            raw = {
                "target": {"tag": "input", "input_type": "radio", "classes": [],
                           "visible": False, "width": 0, "height": 0},
                "siblings": [
                    {"tag": "span", "input_type": None, "classes": [], "visible": True, "width": 1, "height": 1}
                    for _ in range(10)
                ],
            }
            elements = {f"//input[@id='radio_{i}']": _FakeElement(raw) for i in range(5)}
            page = _FakePage(elements)
            result = summarize_requested_option_dom_facts(page, case_dir, targets)
        self.assertEqual(len(result), 3)
        self.assertEqual(len(result[0]["siblings"]), 6)

    def test_requested_option_dom_facts_skips_target_in_a_frame_without_querying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            self._write_actions(case_dir, [{"target_id": "t1", "value": "Oui"}])
            payload = self._group_payload({"Oui": "//input[@id='radio_oui']"}, frame_chain=[2])
            page = _FakePage({"//input[@id='radio_oui']": _FakeElement({"target": {}, "siblings": []})})
            result = summarize_requested_option_dom_facts(page, case_dir, {"t1": payload})
        self.assertIsNone(result)
        self.assertEqual(page.queried, [])

    def test_requested_option_dom_facts_skips_unresolved_element_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            self._write_actions(case_dir, [{"target_id": "t1", "value": "Oui"}])
            payload = self._group_payload({"Oui": "//input[@id='radio_oui']"})
            page = _FakePage({})
            result = summarize_requested_option_dom_facts(page, case_dir, {"t1": payload})
        self.assertIsNone(result)

    def test_requested_option_dom_facts_skips_on_evaluate_error_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            self._write_actions(case_dir, [{"target_id": "t1", "value": "Oui"}])
            payload = self._group_payload({"Oui": "//input[@id='radio_oui']"})
            page = _FakePage({"//input[@id='radio_oui']": _RaisingElement()})
            result = summarize_requested_option_dom_facts(page, case_dir, {"t1": payload})
        self.assertIsNone(result)

    def test_requested_option_dom_facts_skips_unexpected_evaluate_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            self._write_actions(case_dir, [{"target_id": "t1", "value": "Oui"}])
            payload = self._group_payload({"Oui": "//input[@id='radio_oui']"})
            page = _FakePage({"//input[@id='radio_oui']": _FakeElement("not-a-dict")})
            result = summarize_requested_option_dom_facts(page, case_dir, {"t1": payload})
        self.assertIsNone(result)

    def test_requested_option_dom_facts_ignores_non_group_or_frameless_shape_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            self._write_actions(case_dir, [
                {"target_id": "t_single", "value": "Oui"},
                {"target_id": "t_missing_value"},
                {"target_id": "t1", "value": "Oui"},
            ])
            targets = {
                "t_single": {"kind": "single", "itype": "text", "frame_chain": []},
                "t_missing_value": self._group_payload({"Oui": "//input[@id='other']"}),
                "t1": self._group_payload({"Oui": "//input[@id='radio_oui']"}),
            }
            raw = {"target": {"tag": "input", "input_type": "radio", "classes": [],
                               "visible": True, "width": 10, "height": 10}, "siblings": []}
            page = _FakePage({"//input[@id='radio_oui']": _FakeElement(raw)})
            result = summarize_requested_option_dom_facts(page, case_dir, targets)
        self.assertEqual(len(result), 1)
        self.assertEqual(page.queried, ["xpath=//input[@id='radio_oui']"])

    def test_requested_option_dom_facts_without_actions_file_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            case_dir.mkdir(parents=True)
            result = summarize_requested_option_dom_facts(_FakePage({}), case_dir, {})
        self.assertIsNone(result)

    def test_attempt_real_dispatch_replay_adds_requested_option_dom_facts(self) -> None:
        raw = {
            "target": {"tag": "input", "input_type": "radio", "classes": ["input_radioQT"],
                       "visible": False, "width": 0, "height": 0},
            "siblings": [
                {"tag": "span", "input_type": None, "classes": ["option_radio"],
                 "visible": True, "width": 16, "height": 16},
            ],
        }
        page = _FakePage({"//input[@id='radio_oui']": _FakeElement(raw)})
        execution = SimpleNamespace(
            status="FAILURE", reason=None, dispatcher_success=False, duration_s=0.1,
            budget_s=30.0, validation_comparison=None, validation_error=None,
            trace_replay=None, dispatcher_steps=None,
        )
        extraction = SimpleNamespace(blocks=[], error=None, targets={
            "t1": self._group_payload({"Oui": "//input[@id='radio_oui']"}),
        })
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "synthetic_case"
            self._write_actions(case_dir, [{"target_id": "t1", "value": "Oui"}])
            with (
                patch("Survey.autofix.replay_browser.IsolatedReplayBrowser") as browser_type,
                patch("Survey.autofix.replay_browser.extract_case_blocks", return_value=extraction),
                patch("Survey.autofix.replay_browser.execute_case_action", return_value=execution),
            ):
                browser_type.return_value.__enter__.return_value.load_case_document.return_value = page
                result = _attempt_real_dispatch_replay(case_dir, {"stage": "action"})
        self.assertEqual(result["requested_option_dom_facts"], [{
            "element": {"tag": "input", "input_type": "radio", "classes": ["input_radioQT"],
                        "visible": False, "width": 0, "height": 0},
            "siblings": [
                {"tag": "span", "input_type": None, "classes": ["option_radio"],
                 "visible": True, "width": 16, "height": 16},
            ],
        }])
        self.assertEqual(page.queried, ["xpath=//input[@id='radio_oui']"])

    def test_not_executed_reason_is_preserved_in_action_replay_outputs(self) -> None:
        execution = SimpleNamespace(
            status="NOT_EXECUTED", reason="budget invalide", dispatcher_success=None,
            duration_s=None, budget_s=0, validation_comparison=None,
            validation_error=None, trace_replay=None,
            dispatcher_steps=["strategy=target_id verification=failed"],
        )
        extraction = SimpleNamespace(blocks=[], error=None, targets={
            "target_secret": {"kind": "group", "itype": "radio", "group_key": "radio:name:private",
                              "frame_chain": [], "option_xpath_map": {"private label": '//*[@id="private"]'}}
        })
        with (
            patch("Survey.autofix.replay_browser.IsolatedReplayBrowser") as browser_type,
            patch("Survey.autofix.replay_browser.extract_case_blocks", return_value=extraction),
            patch("Survey.autofix.replay_browser.execute_case_action", return_value=execution),
        ):
            result = _attempt_real_dispatch_replay(Path("synthetic_case"), {"stage": "action"})
        self.assertEqual(result["reason"], "budget invalide")
        self.assertEqual(result["dispatcher_steps"], execution.dispatcher_steps)
        self.assertEqual(result["target_shapes"][0]["locator_shapes"][0]["shape"], "//*[@id=<LITERAL>]")
        self.assertNotIn("private", str(result["target_shapes"]))
        self.assertIs(result["execute_scripts"], _ACTION_REPLAY_EXECUTE_SCRIPTS)
        self.assertIs(result["execute_scripts"], False)
        self.assertIs(
            browser_type.return_value.__enter__.return_value.load_case_document.call_args.kwargs["execute_scripts"],
            result["execute_scripts"],
        )
        self.assertIn('"reason": execution.reason', _PATCH_ACTION_RUNNER_SCRIPT)
        self.assertIn('"reason": execution.reason', _LIVE_ACTION_RUNNER_SCRIPT)
        self.assertIn('"dispatcher_steps": execution.dispatcher_steps', _PATCH_ACTION_RUNNER_SCRIPT)
        self.assertIn('"dispatcher_steps": execution.dispatcher_steps', _LIVE_ACTION_RUNNER_SCRIPT)
        self.assertIn('execute_scripts=_ACTION_REPLAY_EXECUTE_SCRIPTS', _PATCH_ACTION_RUNNER_SCRIPT)
        self.assertIn('"execute_scripts": _ACTION_REPLAY_EXECUTE_SCRIPTS', _PATCH_ACTION_RUNNER_SCRIPT)

    def test_diagnostic_outcome_is_not_reclassified_by_patch_replay_rule(self) -> None:
        comparison = {"outcome": "BUG_PERSISTANT", "verdict": "REPRODUIT"}
        execution = SimpleNamespace(
            status="FAILURE", reason=None, dispatcher_success=False, duration_s=0.2,
            budget_s=30.0, validation_comparison=comparison, validation_error=None,
            trace_replay=None, dispatcher_steps=[
                "action_fix selected fix_id=fix_radio_qt",
                "action_fix verdict=HANDLED_FAILURE reason=handler_returned_failure",
            ],
        )
        with (
            patch("Survey.autofix.replay_browser.IsolatedReplayBrowser"),
            patch("Survey.autofix.replay_browser.extract_case_blocks",
                  return_value=SimpleNamespace(blocks=[], error=None, targets={})),
            patch("Survey.autofix.replay_browser.execute_case_action", return_value=execution),
        ):
            result = _attempt_real_dispatch_replay(Path("synthetic_case"), {"stage": "action"})
        self.assertEqual(result["validation_comparison"], comparison)
        self.assertEqual(result["validation_comparison"]["outcome"], "BUG_PERSISTANT")
        self.assertNotIn("outcome_reason", result)

    def test_dispatcher_capture_is_bounded_sanitized_and_independent_of_log_level(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, {"LOG_LEVEL": "INFO"}), redirect_stdout(output):
            with _capture_dispatcher_steps() as steps:
                log_info("[TARGET]", "apply ok=true strategy=radio_main reason=applied")
                for index in range(30):
                    log_debug("[TARGET]", f"apply ok=true strategy=s{index} reason=applied")
                log_info("[TARGET]", "apply ok=false reason=no_strategy strategy=none")
        self.assertEqual(
            output.getvalue(),
            "[TARGET] apply ok=true strategy=radio_main reason=applied\n"
            "[TARGET] apply ok=false reason=no_strategy strategy=none\n",
        )
        self.assertEqual(steps[0], "apply ok=true strategy=radio_main reason=applied")
        self.assertEqual(len(steps), 24)
        self.assertEqual(steps[-2:], ["capture=truncated", "apply ok=false strategy=none reason=no_strategy"])
        self.assertTrue(all(len(step) <= 160 for step in steps))
        self.assertNotIn("secret", " ".join(steps))
        self.assertNotIn("private.example", " ".join(steps))

    def test_click_failure_reasons_are_closed_and_prioritized(self) -> None:
        cases = (
            ("Element is not visible", "not_visible"),
            ("<div> intercepts pointer events", "intercepted"),
            ("Element is not enabled", "not_enabled"),
            ("Element is not stable", "unstable"),
            ("Element is detached from DOM", "detached"),
            ("Element is not attached to the DOM", "detached"),
        )
        for detail, reason in cases:
            with self.subTest(reason=reason, detail=detail):
                raw = f"native click failed on target: TimeoutError: {detail}; secret answer"
                self.assertEqual(_safe_target_log("[TARGET_DEBUG]", raw),
                                 f"click=native_failed reason={reason}")
        self.assertEqual(
            _safe_target_log("[TARGET_DEBUG]", "actionchains click failed on target: "
                             "TimeoutError: Element is not visible; secret answer"),
            "click=hover_failed reason=not_visible",
        )
        self.assertEqual(
            _safe_target_log("[TARGET_DEBUG]", "native click failed on target: "
                             "TimeoutError: Element is not visible; <div> intercepts pointer events; "
                             "Element is detached from DOM"),
            "click=native_failed reason=detached",
        )
        self.assertEqual(_safe_target_log("[TARGET_DEBUG]", "native click failed on target: "
                                          "TimeoutError: secret answer"), "click=native_failed")
        self.assertEqual(_safe_target_log("[TARGET_DEBUG]", "actionchains click failed on target: "
                                          "TimeoutError: secret answer"), "click=hover_failed")

    def test_dispatcher_capture_skips_only_consecutive_identical_lines(self) -> None:
        with patch.dict(os.environ, {"LOG_LEVEL": "INFO"}):
            with _capture_dispatcher_steps() as steps:
                log_debug("[TARGET_DEBUG]", "native click failed on target: "
                          "TimeoutError: Element is not visible; secret answer")
                log_debug("[TARGET_DEBUG]", "native click failed on target: "
                          "TimeoutError: Element is not visible; another secret")
                log_debug("[TARGET_DEBUG]", "actionchains click failed on target: "
                          "TimeoutError: unrecognized secret")
                log_debug("[TARGET_DEBUG]", "native click failed on target: "
                          "TimeoutError: Element is not visible; third secret")
        self.assertEqual(steps, [
            "click=native_failed reason=not_visible",
            "click=hover_failed",
            "click=native_failed reason=not_visible",
        ])
        self.assertNotIn("secret", " ".join(steps))

        with patch.dict(os.environ, {"LOG_LEVEL": "INFO"}), redirect_stdout(io.StringIO()):
            with _capture_dispatcher_steps() as repeated_steps:
                for _ in range(30):
                    log_debug("[TARGET_DEBUG]", "target_id QT post-verification failed: secret answer")
                log_info("[TARGET]", "apply ok=false reason=no_strategy strategy=none")
        self.assertEqual(repeated_steps, [
            "strategy=target_id verification=failed",
            "apply ok=false strategy=none reason=no_strategy",
        ])

    def test_dispatcher_capture_records_strategy_result_without_debug_console(self) -> None:
        from Survey.action_dispatcher import _try

        output = io.StringIO()
        with patch.dict(os.environ, {"LOG_LEVEL": "INFO"}), redirect_stdout(output):
            with _capture_dispatcher_steps() as steps:
                self.assertFalse(_try(SimpleNamespace(), "radio_main", lambda: False))
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(steps, ["strategy=radio_main attempted", "strategy=radio_main result=failed"])

    def test_dispatcher_capture_accepts_only_closed_action_fix_decisions(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, {"LOG_LEVEL": "INFO"}), redirect_stdout(output):
            with _capture_dispatcher_steps() as steps:
                log_debug("[ACTION_FIX]", "selected fix_id=fix_radio_qt")
                log_debug("[ACTION_FIX]", "verdict=HANDLED_FAILURE")
                log_debug("[ACTION_FIX]", "verdict=HANDLED_FAILURE reason=handler_returned_failure")
                log_debug("[ACTION_FIX]", "verdict=HANDLED_FAILURE reason=handler_exception")
                log_debug("[ACTION_FIX]", "verdict=HANDLED_FAILURE reason=invalid_result")
                log_debug("[ACTION_FIX]", "verdict=HANDLED_FAILURE reason=post_handler_timeout")
                log_debug("[ACTION_FIX]", "selected fix_id=bad-id")
                log_debug("[ACTION_FIX]", "verdict=DECLINED value=secret_answer")
                log_debug("[ACTION_FIX]", "verdict=UNKNOWN")
                log_debug("[ACTION_FIX]", "verdict=HANDLED_FAILURE reason=unknown")
                log_debug("[ACTION_FIX]", "verdict=HANDLED_SUCCESS reason=handler_returned_failure")
                log_debug("[ACTION_FIX]", "verdict=HANDLED_FAILURE reason=handler_returned_failure value=secret")
                log_debug("[OTHER]", "selected fix_id=fix_radio_qt")
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(steps, [
            "action_fix selected fix_id=fix_radio_qt",
            "action_fix verdict=HANDLED_FAILURE",
            "action_fix verdict=HANDLED_FAILURE reason=handler_returned_failure",
            "action_fix verdict=HANDLED_FAILURE reason=handler_exception",
            "action_fix verdict=HANDLED_FAILURE reason=invalid_result",
            "action_fix verdict=HANDLED_FAILURE reason=post_handler_timeout",
        ])

    def test_utf8_output_outside_cp1252_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ok, out, err, timed_out = _run(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write('漢字🙂'.encode('utf-8'))"],
                cwd=Path(directory), timeout=10,
            )
        self.assertTrue(ok)
        self.assertEqual(out, "漢字🙂")
        self.assertEqual(err, "")
        self.assertFalse(timed_out)

    def test_invalid_utf8_is_an_explicit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ok, out, err, timed_out = _run(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"],
                cwd=Path(directory), timeout=10,
            )
        self.assertFalse(ok)
        self.assertEqual(out, "\ufffd")
        self.assertIn("sortie UTF-8 invalide", err)
        self.assertFalse(timed_out)

    def test_missing_captured_streams_are_empty_strings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "Survey.autofix.static_validator.subprocess.run",
                return_value=subprocess.CompletedProcess(["git"], 0, None, None),
            ):
                ok, out, err, timed_out = _run(["git", "status"], cwd=Path(directory), timeout=10)
        self.assertEqual((ok, out, err, timed_out), (True, "", "", False))

    def test_git_history_with_unicode_commit_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)

            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", *args], cwd=repo, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", check=True,
                ).stdout.strip()

            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "file.txt").write_text("content", encoding="utf-8")
            git("add", "file.txt")
            git("commit", "-qm", "Mise à jour 漢字🙂\n\ncase_id=case-utf8")
            branch = git("branch", "--show-current")
            sha = git("rev-parse", "HEAD")
            self.assertEqual(
                _find_existing_case_commit(repo, branch, "case-utf8", timeout=10), sha,
            )
