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
from Survey.autofix.replay_browser import _ACTION_REPLAY_EXECUTE_SCRIPTS, _capture_dispatcher_steps
from Survey.autofix.static_validator import _run
from Survey.log_utils import log_debug, log_info


class AutofixSubprocessEncodingTests(unittest.TestCase):
    def test_not_executed_reason_is_preserved_in_action_replay_outputs(self) -> None:
        execution = SimpleNamespace(
            status="NOT_EXECUTED", reason="budget invalide", dispatcher_success=None,
            duration_s=None, budget_s=0, validation_comparison=None,
            validation_error=None, trace_replay=None,
            dispatcher_steps=["strategy=target_id verification=failed"],
        )
        extraction = SimpleNamespace(blocks=[], error=None)
        with (
            patch("Survey.autofix.replay_browser.IsolatedReplayBrowser") as browser_type,
            patch("Survey.autofix.replay_browser.extract_case_blocks", return_value=extraction),
            patch("Survey.autofix.replay_browser.execute_case_action", return_value=execution),
        ):
            result = _attempt_real_dispatch_replay(Path("synthetic_case"), {"stage": "action"})
        self.assertEqual(result["reason"], "budget invalide")
        self.assertEqual(result["dispatcher_steps"], execution.dispatcher_steps)
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
                for _ in range(30):
                    log_debug(
                        "[TARGET_DEBUG]",
                        "target_id QT post-verification failed: question='secret question' "
                        "value='secret answer' option='secret label' url='https://private.example'",
                    )
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
