from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from Survey.autofix.bem_proposal import _detect_changed_files
from Survey.autofix.patch_commit import _check_bem_updated


class PatchCommitChangedPathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.package = self.repo / "surveybot"
        self.survey = self.package / "Survey"
        self.survey.mkdir(parents=True)
        (self.package / "tools").mkdir()
        (self.package / "tools" / ".keep").write_text("", encoding="utf-8")
        (self.survey / "BOT_EVOLUTION_MEMORY.md").write_text("# Mémoire\n", encoding="utf-8")
        (self.survey / "sample.py").write_text("def existing():\n    return 1\n", encoding="utf-8")
        self._git("init", "-q")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.invalid")
        self._git("add", ".")
        self._git("commit", "-qm", "baseline")
        self.base_sha = self._git("rev-parse", "HEAD").stdout.strip()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.repo, text=True, capture_output=True, check=True,
        )

    def _make_patch(self, *, update_memory: bool) -> None:
        # Un ajout déjà committé, un fichier suivi modifié et un non suivi :
        # _git_changed_paths doit tous les rendre dans sa première liste.
        (self.survey / "added.py").write_text("def added():\n    return 2\n", encoding="utf-8")
        self._git("add", "surveybot/Survey/added.py")
        self._git("commit", "-qm", "add module")
        (self.survey / "sample.py").write_text("def existing():\n    return 3\n", encoding="utf-8")
        (self.survey / "untracked.py").write_text("def untracked():\n    return 4\n", encoding="utf-8")
        if update_memory:
            (self.survey / "BOT_EVOLUTION_MEMORY.md").write_text(
                "# Mémoire\n\n### Correctif synthétique\n", encoding="utf-8",
            )

    def test_memory_updated_with_modified_added_and_untracked_files(self) -> None:
        self._make_patch(update_memory=True)
        updated, changed = _check_bem_updated(self.repo, self.base_sha, timeout=15)
        self.assertTrue(updated)
        self.assertEqual(set(changed), {
            "surveybot/Survey/BOT_EVOLUTION_MEMORY.md",
            "surveybot/Survey/sample.py",
            "surveybot/Survey/added.py",
            "surveybot/Survey/untracked.py",
        })

    def test_memory_unchanged_and_bem_proposal_uses_path_list(self) -> None:
        self._make_patch(update_memory=False)
        updated, changed = _check_bem_updated(self.repo, self.base_sha, timeout=15)
        self.assertFalse(updated)
        self.assertEqual(set(changed), {
            "surveybot/Survey/sample.py",
            "surveybot/Survey/added.py",
            "surveybot/Survey/untracked.py",
        })
        files, warnings = _detect_changed_files(
            {"worktree_path": str(self.repo), "base_sha": self.base_sha}, git_timeout_s=15,
        )
        self.assertEqual({f.package_relative_path for f in files}, {
            "Survey/sample.py", "Survey/added.py", "Survey/untracked.py",
        })
        self.assertFalse(warnings)
