from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Survey.autofix.static_validator import validate_patch_static
from Survey.extractor_integrity import _hash_function


class StaticValidatorActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.package = self.repo / "surveybot"
        self.survey = self.package / "Survey"
        self.tests = self.package / "tests"
        self.survey.mkdir(parents=True)
        self.tests.mkdir()
        (self.package / "tools").mkdir()
        source_survey = Path(__file__).resolve().parents[1] / "Survey"
        (self.survey / "__init__.py").write_text("", encoding="utf-8")
        for name in ("extractor_integrity.py", "external_fix_registry.py",
                     "external_fix_loader.py", "log_utils.py"):
            shutil.copyfile(source_survey / name, self.survey / name)
        (self.survey / "core.py").write_text(
            "def core():\n    return []\n\ndef anchor():\n    return []\n", encoding="utf-8",
        )
        self.core_hash = _hash_function(self.survey, "core.py", "core")
        (self.survey / "extractor_integrity.json").write_text(json.dumps({
            "core.py::core": {"hash": self.core_hash},
        }), encoding="utf-8")
        (self.tests / "__init__.py").write_text("", encoding="utf-8")
        self._git("init", "-q")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.invalid")
        self._git("add", ".")
        self._git("commit", "-qm", "baseline")
        self.base_sha = self._git("rev-parse", "HEAD").stdout.strip()
        self.manifest = Path(self.temp.name) / "worktree.json"
        self.manifest.write_text(json.dumps({
            "case_id": "synthetic_case", "branch": "autofix/synthetic_case",
            "base_sha": self.base_sha, "worktree_path": str(self.repo),
        }), encoding="utf-8")

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.repo, text=True, capture_output=True, check=True,
        )

    def _add_fix(self, *, bad_hash: bool = False, loaded: bool = True) -> None:
        expected_hash = "0" * 64 if bad_hash else self.core_hash
        (self.survey / "external_fix_sample.py").write_text(
            "from Survey.external_fix_registry import DomCondition, ExternalFix\n"
            "def handler(*_args):\n    return None\n"
            "FIXES = (ExternalFix(\n"
            "    fix_id='synthetic_fix', core_function_id='core.py::core',\n"
            f"    expected_core_hash='{expected_hash}',\n"
            "    anchor_function_id='core.py::anchor', position='before',\n"
            "    case_ids=('synthetic_case',), condition=DomCondition(('div.synthetic',)),\n"
            "    handler=handler,\n"
            "),)\n",
            encoding="utf-8",
        )
        (self.tests / "test_external_fix_sample.py").write_text(
            "from Survey.external_fix_sample import FIXES\n"
            "def test_synthetic_guard():\n"
            "    assert FIXES[0].condition.required_selectors == ('div.synthetic',)\n",
            encoding="utf-8",
        )
        if loaded:
            loader = self.survey / "external_fix_loader.py"
            loader.write_text(loader.read_text(encoding="utf-8") +
                "\ndef _import_sample():\n"
                "    from Survey.external_fix_sample import FIXES\n"
                "    return FIXES\n"
                "MODULE_IMPORTS = (_import_sample,)\n", encoding="utf-8")

    def _validate(self):
        # Ruff n'est pas requis pour isoler la vérification d'activation ici.
        with patch("Survey.autofix.static_validator._check_lint", return_value={"ok": True}):
            return validate_patch_static(self.manifest)

    def test_valid_fix_registers_and_is_candidate(self) -> None:
        self._add_fix()
        result = self._validate()
        self.assertEqual(result.verdict, "ACCEPTED", result.reasons)
        self.assertEqual(result.checks["activation"]["registered"], ["synthetic_fix"])
        self.assertFalse(result.checks["activation"]["skipped"])
        self.assertTrue(result.checks["tests"]["executed"])

    def test_wrong_hash_rejected_with_fix_id_and_cause(self) -> None:
        self._add_fix(bad_hash=True)
        result = self._validate()
        self.assertEqual(result.verdict, "REJECTED")
        self.assertIn("fix_id=synthetic_fix", result.checks["activation"]["error"])
        self.assertIn("FixRegistryError", result.checks["activation"]["error"])
        self.assertIn("hash core différent", result.checks["activation"]["error"])

    def test_unrelated_change_skips_activation(self) -> None:
        (self.survey / "ordinary.py").write_text("VALUE = 1\n", encoding="utf-8")
        result = self._validate()
        self.assertEqual(result.verdict, "ACCEPTED", result.reasons)
        self.assertTrue(result.checks["activation"]["skipped"])

    def test_new_project_root_file_is_rejected(self) -> None:
        (self.package / "_tmp_check_fix.py").write_text("VALUE = 1\n", encoding="utf-8")
        result = self._validate()
        self.assertEqual(result.verdict, "REJECTED")
        self.assertIn("_tmp_check_fix.py", result.checks["root_files"]["error"])

    def test_unlisted_fix_module_is_rejected(self) -> None:
        self._add_fix(loaded=False)
        result = self._validate()
        self.assertEqual(result.verdict, "REJECTED")
        self.assertIn("absent de MODULE_IMPORTS", result.checks["activation"]["error"])

    def test_fix_without_associated_test_is_rejected(self) -> None:
        self._add_fix()
        (self.tests / "test_external_fix_sample.py").unlink()
        result = self._validate()
        self.assertEqual(result.verdict, "REJECTED")
        self.assertIn("test associé absent", result.checks["tests"]["error"])
