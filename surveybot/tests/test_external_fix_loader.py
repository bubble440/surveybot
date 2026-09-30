from __future__ import annotations

import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sys import modules
from unittest.mock import Mock, patch

import Survey.action_fix_hook as action_hook
import Survey.external_fix_loader as loader
import Survey.extraction_fix_hook as extraction_hook
from Survey.action_fix_hook import ActionFixOutcome
from Survey.external_fix_registry import DomCondition, EXTERNAL_FIXES, ExternalFix, ExternalFixRegistry
from Survey.extractor_integrity import _hash_function


class FakeDom:
    def query_selector(self, selector: str) -> object | None:
        return object() if selector == "div.synthetic-fix" else None


class ExternalFixLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(loader.__file__).resolve().parent
        self.anchor = "dom_extractors_misc.py::_extract_consent_modal_radio_block"
        self.blocks = [{"itype": "radio", "question": "synthetic", "options": ["A"]}]
        registry_patch = patch.dict(EXTERNAL_FIXES._fixes, clear=True)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)

    def _fix(self) -> ExternalFix:
        return ExternalFix(
            fix_id="synthetic_loader_fix", core_function_id=self.anchor,
            expected_core_hash=_hash_function(
                self.root, "dom_extractors_misc.py", "_extract_consent_modal_radio_block"
            ),
            anchor_function_id=self.anchor, position="before", case_ids=("synthetic_case",),
            condition=DomCondition(("div.synthetic-fix",)),
            handler=lambda _driver, _chain: self.blocks,
        )

    def _run_extraction(self, strategy: Mock, *, registry: ExternalFixRegistry = EXTERNAL_FIXES) -> object:
        return extraction_hook.run_extraction_strategy_with_fixes(
            FakeDom(), None, anchor_function_id=self.anchor, strategy=strategy, registry=registry,
        )

    def test_empty_module_list_keeps_common_registry_empty_and_hooks_normal(self) -> None:
        with patch.object(loader, "MODULE_IMPORTS", ()), patch.object(loader, "_loaded", False):
            strategy = Mock(return_value=self.blocks)
            self.assertEqual(self._run_extraction(strategy), self.blocks)
            strategy.assert_called_once()
            self.assertIs(action_hook.run_action_fix_hook(FakeDom(), {}), ActionFixOutcome.DECLINED)
            self.assertEqual(EXTERNAL_FIXES.candidates(
                stage="extraction", anchor_function_id=self.anchor, position="before",
            ), ())

    def test_static_module_import_activates_fix_via_common_hook_once(self) -> None:
        module = types.ModuleType("Survey.synthetic_fix_module")
        module.FIXES = (self._fix(),)
        importer = Mock(side_effect=lambda: self._import_synthetic_fixes())
        with (patch.dict(modules, {"Survey.synthetic_fix_module": module}),
              patch.object(loader, "MODULE_IMPORTS", (importer,)),
              patch.object(loader, "_loaded", False)):
            strategy = Mock(return_value=[])
            self.assertEqual(self._run_extraction(strategy), self.blocks)
            self.assertEqual(self._run_extraction(strategy), self.blocks)
            strategy.assert_not_called()
            importer.assert_called_once()
            self.assertEqual(len(EXTERNAL_FIXES.candidates(
                stage="extraction", anchor_function_id=self.anchor, position="before",
            )), 1)

    @staticmethod
    def _import_synthetic_fixes() -> tuple[ExternalFix, ...]:
        from Survey.synthetic_fix_module import FIXES
        return FIXES

    def test_failed_import_or_declaration_does_not_block_other_fix(self) -> None:
        def failed_import() -> tuple[ExternalFix, ...]:
            raise ImportError("session=secret")

        importer = Mock(return_value=(object(), self._fix()))
        with (
            patch.object(loader, "MODULE_IMPORTS", (failed_import, importer)),
            patch.object(loader, "_loaded", False),
            patch("Survey.external_fix_loader.log_debug") as loader_debug,
            patch("Survey.external_fix_registry.log_debug") as registry_debug,
        ):
            self.assertEqual(self._run_extraction(Mock(return_value=[])), self.blocks)
            importer.assert_called_once()
            self.assertNotIn("session=secret", str(loader_debug.call_args_list))
            self.assertNotIn("session=secret", str(registry_debug.call_args_list))
            self.assertEqual(len(EXTERNAL_FIXES.candidates(
                stage="extraction", anchor_function_id=self.anchor, position="before",
            )), 1)

    def test_common_action_hook_loads_list_before_evaluation(self) -> None:
        anchor = "action_dispatcher.py::execute_action"
        fix = ExternalFix(
            fix_id="synthetic_action_loader_fix", core_function_id=anchor,
            expected_core_hash=_hash_function(self.root, "action_dispatcher.py", "execute_action"),
            anchor_function_id=anchor, position="before", case_ids=("synthetic_case",),
            condition=DomCondition(("div.synthetic-fix",)),
            handler=lambda *_: ActionFixOutcome.HANDLED_SUCCESS, stage="action",
        )
        importer = Mock(return_value=(fix,))
        with (patch.object(loader, "MODULE_IMPORTS", (importer,)),
              patch.object(loader, "_loaded", False),
              patch.object(action_hook, "is_cta_intercept_only", return_value=False)):
            self.assertIs(action_hook.run_action_fix_hook(FakeDom(), {}),
                          ActionFixOutcome.HANDLED_SUCCESS)
            importer.assert_called_once()

    def test_concurrent_triggers_import_only_once(self) -> None:
        importer = Mock(side_effect=lambda: time.sleep(0.01) or (self._fix(),))
        with patch.object(loader, "MODULE_IMPORTS", (importer,)), patch.object(loader, "_loaded", False):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: loader.load_external_fixes_once(), range(8)))
            importer.assert_called_once()
            self.assertEqual(len(EXTERNAL_FIXES.candidates(
                stage="extraction", anchor_function_id=self.anchor, position="before",
            )), 1)

    def test_injected_registries_do_not_trigger_loading_in_either_hook(self) -> None:
        class FalseyRegistry(ExternalFixRegistry):
            def __bool__(self) -> bool:
                return False

        injected = FalseyRegistry(root=self.root)
        with (
            patch.object(extraction_hook, "load_external_fixes_once") as extraction_load,
            patch.object(action_hook, "load_external_fixes_once") as action_load,
        ):
            strategy = Mock(return_value=self.blocks)
            self.assertEqual(self._run_extraction(strategy, registry=injected), self.blocks)
            self.assertIs(action_hook.run_action_fix_hook(FakeDom(), {}, registry=injected),
                          ActionFixOutcome.DECLINED)
            extraction_load.assert_not_called()
            action_load.assert_not_called()
