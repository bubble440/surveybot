from __future__ import annotations

import io
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
from Survey.autofix.patch_replay import _ACTION_RUNNER_SCRIPT as _PATCH_ACTION_RUNNER_SCRIPT
from Survey.autofix.patch_commit import _find_existing_case_commit
from Survey.autofix.replay_browser import _ACTION_REPLAY_EXECUTE_SCRIPTS, _capture_dispatcher_steps, _safe_target_log
from Survey.autofix.replay_browser import summarize_action_target_shapes
from Survey.autofix.static_validator import _run
from Survey.log_utils import log_debug, log_info


class AutofixSubprocessEncodingTests(unittest.TestCase):
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
                log_debug("[ACTION_FIX]", "selected fix_id=bad-id")
                log_debug("[ACTION_FIX]", "verdict=DECLINED value=secret_answer")
                log_debug("[ACTION_FIX]", "verdict=UNKNOWN")
                log_debug("[OTHER]", "selected fix_id=fix_radio_qt")
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(steps, [
            "action_fix selected fix_id=fix_radio_qt",
            "action_fix verdict=HANDLED_FAILURE",
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
