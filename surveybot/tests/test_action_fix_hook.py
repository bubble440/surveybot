from __future__ import annotations

import json
import tempfile
import time
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import Mock, patch

from Survey.action_fix_hook import ActionFixOutcome, run_action_fix_hook
from Survey.external_fix_registry import DomCondition, ExternalFix, ExternalFixRegistry, FixRegistryError
from Survey.extractor_integrity import _hash_function


class FakeDom:
    def __init__(self, present: tuple[str, ...] = (), *, broken: bool = False) -> None:
        self.present = set(present)
        self.broken = broken
        self.queries: list[str] = []
        self.clicks = 0
        self.child_frames: list[FakeDom] = []

    def query_selector(self, selector: str) -> object | None:
        self.queries.append(selector)
        if self.broken:
            raise RuntimeError("email=secret")
        return object() if selector in self.present else None


class ActionFixHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Survey"
        self.root.mkdir()
        (self.root / "action_dispatcher.py").write_text(
            "def core():\n    return False\n\ndef execute_action():\n    return False\n", encoding="utf-8"
        )
        self.core_hash = _hash_function(self.root, "action_dispatcher.py", "core")
        (self.root / "extractor_integrity.json").write_text(json.dumps({
            "action_dispatcher.py::core": {"hash": self.core_hash}
        }), encoding="utf-8")
        self.registry = ExternalFixRegistry(root=self.root)
        self.action = {"qid": "Q1", "target_id": None, "value": "row || col", "itype": "matrix", "context": "row"}
        self.events: list[str] = []
        self.selector = "div.action-test"

    def _register(
        self, fix_id: str, handler: object, *, position: str = "before",
        excluded: tuple[str, ...] = (),
    ) -> None:
        self.registry.register(ExternalFix(
            fix_id=fix_id,
            core_function_id="action_dispatcher.py::core",
            expected_core_hash=self.core_hash,
            anchor_function_id="action_dispatcher.py::execute_action",
            position=position,
            case_ids=("synthetic_case",),
            condition=DomCondition((self.selector,), excluded),
            handler=handler,
            stage="action",
        ))

    def _run(self, dom: FakeDom, **kwargs: object) -> ActionFixOutcome:
        with patch("Survey.action_fix_hook.is_cta_intercept_only", return_value=False):
            return run_action_fix_hook(dom, self.action, registry=self.registry, **kwargs)

    def test_empty_registry_and_dom_guard_fail_open(self) -> None:
        dom = FakeDom((self.selector,))
        with patch("Survey.action_fix_hook.log_debug") as debug:
            self.assertIs(self._run(dom), ActionFixOutcome.DECLINED)
        debug.assert_not_called()
        self.assertEqual(dom.queries, [])

        handler = Mock(return_value=ActionFixOutcome.HANDLED_SUCCESS)
        self._register("fix_guard", handler, excluded=("div.action-disabled",))
        with patch("Survey.action_fix_hook.log_debug") as debug:
            self.assertIs(self._run(FakeDom()), ActionFixOutcome.DECLINED)
        debug.assert_not_called()
        self.assertIs(
            self._run(FakeDom((self.selector, "div.action-disabled"))), ActionFixOutcome.DECLINED
        )
        with patch("Survey.action_fix_hook.log_debug") as debug:
            self.assertIs(self._run(FakeDom((self.selector,), broken=True)), ActionFixOutcome.DECLINED)
        self.assertNotIn("email=secret", str(debug.call_args_list))
        handler.assert_not_called()

    def test_selected_fix_logs_exact_returned_verdict_and_log_failure_is_inert(self) -> None:
        for outcome in ActionFixOutcome:
            with self.subTest(outcome=outcome.name):
                self.registry = ExternalFixRegistry(root=self.root)
                fix_id = f"fix_{outcome.value}"
                self._register(fix_id, Mock(return_value=outcome))
                with patch("Survey.action_fix_hook.log_debug") as debug:
                    returned = self._run(FakeDom((self.selector,)))
                self.assertIs(returned, outcome)
                self.assertEqual(
                    [call.args for call in debug.call_args_list],
                    [("[ACTION_FIX]", f"selected fix_id={fix_id}"),
                     ("[ACTION_FIX]", f"verdict={returned.name}"
                      + (" reason=handler_returned_failure" if returned is ActionFixOutcome.HANDLED_FAILURE else ""))],
                )

        self.registry = ExternalFixRegistry(root=self.root)
        self._register("fix_log_failure", Mock(return_value=ActionFixOutcome.HANDLED_SUCCESS))
        with patch("Survey.action_fix_hook.log_debug", side_effect=OSError("journal indisponible")):
            self.assertIs(self._run(FakeDom((self.selector,))), ActionFixOutcome.HANDLED_SUCCESS)
        self.registry = ExternalFixRegistry(root=self.root)
        self._register("fix_failure_log_failure", Mock(return_value=ActionFixOutcome.HANDLED_FAILURE))
        with patch("Survey.action_fix_hook.log_debug", side_effect=OSError("journal indisponible")):
            self.assertIs(self._run(FakeDom((self.selector,))), ActionFixOutcome.HANDLED_FAILURE)
        with patch("Survey.action_fix_hook.log_debug", side_effect=[None, OSError("verdict indisponible")]):
            self.assertIs(self._run(FakeDom((self.selector,))), ActionFixOutcome.HANDLED_FAILURE)

    def test_selected_fix_exception_invalid_result_and_late_budget_log_decision(self) -> None:
        def broken_handler(*_args: object) -> ActionFixOutcome:
            raise RuntimeError("answer=secret")

        cases = (
            ("fix_invalid_logged", lambda *_: True, "invalid_result"),
            ("fix_exception_logged", broken_handler, "handler_exception"),
        )
        for fix_id, handler, reason in cases:
            with self.subTest(fix_id=fix_id):
                self.registry = ExternalFixRegistry(root=self.root)
                self._register(fix_id, handler)
                with patch("Survey.action_fix_hook.log_debug") as debug:
                    returned = self._run(FakeDom((self.selector,)))
                self.assertIs(returned, ActionFixOutcome.HANDLED_FAILURE)
                decisions = [call.args[1] for call in debug.call_args_list
                             if call.args[1].startswith(("selected fix_id=", "verdict="))]
                self.assertEqual(decisions, [f"selected fix_id={fix_id}", f"verdict=HANDLED_FAILURE reason={reason}"])
                self.assertNotIn("answer=secret", str(debug.call_args_list))

        now = [0.0]

        def late_handler(*_args: object) -> ActionFixOutcome:
            now[0] = 1.0
            return ActionFixOutcome.DECLINED

        self.registry = ExternalFixRegistry(root=self.root)
        self._register("fix_late_budget", late_handler)
        with (
            patch("Survey.action_fix_hook.time.monotonic", side_effect=lambda: now[0]),
            patch("Survey.action_fix_hook.log_debug") as debug,
        ):
            returned = self._run(FakeDom((self.selector,)))
        self.assertIs(returned, ActionFixOutcome.HANDLED_FAILURE)
        decisions = [call.args[1] for call in debug.call_args_list
                     if call.args[1].startswith(("selected fix_id=", "verdict="))]
        self.assertEqual(decisions, ["selected fix_id=fix_late_budget", "verdict=HANDLED_FAILURE reason=post_handler_timeout"])

        now[0] = 0.0

        def late_failure(*_args: object) -> ActionFixOutcome:
            now[0] = 1.0
            return ActionFixOutcome.HANDLED_FAILURE

        self.registry = ExternalFixRegistry(root=self.root)
        self._register("fix_late_failure", late_failure)
        with (
            patch("Survey.action_fix_hook.time.monotonic", side_effect=lambda: now[0]),
            patch("Survey.action_fix_hook.log_debug") as debug,
        ):
            returned = self._run(FakeDom((self.selector,)))
        self.assertIs(returned, ActionFixOutcome.HANDLED_FAILURE)
        decisions = [call.args[1] for call in debug.call_args_list
                     if call.args[1].startswith(("selected fix_id=", "verdict="))]
        self.assertEqual(decisions, ["selected fix_id=fix_late_failure", "verdict=HANDLED_FAILURE reason=post_handler_timeout"])

    def test_context_exit_exception_logs_the_final_returned_failure(self) -> None:
        class BrokenContext:
            def __enter__(self) -> bool:
                return True

            def __exit__(self, *_args: object) -> None:
                raise RuntimeError("context exit failed")

        self._register("fix_context_exit", Mock(return_value=ActionFixOutcome.HANDLED_SUCCESS))
        with (
            patch("Survey.action_fix_hook.switch_to_frame_chain", return_value=BrokenContext()),
            patch("Survey.action_fix_hook.log_debug") as debug,
        ):
            returned = self._run(FakeDom((self.selector,)), frame_chain=[0])
        self.assertIs(returned, ActionFixOutcome.HANDLED_FAILURE)
        decisions = [call.args[1] for call in debug.call_args_list
                     if call.args[1].startswith(("selected fix_id=", "verdict="))]
        self.assertEqual(decisions, ["selected fix_id=fix_context_exit", "verdict=HANDLED_FAILURE reason=handler_exception"])

    def test_action_after_is_rejected_and_ambiguity_runs_no_handler(self) -> None:
        handler = Mock(return_value=ActionFixOutcome.HANDLED_SUCCESS)
        with self.assertRaises(FixRegistryError):
            self._register("fix_after", handler, position="after")
        self.assertEqual(self.registry.candidates(
            stage="action", anchor_function_id="action_dispatcher.py::execute_action", position="after"
        ), ())

        self._register("fix_one", handler)
        self._register("fix_two", handler)
        self.assertIs(self._run(FakeDom((self.selector,))), ActionFixOutcome.DECLINED)
        handler.assert_not_called()

    def test_handler_contract_exception_and_budget_prevent_fallback(self) -> None:
        def handler(_driver: object, _dom: object, action: object) -> ActionFixOutcome:
            self.events.append("handled")
            self.assertEqual(action["value"], "row || col")
            with self.assertRaises(TypeError):
                action["value"] = "changed"
            return ActionFixOutcome.HANDLED_SUCCESS

        self._register("fix_success", handler)
        self.assertIs(self._run(FakeDom((self.selector,))), ActionFixOutcome.HANDLED_SUCCESS)
        self.assertEqual(self.events, ["handled"])

        self.registry = ExternalFixRegistry(root=self.root)
        self._register("fix_invalid", lambda *_: True)
        self.assertIs(self._run(FakeDom((self.selector,))), ActionFixOutcome.HANDLED_FAILURE)

        self.registry = ExternalFixRegistry(root=self.root)
        def broken_handler(*_args: object) -> ActionFixOutcome:
            self.events.append("click")
            raise RuntimeError("answer=secret")
        self._register("fix_exception", broken_handler)
        with patch("Survey.action_fix_hook.log_debug") as debug:
            self.assertIs(self._run(FakeDom((self.selector,))), ActionFixOutcome.HANDLED_FAILURE)
        self.assertNotIn("answer=secret", str(debug.call_args_list))

        self.registry = ExternalFixRegistry(root=self.root)
        def slow_handler(*_args: object) -> ActionFixOutcome:
            time.sleep(0.02)
            return ActionFixOutcome.DECLINED
        self._register("fix_slow", slow_handler)
        self.assertIs(
            self._run(FakeDom((self.selector,)), budget_s=0.005), ActionFixOutcome.HANDLED_FAILURE
        )

        self.registry = ExternalFixRegistry(root=self.root)
        before_budget_handler = Mock(return_value=ActionFixOutcome.HANDLED_SUCCESS)
        self._register("fix_budget", before_budget_handler)
        with patch("Survey.action_fix_hook.log_debug") as debug:
            self.assertIs(self._run(FakeDom((self.selector,)), budget_s=0), ActionFixOutcome.DECLINED)
        before_budget_handler.assert_not_called()
        self.assertTrue(any("budget dépassé avant handler" in str(call)
                            for call in debug.call_args_list))

    def test_frame_context_and_cta_intercept(self) -> None:
        frame = FakeDom((self.selector,))
        page = FakeDom()
        page.main_frame = FakeDom()
        page.main_frame.child_frames = [frame]
        seen: list[object] = []
        self._register("fix_frame", lambda _driver, dom, _action: seen.append(dom) or ActionFixOutcome.HANDLED_SUCCESS)
        self.assertIs(self._run(page, frame_chain=[0]), ActionFixOutcome.HANDLED_SUCCESS)
        self.assertEqual(seen, [frame])
        self.assertEqual(page.queries, [])
        self.assertEqual(page._current_frame, page)

        with patch("Survey.action_fix_hook.is_cta_intercept_only", return_value=True):
            self.assertIs(
                run_action_fix_hook(page, self.action, registry=self.registry, frame_chain=[0]),
                ActionFixOutcome.DECLINED,
            )
        self.assertEqual(seen, [frame])

    def test_candidate_cap_skips_all_handlers(self) -> None:
        handler = Mock(return_value=ActionFixOutcome.HANDLED_SUCCESS)
        for index in range(9):
            self._register(f"fix_{index:03d}", handler)
        self.assertIs(self._run(FakeDom((self.selector,))), ActionFixOutcome.DECLINED)
        handler.assert_not_called()

    def test_real_dispatcher_seam_success_decline_and_uncertain_failure(self) -> None:
        import Survey.action_dispatcher as dispatcher

        dom = FakeDom((self.selector,))
        instruction = "row || col //// matrix //// row"
        legacy = Mock(return_value=True)

        def dispatch() -> bool:
            with (
                patch.object(dispatcher, "run_action_fix_hook", partial(run_action_fix_hook, registry=self.registry)),
                patch.object(dispatcher, "_try_gridclick_matrix_set", legacy),
                patch.object(dispatcher, "log_info"),
                patch("Survey.action_fix_hook.is_cta_intercept_only", return_value=False),
            ):
                return dispatcher.execute_action(dom, instruction)

        self.assertTrue(dispatch())
        legacy.assert_called_once()

        def click_once(driver: FakeDom, _dom: object, _action: object) -> ActionFixOutcome:
            driver.clicks += 1
            return ActionFixOutcome.HANDLED_SUCCESS
        self._register("fix_dispatch", click_once)
        legacy.reset_mock()
        self.assertTrue(dispatch())
        legacy.assert_not_called()
        self.assertEqual(dom.clicks, 1)

        self.registry = ExternalFixRegistry(root=self.root)
        self._register("fix_decline", lambda *_: ActionFixOutcome.DECLINED)
        self.assertTrue(dispatch())
        legacy.assert_called_once()

        self.registry = ExternalFixRegistry(root=self.root)
        self._register("fix_failed", lambda *_: ActionFixOutcome.HANDLED_FAILURE)
        legacy.reset_mock()
        self.assertFalse(dispatch())
        legacy.assert_not_called()

        self.registry = ExternalFixRegistry(root=self.root)
        def uncertain(*_args: object) -> ActionFixOutcome:
            self.events.append("click")
            raise RuntimeError("after click")
        self._register("fix_uncertain", uncertain)
        legacy.reset_mock()
        self.assertFalse(dispatch())
        legacy.assert_not_called()
        self.assertEqual(self.events, ["click"])

        legacy.reset_mock()
        with (
            patch.object(dispatcher, "run_action_fix_hook", partial(run_action_fix_hook, registry=self.registry)),
            patch.object(dispatcher, "_try_gridclick_matrix_set", legacy),
            patch.object(dispatcher, "log_info"),
            patch("Survey.action_fix_hook.is_cta_intercept_only", return_value=True),
        ):
            self.assertTrue(dispatcher.execute_action(dom, instruction))
        legacy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
