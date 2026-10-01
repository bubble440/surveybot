from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import Survey.autofix.autofix_worktree as worktree_module
from Survey.autofix.autofix_orchestrator import (
    _process_downstream_case, write_downstream_run_result,
)
from Survey.autofix.autofix_worktree import remove_merged_case_worktree


class MergedWorktreeCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.case_id = "synthetic_case"
        self.branch = f"autofix/{self.case_id}"
        self.worktree = self.root / "repo-worktrees" / self.case_id
        self.manifest_root = self.repo / "autofix_worktrees"
        self.manifest = self.manifest_root / self.case_id / "worktree.json"
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("branch", "-M", "integration")
        (self.repo / ".gitignore").write_text("surveybot/failure_cases/\n", encoding="utf-8")
        (self.repo / "core.txt").write_text("base\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.git("worktree", "add", "-qb", self.branch, str(self.worktree), "integration")
        (self.worktree / "core.txt").write_text("fixed\n", encoding="utf-8")
        self.git("add", "core.txt", cwd=self.worktree)
        self.git("commit", "-qm", "fix", cwd=self.worktree)
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text(json.dumps({
            "case_id": self.case_id, "branch": self.branch,
            "worktree_path": str(self.worktree), "source_branch": "integration",
        }), encoding="utf-8")
        self.artifact = self.root / "failure_cases" / self.case_id / "manifest.json"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_text("case evidence", encoding="utf-8")
        self.previous_cwd = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.previous_cwd)

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args], cwd=cwd or self.repo, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=True,
        )
        return result.stdout.strip()

    def merge(self) -> None:
        self.git("merge", "--no-ff", "-qm", "merge synthetic case", self.branch)

    def cleanup(self) -> dict:
        return remove_merged_case_worktree(
            case_id=self.case_id, manifest_path=self.manifest,
            integration_branch="integration",
        )

    def test_removes_clean_merged_worktree_and_branch_but_keeps_artifacts(self) -> None:
        self.merge()
        evidence = self.worktree / "surveybot" / "failure_cases" / self.case_id / "artifacts" / "dom_body.html"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("<html></html>", encoding="utf-8")
        self.assertEqual(self.git("status", "--porcelain", cwd=self.worktree), "")
        self.assertEqual(self.cleanup()["status"], "REMOVED")
        self.assertFalse(self.worktree.exists())
        self.assertEqual(self.git("branch", "--list", self.branch), "")
        self.assertTrue(self.manifest.is_file())
        self.assertEqual(self.artifact.read_text(encoding="utf-8"), "case evidence")

    def test_untracked_file_preserves_worktree(self) -> None:
        self.merge()
        (self.worktree / "operator_note.txt").write_text("keep", encoding="utf-8")
        result = self.cleanup()
        self.assertEqual(result["status"], "PRESERVED")
        self.assertIn("non suivis", result["reason"])
        self.assertTrue(self.worktree.is_dir())
        self.assertTrue(self.git("branch", "--list", self.branch))

    def test_unmerged_branch_preserves_worktree(self) -> None:
        result = self.cleanup()
        self.assertEqual(result["status"], "PRESERVED")
        self.assertIn("non ancêtre", result["reason"])
        self.assertTrue(self.worktree.is_dir())

    def test_path_outside_configured_root_is_rejected(self) -> None:
        self.merge()
        outside = self.root / "other" / self.case_id
        outside.parent.mkdir()
        self.git("worktree", "move", str(self.worktree), str(outside))
        self.manifest.write_text(json.dumps({
            "case_id": self.case_id, "branch": self.branch, "worktree_path": str(outside),
        }), encoding="utf-8")
        result = self.cleanup()
        self.assertEqual(result["status"], "PRESERVED")
        self.assertIn("hors de la racine", result["reason"])
        self.assertTrue(outside.is_dir())

    def test_protected_branch_is_rejected(self) -> None:
        self.merge()
        self.manifest.write_text(json.dumps({
            "case_id": self.case_id, "branch": "main", "worktree_path": str(self.worktree),
        }), encoding="utf-8")
        result = self.cleanup()
        self.assertEqual(result["status"], "PRESERVED")
        self.assertIn("protégée", result["reason"])
        self.assertTrue(self.worktree.is_dir())

    def test_unregistered_directory_is_preserved(self) -> None:
        self.merge()
        self.git("worktree", "remove", str(self.worktree))
        self.worktree.mkdir(parents=True)
        result = self.cleanup()
        self.assertEqual(result["status"], "PRESERVED")
        self.assertIn("non enregistré", result["reason"])
        self.assertTrue(self.worktree.is_dir())

    def test_absent_worktree_is_idempotent(self) -> None:
        self.merge()
        self.assertEqual(self.cleanup()["status"], "REMOVED")
        self.assertEqual(self.cleanup()["status"], "ALREADY_ABSENT")

    def test_branch_deletion_failure_restores_worktree(self) -> None:
        self.merge()
        real_run = worktree_module._run_git

        def fail_branch_delete(args, *, cwd, timeout=30.0):
            if args[:2] == ["branch", "-d"]:
                return subprocess.CompletedProcess(["git", *args], 1, "", "simulated refusal")
            return real_run(args, cwd=cwd, timeout=timeout)

        with patch.object(worktree_module, "_run_git", side_effect=fail_branch_delete):
            result = self.cleanup()
        self.assertEqual(result["status"], "PRESERVED")
        self.assertIn("simulated refusal", result["reason"])
        self.assertTrue(self.worktree.is_dir())
        self.assertTrue(self.git("branch", "--list", self.branch))

    def test_downstream_merged_result_removes_worktree(self) -> None:
        self.merge()
        commit_root = self.root / "commit_results"
        merge_root = self.root / "merge_results"
        run_root = self.root / "pipeline_runs"
        commit_file = commit_root / self.case_id / "commit_result.json"
        merge_file = merge_root / self.case_id / "merge_result.json"
        commit_file.parent.mkdir(parents=True)
        merge_file.parent.mkdir(parents=True)
        commit_file.write_text("{}", encoding="utf-8")
        merge_file.write_text(json.dumps({
            "status": "MERGED", "target_branch": "integration",
        }), encoding="utf-8")
        with patch("Survey.autofix.autofix_orchestrator._maybe_notify", return_value=(True, [])):
            summary = _process_downstream_case(
                self.case_id, confidence_scores_root=self.root / "scores",
                human_reviews_root=self.root / "reviews", worktrees_root=self.manifest_root,
                diagnoses_root=self.root / "diagnoses", context_selections_root=self.root / "selections",
                commit_results_root=commit_root, merge_results_root=merge_root,
                pipeline_runs_root=run_root,
            )
        self.assertTrue(summary.terminal)
        self.assertFalse(summary.is_error)
        self.assertTrue(summary.notified)
        self.assertEqual(summary.worktree_cleanup["status"], "REMOVED")
        self.assertFalse(self.worktree.exists())
        self.assertTrue(self.manifest.is_file())
        self.assertTrue(self.artifact.is_file())

    def test_downstream_conflict_does_not_attempt_cleanup(self) -> None:
        commit_root = self.root / "commit_results"
        merge_root = self.root / "merge_results"
        commit_file = commit_root / self.case_id / "commit_result.json"
        merge_file = merge_root / self.case_id / "merge_result.json"
        commit_file.parent.mkdir(parents=True)
        merge_file.parent.mkdir(parents=True)
        commit_file.write_text("{}", encoding="utf-8")
        merge_file.write_text(json.dumps({"status": "CONFLICT"}), encoding="utf-8")
        with patch("Survey.autofix.autofix_orchestrator._maybe_notify", return_value=(True, [])), patch(
            "Survey.autofix.autofix_orchestrator.remove_merged_case_worktree",
        ) as cleanup:
            summary = _process_downstream_case(
                self.case_id, confidence_scores_root=self.root / "scores",
                human_reviews_root=self.root / "reviews", worktrees_root=self.manifest_root,
                diagnoses_root=self.root / "diagnoses", context_selections_root=self.root / "selections",
                commit_results_root=commit_root, merge_results_root=merge_root,
                pipeline_runs_root=self.root / "pipeline_runs",
            )
        cleanup.assert_not_called()
        self.assertIsNone(summary.worktree_cleanup)
        self.assertTrue(self.worktree.is_dir())

    def test_git_failure_preserves_terminal_merge_and_notification(self) -> None:
        self.merge()
        commit_root = self.root / "commit_results"
        merge_root = self.root / "merge_results"
        run_root = self.root / "pipeline_runs"
        commit_file = commit_root / self.case_id / "commit_result.json"
        merge_file = merge_root / self.case_id / "merge_result.json"
        commit_file.parent.mkdir(parents=True)
        merge_file.parent.mkdir(parents=True)
        commit_file.write_text("{}", encoding="utf-8")
        merge_file.write_text(json.dumps({
            "status": "MERGED", "target_branch": "integration",
        }), encoding="utf-8")
        real_run = worktree_module._run_git

        def fail_remove(args, *, cwd, timeout=30.0):
            if args[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(["git", *args], 1, "", "simulated lock")
            return real_run(args, cwd=cwd, timeout=timeout)

        with patch.object(worktree_module, "_run_git", side_effect=fail_remove), patch(
            "Survey.autofix.autofix_orchestrator._maybe_notify", return_value=(True, []),
        ):
            summary = _process_downstream_case(
                self.case_id, confidence_scores_root=self.root / "scores",
                human_reviews_root=self.root / "reviews", worktrees_root=self.manifest_root,
                diagnoses_root=self.root / "diagnoses", context_selections_root=self.root / "selections",
                commit_results_root=commit_root, merge_results_root=merge_root,
                pipeline_runs_root=run_root,
            )
        write_downstream_run_result(summary, out_root=run_root)
        saved = json.loads((run_root / self.case_id / "downstream_run.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["terminal"])
        self.assertFalse(saved["is_error"])
        self.assertTrue(saved["notified"])
        self.assertEqual(saved["worktree_cleanup"]["status"], "PRESERVED")
        self.assertIn("simulated lock", saved["worktree_cleanup"]["reason"])
        self.assertTrue(self.worktree.is_dir())
        self.assertEqual(self.artifact.read_text(encoding="utf-8"), "case evidence")
