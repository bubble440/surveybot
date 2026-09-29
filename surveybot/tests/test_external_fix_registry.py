from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from Survey.external_fix_registry import DomCondition, ExternalFix, ExternalFixRegistry, FixRegistryError
from Survey.extractor_integrity import _hash_function


class ExternalFixRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Survey"
        self.root.mkdir()
        (self.root / "sample.py").write_text(
            "def core():\n    return []\n\ndef anchor():\n    return []\n", encoding="utf-8"
        )
        self.core_hash = _hash_function(self.root, "sample.py", "core")
        registry = {"sample.py::core": {"hash": self.core_hash}}
        (self.root / "extractor_integrity.json").write_text(json.dumps(registry), encoding="utf-8")
        self.calls = 0

    def _fix(self, fix_id: str = "fix_alpha", position: str = "before") -> ExternalFix:
        def handler(*_args: object) -> object:
            self.calls += 1
            return None

        return ExternalFix(
            fix_id=fix_id,
            core_function_id="sample.py::core",
            expected_core_hash=self.core_hash,
            anchor_function_id="sample.py::anchor",
            position=position,
            case_ids=("case_001",),
            condition=DomCondition(("div.survey-widget input[type=radio]", "div.answer-row"), ("div.disabled",)),
            handler=handler,
        )

    def test_stable_identity_and_flat_before_after_lookup(self) -> None:
        first = ExternalFixRegistry(root=self.root)
        first.register(self._fix("fix_beta"))
        first.register(self._fix("fix_alpha"))
        first.register(self._fix("fix_gamma", "after"))
        before = first.candidates(stage="extraction", anchor_function_id="sample.py::anchor", position="before")
        after = first.candidates(stage="extraction", anchor_function_id="sample.py::anchor", position="after")
        self.assertEqual([fix.fix_id for fix in before], ["fix_alpha", "fix_beta"])
        self.assertEqual([fix.fix_id for fix in after], ["fix_gamma"])
        self.assertTrue(all(fix.core_function_id == "sample.py::core" for fix in (*before, *after)))
        self.assertEqual(first.get("fix_alpha"), before[0])
        self.assertIsNone(first.get("fix_absent"))
        self.assertEqual(self.calls, 0)  # Le registre n'évalue ni garde ni handler.

        second = ExternalFixRegistry(root=self.root)
        second.register(self._fix("fix_alpha"))
        self.assertEqual(second.candidates(
            stage="extraction", anchor_function_id="sample.py::anchor", position="before"
        )[0].fix_id, before[0].fix_id)

    def test_invalid_entries_leave_registry_unchanged(self) -> None:
        registry = ExternalFixRegistry(root=self.root)
        valid = self._fix()
        registry.register(valid)
        invalid = (
            valid,  # Identifiant dupliqué.
            replace(valid, fix_id="fix_unknown", core_function_id="sample.py::anchor"),
            replace(valid, fix_id="fix_version", expected_core_hash="0" * 64),
            replace(valid, fix_id="fix_anchor", anchor_function_id="sample.py::missing"),
            replace(valid, fix_id="fix_escape", anchor_function_id="../../outside.py::outside"),
            replace(valid, fix_id="fix_position", position="middle"),
            replace(valid, fix_id="fix_case", case_ids=("user@example.com",)),
            replace(valid, fix_id="fix_guard", condition=DomCondition(("div",))),
            replace(valid, fix_id="fix_question", condition=DomCondition(("div.answer-row What is your age",))),
            replace(valid, fix_id="fix_operator", condition=DomCondition((">",))),
            replace(valid, fix_id="fix_empty", condition=DomCondition(())),
            replace(valid, fix_id="fix_many", condition=DomCondition(("div.answer-row",) * 9)),
            replace(valid, fix_id="fix_url", condition=DomCondition(("a[href='https://example.test/?session=secret']",))),
            replace(valid, fix_id="fix_handler", handler=None),
        )
        for entry in invalid:
            with self.subTest(fix_id=entry.fix_id), self.assertRaises(FixRegistryError):
                registry.register(entry)
        self.assertEqual([fix.fix_id for fix in registry.candidates(
            stage="extraction", anchor_function_id="sample.py::anchor", position="before"
        )], ["fix_alpha"])
        self.assertFalse(registry.try_register(replace(valid, fix_id="fix_broken", position="middle")))

    def test_registry_capacity_is_bounded(self) -> None:
        registry = ExternalFixRegistry(root=self.root)
        for index in range(64):
            registry.register(self._fix(f"fix_{index:03d}"))
        with self.assertRaises(FixRegistryError):
            registry.register(self._fix("fix_extra"))
        self.assertEqual(len(registry.candidates(
            stage="extraction", anchor_function_id="sample.py::anchor", position="before"
        )), 64)

    def test_missing_baseline_disables_registration_without_import_failure(self) -> None:
        (self.root / "extractor_integrity.json").unlink()
        registry = ExternalFixRegistry(root=self.root)
        with self.assertRaises(FixRegistryError):
            registry.register(self._fix())
        self.assertEqual(registry.candidates(
            stage="extraction", anchor_function_id="sample.py::anchor", position="before"
        ), ())

    def test_changed_core_is_not_registered_under_old_baseline(self) -> None:
        (self.root / "sample.py").write_text(
            "def core():\n    return [1]\n\ndef anchor():\n    return []\n", encoding="utf-8"
        )
        registry = ExternalFixRegistry(root=self.root)
        with self.assertRaisesRegex(FixRegistryError, "code core différent"):
            registry.register(self._fix())
        self.assertFalse(registry.try_register(self._fix()))


if __name__ == "__main__":
    unittest.main()
