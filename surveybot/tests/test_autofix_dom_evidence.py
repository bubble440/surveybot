from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Survey.autofix.autofix_worktree import dom_evidence_relative_dir, prepare_autofix_worktree
from Survey.autofix.autofix_orchestrator import (
    ClaudeInvocationResult, EligibleCase, SafetyOutcome, STATUS_FAILURE, process_case,
)
from Survey.autofix.prompt_generator import add_dom_evidence_context


class AutofixDomEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q", cwd=self.repo)
        self._git("config", "user.name", "Test", cwd=self.repo)
        self._git("config", "user.email", "test@example.invalid", cwd=self.repo)
        (self.repo / ".gitignore").write_text("failure_cases/\n", encoding="utf-8")
        (self.repo / "surveybot").mkdir()
        (self.repo / "surveybot" / "tracked.txt").write_text("tracked", encoding="utf-8")
        self._git("add", ".", cwd=self.repo)
        self._git("commit", "-qm", "baseline", cwd=self.repo)
        previous = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, previous)

    def _git(self, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True, check=True,
        )

    def _prepare(self, case_id: str, files: dict[str, bytes]):
        case_dir = self.root / "inputs" / case_id
        diagnosis_dir = self.root / "diagnoses" / case_id
        prompt_dir = self.root / "prompts" / case_id
        for directory in (case_dir, diagnosis_dir, prompt_dir):
            directory.mkdir(parents=True)
        (case_dir / "manifest.json").write_text(
            json.dumps({"case_id": case_id, "stage": "extraction"}), encoding="utf-8",
        )
        (diagnosis_dir / "diagnosis.json").write_text(json.dumps({
            "case_id": case_id, "stage": "extraction", "replay": {"verdict": "REPRODUIT"},
            "confidence_global": "certain", "case_incomplete": False,
        }), encoding="utf-8")
        (prompt_dir / "prompt.txt").write_text(
            f"MÉTADONNÉES TECHNIQUES DU CASE\ncase_id à rattacher au correctif : {case_id}\n"
            "BUG IDENTIFIÉ\nSymptôme synthétique\nRÈGLES STRICTES\n",
            encoding="utf-8",
        )
        originals = {}
        for name, content in files.items():
            path = case_dir / "artifacts" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            originals[name] = path.read_bytes()
        result = prepare_autofix_worktree(
            failure_case_dir=case_dir, diagnosis_dir=diagnosis_dir, prompt_dir=prompt_dir,
            worktrees_root=self.root / "worktrees", out_root=self.root / "outputs",
        )
        return result, originals

    def _emitted_prompt(self, result) -> str:
        relative = dom_evidence_relative_dir(result.case_id)
        return add_dom_evidence_context(
            result.prompt_path.read_text(encoding="utf-8"), result.case_id,
            result.worktree_path / relative, relative,
        )

    def test_copies_only_case_evidence_to_ignored_path_and_mentions_it(self) -> None:
        case_id = "synthetic_evidence_case"
        files = {
            "dom_outer.html": b"<html>case DOM</html>",
            "question_blocks.json": b"[]",
            "validation_report.json": b"{}",
            "frames/frame_0.dom_outer.html": b"<html>frame</html>",
            "viewport.png": b"not a DOM proof",
        }
        result, originals = self._prepare(case_id, files)
        relative = dom_evidence_relative_dir(case_id)
        evidence = result.worktree_path / relative
        for name in ("dom_outer.html", "question_blocks.json", "validation_report.json",
                     "frames/frame_0.dom_outer.html"):
            self.assertEqual((evidence / name).read_bytes(), originals[name])
        self.assertFalse((evidence / "viewport.png").exists())
        self.assertEqual(self._git("status", "--porcelain", "--untracked-files=all",
                                   cwd=result.worktree_path).stdout, "")
        self.assertTrue(self._git("check-ignore", str(evidence / "dom_outer.html"),
                                  cwd=result.worktree_path).stdout.strip())
        prompt = self._emitted_prompt(result)
        self.assertIn(relative.as_posix(), prompt)
        self.assertIn("État des preuves : présentes", prompt)
        self.assertIn("uniquement sur ces fichiers du case", prompt)
        self.assertIn("Ne te fie pas aux autres fichiers de snapshot suivis", prompt)
        self.assertEqual(prompt.split("BUG IDENTIFIÉ\n", 1)[1],
                         result.prompt_path.read_text(encoding="utf-8").split("BUG IDENTIFIÉ\n", 1)[1])
        self.assertNotIn(relative.as_posix(), prompt.split("BUG IDENTIFIÉ\n", 1)[1])
        for name, content in originals.items():
            self.assertEqual((self.root / "inputs" / case_id / "artifacts" / name).read_bytes(), content)

    def test_missing_evidence_keeps_worktree_and_reports_absence(self) -> None:
        result, _ = self._prepare("synthetic_no_evidence", {})
        self.assertFalse((result.worktree_path / dom_evidence_relative_dir(result.case_id)).exists())
        self.assertEqual(self._git("status", "--porcelain", cwd=result.worktree_path).stdout, "")
        prompt = self._emitted_prompt(result)
        self.assertIn(dom_evidence_relative_dir(result.case_id).as_posix(), prompt)
        self.assertIn("État des preuves : absentes ou incomplètes (aucun fichier)", prompt)

    def test_partial_evidence_is_copied_and_reported(self) -> None:
        result, _ = self._prepare("synthetic_partial_evidence", {
            "dom_body.html": b"<body>partial</body>",
        })
        self.assertEqual(
            (result.worktree_path / dom_evidence_relative_dir(result.case_id) / "dom_body.html").read_bytes(),
            b"<body>partial</body>",
        )
        self.assertIn("État des preuves : absentes ou incomplètes (dom_body.html)",
                      self._emitted_prompt(result))
        self.assertEqual(self._git("status", "--porcelain", cwd=result.worktree_path).stdout, "")

    def test_headless_invocation_receives_evidence_path(self) -> None:
        result, _ = self._prepare("synthetic_headless_case", {
            "dom_outer.html": b"<html>proof</html>",
            "question_blocks.json": b"[]",
            "validation_report.json": b"{}",
        })
        captured = {}

        def fake_invoke(**kwargs):
            captured.update(kwargs)
            return ClaudeInvocationResult(
                case_id=result.case_id, branch=result.branch,
                worktree_path=str(result.worktree_path), status=STATUS_FAILURE,
                exit_code=1, timed_out=False, session_id=None, is_error=True,
                subtype=None, command=[], raw_stdout="", raw_stderr="", error="synthetic stop",
            )

        with patch("Survey.autofix.autofix_orchestrator._check_case_parallel_safety",
                   return_value=SafetyOutcome(checked=True, safe=True, reason=None)), \
             patch("Survey.autofix.autofix_orchestrator.invoke_claude_headless",
                   side_effect=fake_invoke):
            summary = process_case(
                EligibleCase(result.case_id, result.manifest_path, result.prompt_path),
                failure_cases_root=self.root / "inputs",
                diagnoses_root=self.root / "diagnoses",
                context_selections_root=self.root / "context",
                worktrees_root=self.root / "outputs",
                codex_runs_root=self.root / "runs",
                static_validations_root=self.root / "static",
                patch_replays_root=self.root / "replays",
                extractor_integrity_checks_root=self.root / "integrity",
                confidence_scores_root=self.root / "confidence",
                human_reviews_root=self.root / "reviews",
                merge_results_root=self.root / "merges",
                merge_reviews_root=self.root / "merge_reviews",
                claude_timeout_s=1, allowed_tools="Read Edit Write Grep Glob",
                permission_mode="acceptEdits", force=False,
            )
        self.assertTrue(summary.is_error)
        self.assertIn(dom_evidence_relative_dir(result.case_id).as_posix(), captured["prompt_text"])
        self.assertEqual(captured["worktree_path"], result.worktree_path)
        self.assertEqual(captured["allowed_tools"], "Read Edit Write Grep Glob")
        self.assertEqual(captured["permission_mode"], "acceptEdits")
