from __future__ import annotations

import json
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import Mock, patch

from Survey.external_fix_registry import DomCondition, ExternalFix, ExternalFixRegistry
from Survey.extractor_integrity import _hash_function
from Survey.extraction_fix_hook import run_extraction_strategy_with_fixes


class FakeDom:
    def __init__(self, present: tuple[str, ...] = (), *, broken: bool = False) -> None:
        self.present = set(present)
        self.broken = broken
        self.queries: list[str] = []

    def query_selector(self, selector: str) -> object | None:
        self.queries.append(selector)
        if self.broken:
            raise RuntimeError("session=secret")
        return object() if selector in self.present else None

    def query_selector_all(self, _selector: str) -> list:
        return []

    def evaluate(self, *_args: object) -> None:
        raise RuntimeError("synthetic DOM")


class ExtractionFixHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Survey"
        self.root.mkdir()
        (self.root / "sample.py").write_text(
            "def core():\n    return []\n\ndef anchor():\n    return []\n", encoding="utf-8"
        )
        self.core_hash = _hash_function(self.root, "sample.py", "core")
        (self.root / "extractor_integrity.json").write_text(
            json.dumps({"sample.py::core": {"hash": self.core_hash}}), encoding="utf-8"
        )
        self.registry = ExternalFixRegistry(root=self.root)
        self.anchor = "sample.py::anchor"
        self.core_block = [{"itype": "radio", "question": "core fixture", "options": ["A"]}]
        self.fix_block = [{"itype": "radio", "question": "fix fixture", "options": ["B"]}]
        self.events: list[str] = []

    def _register(
        self, fix_id: str = "fix_alpha", position: str = "before",
        *, excluded: tuple[str, ...] = (), result: object = None,
    ) -> None:
        def handler(_driver: object, _frame_chain: object) -> object:
            self.events.append(fix_id)
            return self.fix_block if result is None else result

        self.registry.register(ExternalFix(
            fix_id=fix_id,
            core_function_id="sample.py::core",
            expected_core_hash=self.core_hash,
            anchor_function_id=self.anchor,
            position=position,
            case_ids=("synthetic_case",),
            condition=DomCondition(("div.synthetic-fix", "span.synthetic-signal"), excluded),
            handler=handler,
        ))

    def _run(self, driver: FakeDom, strategy: object, *, budget_s: float = 0.25) -> object:
        return run_extraction_strategy_with_fixes(
            driver, None, anchor_function_id=self.anchor, strategy=strategy,
            registry=self.registry, budget_s=budget_s,
        )

    def test_before_precedes_core_and_requires_precise_dom(self) -> None:
        self._register()
        strategy = Mock(return_value=self.core_block)
        present = ("div.synthetic-fix", "span.synthetic-signal")
        self.assertEqual(self._run(FakeDom(present), strategy), self.fix_block)
        strategy.assert_not_called()
        self.assertEqual(self.events, ["fix_alpha"])

        self.events.clear()
        self.assertEqual(self._run(FakeDom((present[0],)), strategy), self.core_block)
        strategy.assert_called_once()
        self.assertEqual(self.events, [])

    def test_exclusion_and_after_only_when_core_is_empty(self) -> None:
        self._register("fix_after", "after", excluded=("div.synthetic-disabled",))
        present = ("div.synthetic-fix", "span.synthetic-signal")
        strategy = Mock(side_effect=lambda *_: self.events.append("core") or [])
        self.assertEqual(self._run(FakeDom(present), strategy), self.fix_block)
        self.assertEqual(self.events, ["core", "fix_after"])

        self.events.clear()
        strategy = Mock(return_value=self.core_block)
        self.assertEqual(self._run(FakeDom(present), strategy), self.core_block)
        self.assertEqual(self.events, [])

        self.assertEqual(self._run(FakeDom((*present, "div.synthetic-disabled")), Mock(return_value=[])), [])
        self.assertEqual(self.events, [])

    def test_ambiguity_errors_invalid_result_and_budget_fail_open(self) -> None:
        present = ("div.synthetic-fix", "span.synthetic-signal")
        self._register("fix_alpha")
        self._register("fix_beta")
        strategy = Mock(return_value=self.core_block)
        with patch("Survey.extraction_fix_hook.log_debug") as debug:
            self.assertEqual(self._run(FakeDom(present), strategy), self.core_block)
        self.assertEqual(self.events, [])
        self.assertTrue(any("ambigus" in str(call) for call in debug.call_args_list))
        strategy.assert_called_once()

        single = ExternalFixRegistry(root=self.root)
        single.register(self.registry.get("fix_alpha"))
        self.registry = single
        with patch("Survey.extraction_fix_hook.log_debug") as debug:
            self.assertEqual(self._run(FakeDom(present, broken=True), Mock(return_value=self.core_block)), self.core_block)
        self.assertNotIn("session=secret", str(debug.call_args_list))
        with patch("Survey.extraction_fix_hook.log_debug") as debug:
            self.assertEqual(self._run(FakeDom(present), Mock(return_value=self.core_block), budget_s=0), self.core_block)
        self.assertTrue(any(f"budget dépassé anchor={self.anchor} position=before" in str(call)
                            for call in debug.call_args_list))

        self.registry = ExternalFixRegistry(root=self.root)
        self._register("fix_bad", result=[{"itype": "radio", "question": "fixture", "options": "wrong"}])
        self.assertEqual(self._run(FakeDom(present), Mock(return_value=self.core_block)), self.core_block)

        self.registry = ExternalFixRegistry(root=self.root)
        def bad_handler(_driver: object, _chain: object) -> object:
            raise RuntimeError("answer=secret")
        fix = self.registry
        fix.register(ExternalFix(
            fix_id="fix_error", core_function_id="sample.py::core", expected_core_hash=self.core_hash,
            anchor_function_id=self.anchor, position="before", case_ids=("synthetic_case",),
            condition=DomCondition(("div.synthetic-fix",)), handler=bad_handler,
        ))
        with patch("Survey.extraction_fix_hook.log_debug") as debug:
            self.assertEqual(self._run(FakeDom(present), Mock(return_value=self.core_block)), self.core_block)
        self.assertNotIn("answer=secret", str(debug.call_args_list))

    def test_candidate_cap_and_handler_over_budget_keeps_valid_fix(self) -> None:
        present = ("div.synthetic-fix", "span.synthetic-signal")
        for index in range(9):
            self._register(f"fix_{index:03d}")
        self.assertEqual(self._run(FakeDom(present), Mock(return_value=self.core_block)), self.core_block)
        self.assertEqual(self.events, [])

        self.registry = ExternalFixRegistry(root=self.root)
        clock = [100.0]
        def slow_handler(_driver: object, _chain: object) -> object:
            clock[0] = 100.02
            return self.fix_block
        self.registry.register(ExternalFix(
            fix_id="fix_slow", core_function_id="sample.py::core", expected_core_hash=self.core_hash,
            anchor_function_id=self.anchor, position="before", case_ids=("synthetic_case",),
            condition=DomCondition(("div.synthetic-fix",)), handler=slow_handler,
        ))
        strategy = Mock(return_value=self.core_block)
        with patch("Survey.extraction_fix_hook.time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(self._run(FakeDom(present), strategy, budget_s=0.005), self.fix_block)
        strategy.assert_not_called()

    def test_finished_handler_keeps_valid_result_after_deadline_before_and_after(self) -> None:
        for position in ("before", "after"):
            with self.subTest(position=position):
                self.registry = ExternalFixRegistry(root=self.root)
                clock = [100.0]

                def handler(_driver: object, _chain: object) -> object:
                    clock[0] = 100.02
                    return self.fix_block

                self.registry.register(ExternalFix(
                    fix_id=f"fix_slow_{position}", core_function_id="sample.py::core",
                    expected_core_hash=self.core_hash, anchor_function_id=self.anchor,
                    position=position, case_ids=("synthetic_case",),
                    condition=DomCondition(("div.synthetic-fix",)), handler=handler,
                ))
                strategy = Mock(return_value=[])
                with patch("Survey.extraction_fix_hook.time.monotonic", side_effect=lambda: clock[0]), \
                     patch("Survey.extraction_fix_hook.log_debug") as debug:
                    result = self._run(FakeDom(("div.synthetic-fix",)), strategy, budget_s=0.005)
                self.assertEqual(result, self.fix_block)
                self.assertTrue(any("budget dépassé après handler" in str(call)
                                    for call in debug.call_args_list))
                self.assertEqual(strategy.call_count, 0 if position == "before" else 1)

    def test_failed_or_ambiguous_before_does_not_chain_into_after(self) -> None:
        present = ("div.synthetic-fix", "span.synthetic-signal")
        self._register("fix_before", result=[])
        self._register("fix_after", "after")
        strategy = Mock(side_effect=lambda *_: self.events.append("core") or [])
        self.assertEqual(self._run(FakeDom(present), strategy), [])
        self.assertEqual(self.events, ["fix_before", "core"])

        self.events.clear()
        self._register("fix_second_before")
        self.assertEqual(self._run(FakeDom(present), strategy), [])
        self.assertEqual(self.events, ["core"])

    def test_real_cascade_calls_the_selected_seam(self) -> None:
        import Survey.dom_analyzer as analyzer

        real_root = Path(analyzer.__file__).resolve().parent
        real_registry = ExternalFixRegistry(root=real_root)
        anchor = "dom_extractors_decipher.py::_extract_decipher_atmrating_blocks"
        core_hash = _hash_function(real_root, "dom_extractors_decipher.py", "_extract_decipher_atmrating_blocks")
        real_registry.register(ExternalFix(
            fix_id="fix_synthetic_cascade", core_function_id=anchor, expected_core_hash=core_hash,
            anchor_function_id=anchor, position="before", case_ids=("synthetic_case",),
            condition=DomCondition(("div.synthetic-fix",)),
            handler=lambda _driver, _chain: self.fix_block,
        ))
        strategy = Mock(return_value=self.core_block)
        with (
            patch.object(analyzer, "_extract_decipher_atmrating_blocks", strategy),
            patch.object(analyzer, "run_extraction_strategy_with_fixes", partial(
                run_extraction_strategy_with_fixes, registry=real_registry
            )),
        ):
            blocks = analyzer._analyze_dom_current_context(FakeDom(("div.synthetic-fix",)))
        self.assertEqual(blocks, self.fix_block)
        strategy.assert_not_called()

    def _consent_registry(self, *, position: str | None = None, handler: object = None) -> ExternalFixRegistry:
        import Survey.dom_analyzer as analyzer

        real_root = Path(analyzer.__file__).resolve().parent
        registry = ExternalFixRegistry(root=real_root)
        if position is not None:
            anchor = "dom_extractors_misc.py::_extract_consent_modal_radio_block"
            baseline = json.loads((real_root / "extractor_integrity.json").read_text(encoding="utf-8"))
            self.assertIn(anchor, baseline)
            core_hash = _hash_function(real_root, "dom_extractors_misc.py", "_extract_consent_modal_radio_block")
            registry.register(ExternalFix(
                fix_id="fix_synthetic_consent", core_function_id=anchor,
                expected_core_hash=core_hash, anchor_function_id=anchor,
                position=position, case_ids=("synthetic_case",),
                condition=DomCondition(("div.synthetic-fix",)), handler=handler,
            ))
        return registry

    def test_consent_cascade_empty_registry_keeps_strategy_and_continuation(self) -> None:
        import Survey.dom_analyzer as analyzer

        registry = self._consent_registry()
        strategy = Mock(return_value=self.core_block)
        next_strategy = Mock(return_value=self.fix_block)
        with (
            patch.object(analyzer, "_extract_consent_modal_radio_block", strategy),
            patch.object(analyzer, "_extract_mui_card_single_choice_block", next_strategy),
            patch.object(analyzer, "run_extraction_strategy_with_fixes", partial(
                run_extraction_strategy_with_fixes, registry=registry
            )),
        ):
            self.assertEqual(analyzer._analyze_dom_current_context(FakeDom()), self.core_block)
            strategy.assert_called_once()
            next_strategy.assert_not_called()

            strategy.reset_mock(return_value=True)
            strategy.return_value = []
            self.assertEqual(analyzer._analyze_dom_current_context(FakeDom()), self.fix_block)
            strategy.assert_called_once()
            next_strategy.assert_called_once()

    def test_consent_cascade_before_skips_strategy(self) -> None:
        import Survey.dom_analyzer as analyzer

        handler = Mock(return_value=self.fix_block)
        registry = self._consent_registry(position="before", handler=handler)
        strategy = Mock(return_value=self.core_block)
        with (
            patch.object(analyzer, "_extract_consent_modal_radio_block", strategy),
            patch.object(analyzer, "run_extraction_strategy_with_fixes", partial(
                run_extraction_strategy_with_fixes, registry=registry
            )),
        ):
            blocks = analyzer._analyze_dom_current_context(FakeDom(("div.synthetic-fix",)))
        self.assertEqual(blocks, self.fix_block)
        handler.assert_called_once()
        strategy.assert_not_called()

    def test_consent_cascade_after_only_when_strategy_empty(self) -> None:
        import Survey.dom_analyzer as analyzer

        handler = Mock(return_value=self.fix_block)
        registry = self._consent_registry(position="after", handler=handler)
        strategy = Mock(return_value=[])
        with (
            patch.object(analyzer, "_extract_consent_modal_radio_block", strategy),
            patch.object(analyzer, "run_extraction_strategy_with_fixes", partial(
                run_extraction_strategy_with_fixes, registry=registry
            )),
        ):
            self.assertEqual(
                analyzer._analyze_dom_current_context(FakeDom(("div.synthetic-fix",))),
                self.fix_block,
            )
            strategy.assert_called_once()
            handler.assert_called_once()

            strategy.reset_mock(return_value=True)
            strategy.return_value = self.core_block
            handler.reset_mock()
            self.assertEqual(
                analyzer._analyze_dom_current_context(FakeDom(("div.synthetic-fix",))),
                self.core_block,
            )
            strategy.assert_called_once()
            handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
