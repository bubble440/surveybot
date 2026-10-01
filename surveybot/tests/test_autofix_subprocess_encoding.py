from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Survey.autofix.patch_commit import _find_existing_case_commit
from Survey.autofix.static_validator import _run


class AutofixSubprocessEncodingTests(unittest.TestCase):
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
