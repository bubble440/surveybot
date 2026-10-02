from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Survey.autofix import context_selector as cs

_BASE = ["Survey/action_dispatcher.py", "Survey/input_handler.py"]


class ContextSelectorActionBaselineTests(unittest.TestCase):
    """stage="action" : fichiers de base toujours retenus, placés avant ceux de la Phase 4."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Faux dépôt : _REPO_ROOT est redirigé pour que le test ne dépende pas du vrai code.
        self.repo = self.root / "repo"
        self.repo.mkdir()
        patcher = patch.object(cs, "_REPO_ROOT", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _touch(self, rel: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthétique\n", encoding="utf-8")

    def _all_base_files(self) -> None:
        for rel in _BASE + ["Survey/input_radio.py", "Survey/input_checkbox.py", "Survey/input_text.py"]:
            self._touch(rel)

    def _case(
        self, *, stage: str, itype: str | None, phase4: list[str] | None = None,
        case_id: str = "20990101_000000_synthetic",
    ) -> Path:
        diag_dir = self.root / "diagnoses" / case_id
        diag_dir.mkdir(parents=True)
        (self.root / "failure_cases" / case_id).mkdir(parents=True)
        (self.root / "failure_cases" / case_id / "manifest.json").write_text("{}", encoding="utf-8")
        diagnosis = {
            "case_id": case_id,
            "stage": stage,
            "itype": itype,
            "modules_likely_involved": [
                {"module": m, "matched_signals": ["synthetic"], "memory_entries": []}
                for m in (phase4 or [])
            ],
        }
        (diag_dir / "diagnosis.json").write_text(json.dumps(diagnosis), encoding="utf-8")
        return diag_dir

    def _select(self, diag_dir: Path, **kwargs):
        return cs.select_context(diag_dir, failure_cases_root=self.root / "failure_cases", **kwargs)

    def test_action_radio_selects_base_files_in_order(self) -> None:
        self._all_base_files()
        result = self._select(self._case(stage="action", itype="radio"))
        self.assertEqual(
            [f.file for f in result.code_files],
            _BASE + ["Survey/input_radio.py"],
        )
        self.assertTrue(all(f.source == "stage_action_baseline" for f in result.code_files))
        self.assertTrue(result.mapping_table_found)
        self.assertFalse(result.truncated)
        self.assertEqual(result.code_files_found, 3)

    def test_action_text_family_maps_to_input_text(self) -> None:
        self._all_base_files()
        for n, itype in enumerate(("text", "textarea", "number", "open")):
            with self.subTest(itype=itype):
                diag = self._case(stage="action", itype=itype, case_id=f"20990101_00000{n}_text")
                result = self._select(diag)
                self.assertEqual(
                    [f.file for f in result.code_files],
                    _BASE + ["Survey/input_text.py"],
                )

    def test_action_unknown_itype_selects_only_the_two_base_files(self) -> None:
        self._all_base_files()
        for n, itype in enumerate(("totally_unknown", None)):
            with self.subTest(itype=itype):
                diag = self._case(stage="action", itype=itype, case_id=f"20990101_00000{n}_unknown")
                result = self._select(diag)
                self.assertEqual([f.file for f in result.code_files], _BASE)

    def test_phase4_module_duplicating_a_base_file_appears_once_with_baseline_source(self) -> None:
        self._all_base_files()
        self._touch("Survey/dom_analyzer.py")
        result = self._select(self._case(
            stage="action", itype="radio",
            phase4=["Survey/input_radio.py", "Survey/dom_analyzer.py"],
        ))
        files = [f.file for f in result.code_files]
        self.assertEqual(files, _BASE + ["Survey/input_radio.py", "Survey/dom_analyzer.py"])
        self.assertEqual(files.count("Survey/input_radio.py"), 1)
        radio = next(f for f in result.code_files if f.file == "Survey/input_radio.py")
        self.assertEqual(radio.source, "stage_action_baseline")

    def test_cap_drops_phase4_files_never_the_base_files(self) -> None:
        self._all_base_files()
        phase4 = [f"Survey/dom_module_{i:02d}.py" for i in range(10)]
        for rel in phase4:
            self._touch(rel)
        result = self._select(self._case(stage="action", itype="radio", phase4=phase4))
        files = [f.file for f in result.code_files]
        self.assertEqual(result.code_files_found, 13)  # 3 de base + 10 de la Phase 4
        self.assertTrue(result.truncated)
        self.assertEqual(len(files), cs.DEFAULT_CODE_FILES_CAP)
        self.assertEqual(files[:3], _BASE + ["Survey/input_radio.py"])
        self.assertEqual(files[3:], phase4[:5])
        self.assertEqual(result.dropped_files, phase4[5:])

    def test_missing_base_file_is_ignored_with_a_warning(self) -> None:
        self._touch("Survey/action_dispatcher.py")
        self._touch("Survey/input_radio.py")  # input_handler.py volontairement absent
        result = self._select(self._case(stage="action", itype="radio"))
        self.assertEqual(
            [f.file for f in result.code_files],
            ["Survey/action_dispatcher.py", "Survey/input_radio.py"],
        )
        self.assertTrue(any("input_handler.py" in w for w in result.warnings))

    def test_non_action_stage_is_unchanged(self) -> None:
        self._all_base_files()
        self._touch("Survey/dom_analyzer.py")
        result = self._select(self._case(
            stage="extraction", itype="radio", phase4=["Survey/dom_analyzer.py"],
        ))
        self.assertEqual([f.file for f in result.code_files], ["Survey/dom_analyzer.py"])
        self.assertEqual(result.code_files[0].source, "phase4_modules_likely_involved")
        self.assertFalse(result.mapping_table_found)

    def test_non_action_stage_without_phase4_modules_still_selects_nothing(self) -> None:
        self._all_base_files()
        result = self._select(self._case(stage="extraction", itype="radio"))
        self.assertEqual(result.code_files, [])
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()