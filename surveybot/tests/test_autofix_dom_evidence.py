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
    ClaudeInvocationResult, EligibleCase, SafetyOutcome, STATUS_FAILURE, STATUS_SUCCESS,
    STAGE_CONFIDENCE_SCORE, STAGE_NO_CHANGES, _MAX_AGENT_FINAL_TEXT_CHARS,
    _worktree_terminal_state, invoke_claude_headless, process_case, write_pipeline_run_summary,
)
from Survey.autofix.human_review import HumanReviewError
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

    def _prepare(self, case_id: str, files: dict[str, bytes], *, stage: str = "extraction"):
        case_dir = self.root / "inputs" / case_id
        diagnosis_dir = self.root / "diagnoses" / case_id
        prompt_dir = self.root / "prompts" / case_id
        for directory in (case_dir, diagnosis_dir, prompt_dir):
            directory.mkdir(parents=True)
        (case_dir / "manifest.json").write_text(
            json.dumps({"case_id": case_id, "stage": stage}), encoding="utf-8",
        )
        diagnosis = {
            "case_id": case_id, "stage": stage, "replay": {"verdict": "REPRODUIT"},
            "confidence_global": "certain", "case_incomplete": False,
        }
        if stage == "action":
            diagnosis["real_dispatch_replay"] = {"validation_comparison": {"outcome": "BUG_PERSISTANT"}}
        (diagnosis_dir / "diagnosis.json").write_text(json.dumps(diagnosis), encoding="utf-8")
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

    def _process_prepared_case(self, result, *, force: bool = False):
        return process_case(
            EligibleCase(result.case_id, result.manifest_path, result.prompt_path),
            failure_cases_root=self.root / "inputs", diagnoses_root=self.root / "diagnoses",
            context_selections_root=self.root / "context", worktrees_root=self.root / "outputs",
            codex_runs_root=self.root / "runs", static_validations_root=self.root / "static",
            patch_replays_root=self.root / "replays",
            extractor_integrity_checks_root=self.root / "integrity",
            confidence_scores_root=self.root / "confidence", human_reviews_root=self.root / "reviews",
            merge_results_root=self.root / "merges", merge_reviews_root=self.root / "merge_reviews",
            pipeline_runs_root=self.root / "pipeline",
            core_change_candidates_root=self.root / "core_change_candidates",
            claude_timeout_s=1, allowed_tools="Read Edit Write Grep Glob",
            permission_mode="acceptEdits", force=force,
        )

    def _write_synthetic_static(self, case_id: str, changed_files: list[str]) -> Path:
        path = self.root / "static" / case_id / "validation_static.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "case_id": case_id, "verdict": "ACCEPTED", "changed_files": changed_files,
        }), encoding="utf-8")
        return path

    def _terminal_state(self, case_id: str) -> tuple[bool, str]:
        return _worktree_terminal_state(
            case_id, merge_results_root=self.root / "merges",
            human_reviews_root=self.root / "reviews", merge_reviews_root=self.root / "merge_reviews",
            confidence_scores_root=self.root / "confidence", static_validations_root=self.root / "static",
            codex_runs_root=self.root / "runs", pipeline_runs_root=self.root / "pipeline",
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

    def test_action_copies_and_mentions_both_optional_evidence_files(self) -> None:
        case_id = "synthetic_action_evidence_both"
        files = {
            "pre_action_dom.html": b"<html>before</html>",
            "post_action_dom.html": b"<html>after</html>",
            "question_blocks.json": b"[]",
            "validation_report.json": b"{}",
            "runtime_state.json": b'{"facts":[{"visible":true,"checked":false}]}',
            "action_trace.json": b'{"steps":[{"result":"failed"}]}',
            "actions_requested.json": b'{"value":"raw answer"}',
        }
        result, originals = self._prepare(case_id, files, stage="action")
        evidence = result.worktree_path / dom_evidence_relative_dir(case_id)
        prompt = self._emitted_prompt(result)
        for name in ("runtime_state.json", "action_trace.json"):
            self.assertEqual((evidence / name).read_bytes(), originals[name])
            self.assertIn(name, prompt)
        self.assertFalse((evidence / "actions_requested.json").exists())
        self.assertNotIn("actions_requested.json", prompt)

    def test_action_mentions_only_the_optional_evidence_that_exists(self) -> None:
        for name in ("runtime_state.json", "action_trace.json"):
            with self.subTest(name=name):
                case_id = "synthetic_action_only_" + name.removesuffix(".json")
                result, originals = self._prepare(case_id, {
                    "pre_action_dom.html": b"<html>before</html>",
                    "post_action_dom.html": b"<html>after</html>",
                    "question_blocks.json": b"[]",
                    "validation_report.json": b"{}",
                    name: b'{"synthetic":true}',
                }, stage="action")
                evidence = result.worktree_path / dom_evidence_relative_dir(case_id)
                prompt = self._emitted_prompt(result)
                other = "action_trace.json" if name == "runtime_state.json" else "runtime_state.json"
                self.assertEqual((evidence / name).read_bytes(), originals[name])
                self.assertIn(name, prompt)
                self.assertFalse((evidence / other).exists())
                self.assertNotIn(other, prompt)

    def test_action_without_optional_evidence_keeps_existing_list(self) -> None:
        case_id = "synthetic_action_evidence_none"
        result, _ = self._prepare(case_id, {
            "pre_action_dom.html": b"<html>before</html>",
            "post_action_dom.html": b"<html>after</html>",
            "question_blocks.json": b"[]",
            "validation_report.json": b"{}",
            "actions_requested.json": b'{"value":"raw answer"}',
        }, stage="action")
        evidence = result.worktree_path / dom_evidence_relative_dir(case_id)
        prompt = self._emitted_prompt(result)
        self.assertFalse((evidence / "runtime_state.json").exists())
        self.assertFalse((evidence / "action_trace.json").exists())
        self.assertFalse((evidence / "actions_requested.json").exists())
        self.assertIn(
            "État des preuves : présentes (post_action_dom.html, pre_action_dom.html, "
            "question_blocks.json, validation_report.json).", prompt,
        )

    def test_extraction_ignores_action_only_evidence_and_keeps_prompt(self) -> None:
        case_id = "synthetic_extraction_ignores_action_evidence"
        result, _ = self._prepare(case_id, {
            "dom_outer.html": b"<html>extraction</html>",
            "question_blocks.json": b"[]",
            "validation_report.json": b"{}",
            "runtime_state.json": b'{"facts":[]}',
            "action_trace.json": b'{"steps":[]}',
            "actions_requested.json": b'{"value":"raw answer"}',
        })
        evidence = result.worktree_path / dom_evidence_relative_dir(case_id)
        prompt = self._emitted_prompt(result)
        self.assertEqual(sorted(path.name for path in evidence.iterdir()), [
            "dom_outer.html", "question_blocks.json", "validation_report.json",
        ])
        self.assertIn(
            "État des preuves : présentes (dom_outer.html, question_blocks.json, validation_report.json).",
            prompt,
        )
        for name in ("runtime_state.json", "action_trace.json", "actions_requested.json"):
            self.assertNotIn(name, prompt)

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

    def test_agent_final_text_is_bounded_and_explicit_candidate_is_detected(self) -> None:
        text = "a" * (_MAX_AGENT_FINAL_TEXT_CHARS + 1) + " CORE_CHANGE_CANDIDATE"
        output = json.dumps({"is_error": False, "result": text})
        with patch("Survey.autofix.autofix_orchestrator.shutil.which", return_value="claude"), \
             patch("Survey.autofix.autofix_orchestrator.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, output, "")):
            invocation = invoke_claude_headless(
                case_id="synthetic_case", branch="synthetic", worktree_path=self.repo,
                prompt_text="synthetic prompt",
            )
        self.assertEqual(invocation.status, STATUS_SUCCESS)
        self.assertEqual(len(invocation.as_dict()["agent_final_text"]), _MAX_AGENT_FINAL_TEXT_CHARS)
        self.assertTrue(invocation.declares_core_change_candidate)

    def test_empty_diff_stops_notifies_once_and_is_terminal(self) -> None:
        result, _ = self._prepare("synthetic_no_changes", {})
        final_text = "CORE_CHANGE_CANDIDATE : changement du core à examiner."
        invocation = ClaudeInvocationResult(
            case_id=result.case_id, branch=result.branch, worktree_path=str(result.worktree_path),
            status=STATUS_SUCCESS, exit_code=0, timed_out=False, session_id=None,
            is_error=False, subtype="success", command=[], raw_stdout="{}", raw_stderr="",
            error=None, agent_final_text=final_text, declares_core_change_candidate=True,
        )
        with patch("Survey.autofix.autofix_orchestrator._check_case_parallel_safety",
                   return_value=SafetyOutcome(checked=True, safe=True, reason=None)), \
             patch("Survey.autofix.autofix_orchestrator.invoke_claude_headless", return_value=invocation), \
             patch("Survey.autofix.autofix_orchestrator.write_static_validation",
                   side_effect=lambda *_args, **_kwargs: self._write_synthetic_static(result.case_id, [])), \
             patch("Survey.autofix.autofix_orchestrator.write_patch_replay") as replay, \
             patch("Survey.autofix.autofix_orchestrator.write_extractor_integrity_check") as integrity, \
             patch("Survey.autofix.autofix_orchestrator.write_patch_confidence") as confidence, \
             patch("Survey.autofix.autofix_orchestrator.send_status_notification") as notify:
            first = self._process_prepared_case(result)
            self.assertFalse(self._terminal_state(result.case_id)[0])
            write_pipeline_run_summary(first, out_root=self.root / "pipeline")
            second = self._process_prepared_case(result, force=True)
        self.assertEqual(first.stopped_at, STAGE_NO_CHANGES)
        self.assertTrue(first.stop_reason.startswith("CORE_CHANGE_CANDIDATE"))
        self.assertFalse(first.is_error)
        self.assertTrue(first.notified)
        self.assertTrue(second.notified)
        self.assertTrue(self._terminal_state(result.case_id)[0])
        notify.assert_called_once()
        replay.assert_not_called()
        integrity.assert_not_called()
        confidence.assert_not_called()
        run_data = json.loads((self.root / "runs" / result.case_id / "run_result.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["agent_final_text"], final_text)
        candidate = json.loads(Path(first.artifacts["core_change_candidate"]).read_text(encoding="utf-8"))
        self.assertEqual(candidate["case_id"], result.case_id)
        self.assertEqual(candidate["signal"], "CORE_CHANGE_CANDIDATE")
        self.assertEqual(candidate["reason"], final_text)
        self.assertEqual(candidate["agent_final_text"], final_text)

    def test_empty_diff_notification_failure_is_only_a_warning(self) -> None:
        result, _ = self._prepare("synthetic_notify_failure", {})
        invocation = ClaudeInvocationResult(
            case_id=result.case_id, branch=result.branch, worktree_path=str(result.worktree_path),
            status=STATUS_SUCCESS, exit_code=0, timed_out=False, session_id=None,
            is_error=False, subtype="success", command=[], raw_stdout="{}", raw_stderr="",
            error=None, agent_final_text="aucun correctif réalisable",
        )
        with patch("Survey.autofix.autofix_orchestrator._check_case_parallel_safety",
                   return_value=SafetyOutcome(checked=True, safe=True, reason=None)), \
             patch("Survey.autofix.autofix_orchestrator.invoke_claude_headless", return_value=invocation), \
             patch("Survey.autofix.autofix_orchestrator.write_static_validation",
                   side_effect=lambda *_args, **_kwargs: self._write_synthetic_static(result.case_id, [])), \
             patch("Survey.autofix.autofix_orchestrator.send_status_notification",
                   side_effect=HumanReviewError("notification indisponible")) as notify:
            summary = self._process_prepared_case(result)
            write_pipeline_run_summary(summary, out_root=self.root / "pipeline")
            repeated = self._process_prepared_case(result, force=True)
        self.assertEqual(summary.stopped_at, STAGE_NO_CHANGES)
        self.assertFalse(summary.is_error)
        self.assertFalse(summary.notified)
        self.assertEqual(len(summary.warnings), 1)
        self.assertFalse(repeated.notified)
        notify.assert_called_once()

    def test_nonempty_diff_continues_to_existing_confidence_stage(self) -> None:
        result, _ = self._prepare("synthetic_changed_case", {})
        invocation = ClaudeInvocationResult(
            case_id=result.case_id, branch=result.branch, worktree_path=str(result.worktree_path),
            status=STATUS_SUCCESS, exit_code=0, timed_out=False, session_id=None,
            is_error=False, subtype="success", command=[], raw_stdout="{}", raw_stderr="",
            error=None, agent_final_text="correctif ajouté",
        )
        confidence_path = self.root / "confidence" / result.case_id / "confidence_score.json"
        confidence_path.parent.mkdir(parents=True, exist_ok=True)
        confidence_path.write_text(json.dumps({"confidence": "REJECT"}), encoding="utf-8")
        with patch("Survey.autofix.autofix_orchestrator._check_case_parallel_safety",
                   return_value=SafetyOutcome(checked=True, safe=True, reason=None)), \
             patch("Survey.autofix.autofix_orchestrator.invoke_claude_headless", return_value=invocation), \
             patch("Survey.autofix.autofix_orchestrator.write_static_validation",
                   side_effect=lambda *_args, **_kwargs: self._write_synthetic_static(result.case_id, ["Survey/fix.py"])), \
             patch("Survey.autofix.autofix_orchestrator.write_patch_replay",
                   return_value=self.root / "replays" / "synthetic.json") as replay, \
             patch("Survey.autofix.autofix_orchestrator.write_extractor_integrity_check",
                   return_value=self.root / "integrity" / "synthetic.json") as integrity, \
             patch("Survey.autofix.autofix_orchestrator.write_patch_confidence",
                   return_value=confidence_path) as confidence, \
             patch("Survey.autofix.autofix_orchestrator.send_status_notification") as notify:
            summary = self._process_prepared_case(result)
        self.assertEqual(summary.stopped_at, STAGE_CONFIDENCE_SCORE)
        self.assertFalse(summary.notified)
        replay.assert_called_once()
        integrity.assert_called_once()
        confidence.assert_called_once()
        notify.assert_not_called()

    def test_existing_terminal_decisions_remain_unchanged(self) -> None:
        case_id = "synthetic_terminal_states"
        run_path = self.root / "runs" / case_id / "run_result.json"
        run_path.parent.mkdir(parents=True)
        run_path.write_text(json.dumps({"status": "FAILURE"}), encoding="utf-8")
        self.assertTrue(self._terminal_state(case_id)[0])

        run_path.write_text(json.dumps({"status": "SUCCESS"}), encoding="utf-8")
        static_path = self.root / "static" / case_id / "validation_static.json"
        static_path.parent.mkdir(parents=True)
        static_path.write_text(json.dumps({"verdict": "REJECTED"}), encoding="utf-8")
        self.assertTrue(self._terminal_state(case_id)[0])

        self._write_synthetic_static(case_id, ["Survey/fix.py"])
        self.assertFalse(self._terminal_state(case_id)[0])
