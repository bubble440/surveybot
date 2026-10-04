from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Survey.autofix.autofix_worktree import dom_evidence_relative_dir, prepare_autofix_worktree
from Survey.autofix.autofix_orchestrator import (
    AutofixOrchestratorError, ClaudeInvocationResult, DEFAULT_MAX_BUDGET_USD,
    DEFAULT_MAX_ATTEMPTS, DEFAULT_MAX_CASE_BUDGET_USD,
    EligibleCase, SafetyOutcome, STATUS_FAILURE, STATUS_SUCCESS,
    STAGE_CLAUDE_INVOCATION, STAGE_CONFIDENCE_SCORE, STAGE_NO_CHANGES,
    STAGE_STATIC_VALIDATION, _MAX_AGENT_FINAL_TEXT_CHARS,
    STAGE_UPSTREAM_DIAGNOSIS, STAGE_UPSTREAM_HISTORY_DUPLICATE, UpstreamCaseSummary,
    _diagnosis_signature, _worktree_terminal_state,
    _extract_agent_final_summary, _informative_static_rejection,
    invoke_claude_headless, process_case,
    run_autofix_pipeline, run_upstream_stage,
    write_pipeline_run_summary, write_run_result,
)
from Survey.autofix.failure_diagnosis import DiagnosisError, get_code_fingerprint, write_diagnosis
from Survey.autofix.human_review import HumanReviewError
from Survey.autofix.prompt_generator import add_dom_evidence_context


class AutofixHeadlessInvocationTests(unittest.TestCase):
    def test_command_options_and_subprocess_contract(self) -> None:
        scenarios = (
            ({}, ["--restricted", "--max-budget-usd", "5.0"]),
            ({"restricted": False}, ["--max-budget-usd", "5.0"]),
            ({"max_budget_usd": 2.5}, ["--restricted", "--max-budget-usd", "2.5"]),
            ({"max_budget_usd": 0}, ["--restricted"]),
        )
        base = ["claude", "-p", "--output-format", "json",
                "--allowedTools", "Read Edit Write Grep Glob",
                "--permission-mode", "acceptEdits", "--permission-prompts", "none"]
        for options, suffix in scenarios:
            with self.subTest(options=options), \
                 patch("Survey.autofix.autofix_orchestrator.shutil.which", return_value="claude"), \
                 patch("Survey.autofix.autofix_orchestrator.subprocess.run",
                       return_value=subprocess.CompletedProcess([], 0,
                           '{"is_error": false, "subtype": "success"}', "")) as run:
                result = invoke_claude_headless(
                    case_id="synthetic", branch="synthetic", worktree_path=Path("worktree"),
                    prompt_text="synthetic prompt", **options,
                )
                self.assertEqual(result.command, base + suffix)
                self.assertEqual(result.status, STATUS_SUCCESS)
                run.assert_called_once_with(
                    base + suffix, cwd="worktree", input="synthetic prompt",
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=600.0, check=False,
                )

    def test_cost_and_turns_are_optional_validated_artifact_fields(self) -> None:
        samples = (
            ({"total_cost_usd": 1.25, "num_turns": 6}, (1.25, 6)),
            ({}, (None, None)),
            ({"total_cost_usd": "1.25", "num_turns": True}, (None, None)),
            ({"total_cost_usd": -1, "num_turns": -2}, (None, None)),
            ({"total_cost_usd": float("inf"), "num_turns": 2.5}, (None, None)),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (fields, expected) in enumerate(samples):
                with self.subTest(fields=fields), \
                     patch("Survey.autofix.autofix_orchestrator.shutil.which", return_value="claude"), \
                     patch("Survey.autofix.autofix_orchestrator.subprocess.run",
                           return_value=subprocess.CompletedProcess([], 0, json.dumps({
                               "is_error": False, "subtype": "success", **fields,
                           }), "")):
                    result = invoke_claude_headless(
                        case_id=f"synthetic_{index}", branch="synthetic",
                        worktree_path=Path("worktree"), prompt_text="synthetic prompt",
                    )
                path = write_run_result(result, out_root=Path(directory))
                artifact = json.loads(path.read_text(encoding="utf-8"))
                for key, value in zip(("total_cost_usd", "num_turns"), expected):
                    if value is None:
                        self.assertNotIn(key, artifact)
                    else:
                        self.assertEqual(artifact[key], value)
                self.assertEqual(artifact["schema_version"], "1.0")

    def test_cli_reported_failure_keeps_subtype_and_stops(self) -> None:
        for returncode, payload in (
            (0, {"is_error": False, "subtype": "error_max_budget_usd",
                 "total_cost_usd": 5.1, "num_turns": 12}),
            (1, {"is_error": True, "subtype": "error_max_budget_usd",
                 "total_cost_usd": 5.1, "num_turns": 12}),
        ):
            with self.subTest(returncode=returncode), \
                 patch("Survey.autofix.autofix_orchestrator.shutil.which", return_value="claude"), \
                 patch("Survey.autofix.autofix_orchestrator.subprocess.run",
                       return_value=subprocess.CompletedProcess([], returncode, json.dumps(payload), "")):
                result = invoke_claude_headless(
                    case_id="synthetic", branch="synthetic", worktree_path=Path("worktree"),
                    prompt_text="synthetic prompt",
                )
            self.assertEqual(result.status, STATUS_FAILURE)
            self.assertIn("error_max_budget_usd", result.error)
            self.assertEqual(result.as_dict()["total_cost_usd"], 5.1)
            self.assertEqual(result.as_dict()["num_turns"], 12)

    def test_budget_validation_and_cli_options(self) -> None:
        from tools.run_autofix_pipeline import main

        with patch("tools.run_autofix_pipeline.run_autofix_pipeline", return_value=([], [], [])) as run:
            self.assertEqual(main([]), 0)
            self.assertTrue(run.call_args.kwargs["restricted"])
            self.assertEqual(run.call_args.kwargs["max_budget_usd"], DEFAULT_MAX_BUDGET_USD)
            self.assertEqual(run.call_args.kwargs["max_attempts"], DEFAULT_MAX_ATTEMPTS)
            self.assertEqual(run.call_args.kwargs["max_case_budget_usd"], DEFAULT_MAX_CASE_BUDGET_USD)
            self.assertEqual(main(["--no-restricted", "--max-budget-usd", "0"]), 0)
            self.assertFalse(run.call_args.kwargs["restricted"])
            self.assertEqual(run.call_args.kwargs["max_budget_usd"], 0)
            self.assertEqual(main(["--max-budget-usd", "2.5"]), 0)
            self.assertEqual(run.call_args.kwargs["max_budget_usd"], 2.5)
            self.assertEqual(main(["--max-attempts", "3", "--max-case-budget-usd", "0"]), 0)
            self.assertEqual(run.call_args.kwargs["max_attempts"], 3)
            self.assertEqual(run.call_args.kwargs["max_case_budget_usd"], 0)

        for invalid in ("-1", "nan", "inf"):
            with self.subTest(invalid=invalid), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main(["--max-budget-usd", invalid]), 1)
                self.assertIn("--max-budget-usd doit être un nombre fini >= 0", stderr.getvalue())
        with redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as raised:
            main(["--max-budget-usd", "invalid"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--max-budget-usd", stderr.getvalue())
        self.assertIn("invalid float value", stderr.getvalue())
        for invalid in (-1, "invalid", float("nan")):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                AutofixOrchestratorError, "--max-budget-usd doit être un nombre fini >= 0"
            ):
                invoke_claude_headless(
                    case_id="synthetic", branch="synthetic", worktree_path=Path("worktree"),
                    prompt_text="synthetic prompt", max_budget_usd=invalid,
                )
        for args, message in (
            (["--max-attempts", "0"], "--max-attempts doit être"),
            (["--max-attempts", "4"], "--max-attempts doit être"),
            (["--max-case-budget-usd", "-1"], "--max-case-budget-usd doit être"),
            (["--max-case-budget-usd", "nan"], "--max-case-budget-usd doit être"),
        ):
            with self.subTest(args=args), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main(["--no-upstream", "--no-downstream", *args]), 1)
                self.assertIn(message, stderr.getvalue())

    def test_pipeline_forwards_invocation_options(self) -> None:
        case = EligibleCase("synthetic", Path("manifest.json"), Path("prompt.txt"))
        summary = SimpleNamespace(case_id="synthetic")
        with patch("Survey.autofix.autofix_orchestrator.acquire_lock", return_value=object()), \
             patch("Survey.autofix.autofix_orchestrator.release_lock"), \
             patch("Survey.autofix.autofix_orchestrator.discover_eligible_cases", return_value=[case]), \
             patch("Survey.autofix.autofix_orchestrator.process_case", return_value=summary) as process, \
             patch("Survey.autofix.autofix_orchestrator.write_pipeline_run_summary"):
            _, _, results = run_autofix_pipeline(
                run_upstream=False, run_downstream=False, restricted=False, max_budget_usd=2.5,
            )
        self.assertEqual(results, [summary])
        self.assertFalse(process.call_args.kwargs["restricted"])
        self.assertEqual(process.call_args.kwargs["max_budget_usd"], 2.5)


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

    def test_diagnosis_fingerprint_uses_phase_seven_head_and_detects_dirty_tree(self) -> None:
        base_sha = self._git("rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        self.assertEqual(get_code_fingerprint(), {"base_sha": base_sha, "dirty": False})
        (self.repo / "surveybot" / "tracked.txt").write_text("modified", encoding="utf-8")
        self.assertEqual(get_code_fingerprint(), {"base_sha": base_sha, "dirty": True})

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

    def _process_prepared_case(self, result, *, force: bool = False, **options):
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
            permission_mode="acceptEdits", force=force, **options,
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
        self.assertTrue(captured["restricted"])
        self.assertEqual(captured["max_budget_usd"], DEFAULT_MAX_BUDGET_USD)

    def test_budget_failure_stops_before_validation(self) -> None:
        prepared, _ = self._prepare("synthetic_budget_failure", {})
        output = json.dumps({"is_error": True, "subtype": "error_max_budget_usd"})
        with patch("Survey.autofix.autofix_orchestrator._check_case_parallel_safety",
                   return_value=SafetyOutcome(checked=True, safe=True, reason=None)), \
             patch("Survey.autofix.autofix_orchestrator.shutil.which", return_value="claude"), \
             patch("Survey.autofix.autofix_orchestrator.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 1, output, "")), \
             patch("Survey.autofix.autofix_orchestrator.write_static_validation") as validate:
            summary = self._process_prepared_case(prepared)
        self.assertEqual(summary.stopped_at, STAGE_CLAUDE_INVOCATION)
        self.assertTrue(summary.is_error)
        self.assertIn("error_max_budget_usd", summary.stop_reason)
        validate.assert_not_called()

    def test_agent_final_text_is_bounded_and_explicit_candidate_is_detected(self) -> None:
        text = "a" * (_MAX_AGENT_FINAL_TEXT_CHARS + 1) + " CORE_CHANGE_CANDIDATE\nRÉSUMÉ : candidat à examiner"
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
        self.assertEqual(invocation.as_dict()["agent_final_summary"], "candidat à examiner")
        self.assertTrue(invocation.declares_core_change_candidate)

    def test_agent_final_summary_uses_last_matching_tail_line_and_sanitizes(self) -> None:
        for line in ("RÉSUMÉ : bref", "resume:bref", "  r É s u m e  :  bref  "):
            with self.subTest(line=line):
                self.assertEqual(_extract_agent_final_summary(line), "bref")
        self.assertEqual(
            _extract_agent_final_summary("RÉSUMÉ : ancien\ntexte\nReSuMe :  conclusion \x00 finale\t "),
            "conclusion finale",
        )
        self.assertEqual(_extract_agent_final_summary("RÉSUMÉ : " + "x" * 205), "x" * 200)
        self.assertIsNone(_extract_agent_final_summary("aucune ligne conforme"))
        self.assertIsNone(_extract_agent_final_summary("RÉSUMÉ : ancien\n" + "bruit\n" * 10))

    def _synthetic_attempts(
        self, case_id: str, *, static_artifacts: list[dict],
        replay_artifacts: "list[dict] | None" = None,
        integrity_artifacts: "list[dict] | None" = None,
        confidence_artifacts: "list[dict] | None" = None,
        costs: "list[float | None] | None" = None,
        patch_contents: "list[str] | None" = None,
        statuses: "list[str] | None" = None,
        candidates: "list[bool] | None" = None,
        **options,
    ):
        prepared, _ = self._prepare(case_id, {})
        fix_path = prepared.worktree_path / "surveybot" / "fix.py"
        costs = costs or [1.0] * len(static_artifacts)
        patch_contents = patch_contents or [f"patch {i}" for i in range(len(costs))]
        statuses = statuses or [STATUS_SUCCESS] * len(costs)
        candidates = candidates or [False] * len(costs)
        calls: "list[dict]" = []
        counts = {"static": 0, "replay": 0, "integrity": 0, "confidence": 0}

        def invoke(**kwargs):
            index = len(calls)
            calls.append(kwargs)
            fix_path.write_text(patch_contents[index], encoding="utf-8")
            return ClaudeInvocationResult(
                case_id=case_id, branch=prepared.branch,
                worktree_path=str(prepared.worktree_path), status=statuses[index],
                exit_code=0 if statuses[index] == STATUS_SUCCESS else 1,
                timed_out=False, session_id=f"session_{index}", is_error=statuses[index] != STATUS_SUCCESS,
                subtype="success" if statuses[index] == STATUS_SUCCESS else "error",
                command=[], raw_stdout="{}", raw_stderr="", error="synthetic failure" if statuses[index] != STATUS_SUCCESS else None,
                agent_final_text="RÉSUMÉ : libellé secret https://example.invalid/?value=secret",
                agent_final_summary="libellé secret https://example.invalid/?value=secret",
                declares_core_change_candidate=candidates[index],
                total_cost_usd=costs[index], num_turns=index + 2,
            )

        def write_artifact(kind: str, filename: str, records: "list[dict] | None"):
            index = counts[kind]
            counts[kind] += 1
            if records is None or index >= len(records):
                raise AssertionError(f"unexpected {kind} call")
            path = self.root / kind / case_id / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            artifact = dict(records[index])
            if kind == "static":
                artifact["changed_files"] = [str(fix_path)] if artifact.get("changed_files") != [] else []
            path.write_text(json.dumps(artifact), encoding="utf-8")
            return path

        def request_review(**_kwargs):
            path = self.root / "reviews" / case_id / "pending.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return SimpleNamespace(pending_path=path)

        with (
            patch("Survey.autofix.autofix_orchestrator._check_case_parallel_safety",
                  return_value=SafetyOutcome(checked=True, safe=True, reason=None)) as safety,
            patch("Survey.autofix.autofix_orchestrator.invoke_claude_headless", side_effect=invoke),
            patch("Survey.autofix.autofix_orchestrator.write_static_validation",
                  side_effect=lambda *_a, **_k: write_artifact("static", "validation_static.json", static_artifacts)),
            patch("Survey.autofix.autofix_orchestrator.write_patch_replay",
                  side_effect=lambda **_k: write_artifact("replay", "patch_replay.json", replay_artifacts)),
            patch("Survey.autofix.autofix_orchestrator.write_extractor_integrity_check",
                  side_effect=lambda *_a, **_k: write_artifact("integrity", "extractor_integrity_check.json", integrity_artifacts)),
            patch("Survey.autofix.autofix_orchestrator.write_patch_confidence",
                  side_effect=lambda **_k: write_artifact("confidence", "confidence_score.json", confidence_artifacts)),
            patch("Survey.autofix.autofix_orchestrator.send_review_request",
                  side_effect=request_review) as review,
            patch("Survey.autofix.autofix_orchestrator.send_status_notification") as notify,
        ):
            summary = self._process_prepared_case(prepared, **options)
            summary._test_notifications = [call.args[0] for call in notify.call_args_list]
        return summary, calls, counts, safety.call_count, review.call_count, notify.call_count

    def test_informative_static_rejection_retries_and_archives_both_attempts(self) -> None:
        rejected = {"verdict": "REJECTED", "reasons": ["tests : 1 failed — libellé secret"],
                    "checks": {"tests": {"ok": False, "executed": ["test_fix.py"],
                                         "timed_out": False, "error": "1 failed — libellé secret"}}}
        accepted = {"verdict": "ACCEPTED", "reasons": [], "checks": {}}
        replay = {"stage": "action", "refused": False, "after_replay_error": None,
                  "outcome": "CORRECTIF_CONFIRME", "after_replay": {"status": "SUCCESS"}}
        summary, calls, counts, safety, review, notify = self._synthetic_attempts(
            "synthetic_retry_static", static_artifacts=[rejected, accepted],
            replay_artifacts=[replay], integrity_artifacts=[{"verdict": "ACCEPTED"}],
            confidence_artifacts=[{"confidence": "HIGH"}], costs=[1.0, 2.0],
        )
        self.assertEqual((len(calls), counts, safety, review, notify),
                         (2, {"static": 2, "replay": 1, "integrity": 1, "confidence": 1}, 1, 1, 0))
        self.assertEqual(summary.attempt_count, 2)
        self.assertEqual([item["cost_usd"] for item in summary.attempts], [1.0, 2.0])
        self.assertEqual([item["num_turns"] for item in summary.attempts], [2, 3])
        relative = dom_evidence_relative_dir(summary.case_id)
        expected_first_prompt = add_dom_evidence_context(
            (self.root / "prompts" / summary.case_id / "prompt.txt").read_text(encoding="utf-8"),
            summary.case_id, calls[0]["worktree_path"] / relative, relative,
        )
        self.assertEqual(calls[0]["prompt_text"], expected_first_prompt)
        self.assertNotEqual(calls[0]["prompt_text"], calls[1]["prompt_text"])
        self.assertIn("tests : échec (détail masqué)", calls[1]["prompt_text"])
        self.assertIn("Corrige le patch présent dans ce worktree", calls[1]["prompt_text"])
        self.assertNotIn("libellé secret", calls[1]["prompt_text"])
        self.assertNotIn("example.invalid", calls[1]["prompt_text"])
        self.assertEqual(calls[0]["max_budget_usd"], DEFAULT_MAX_BUDGET_USD)
        archive = self.root / "pipeline" / summary.case_id / "attempts"
        first = json.loads((archive / "attempt_1" / "static_validation" / "validation_static.json").read_text(encoding="utf-8"))
        second = json.loads((archive / "attempt_2" / "static_validation" / "validation_static.json").read_text(encoding="utf-8"))
        latest = json.loads((self.root / "static" / summary.case_id / "validation_static.json").read_text(encoding="utf-8"))
        self.assertEqual((first["verdict"], second["verdict"], latest["verdict"]),
                         ("REJECTED", "ACCEPTED", "ACCEPTED"))
        first_run = json.loads((archive / "attempt_1" / "claude_invocation" / "run_result.json").read_text(encoding="utf-8"))
        second_run = json.loads((archive / "attempt_2" / "claude_invocation" / "run_result.json").read_text(encoding="utf-8"))
        self.assertEqual((first_run["session_id"], second_run["session_id"]), ("session_0", "session_1"))
        self.assertEqual(summary.as_dict()["attempt_count"], 2)
        self.assertEqual(len(summary.as_dict()["attempts"]), 2)
        interim = json.loads((self.root / "pipeline" / summary.case_id / "pipeline_run.json").read_text(encoding="utf-8"))
        self.assertEqual((interim["attempt_count"], interim["stopped_at"]), (1, STAGE_STATIC_VALIDATION))
        pipeline = write_pipeline_run_summary(summary, out_root=self.root / "pipeline")
        self.assertEqual(json.loads(pipeline.read_text(encoding="utf-8"))["attempt_count"], 2)

    def test_static_retry_classifies_only_patch_failures(self) -> None:
        informative = (
            {"compile": {"ok": False, "files": [{"ok": False, "error": "SyntaxError"}]}},
            {"import": {"ok": False, "files": [{"ok": False, "timed_out": False,
                                                  "error": "ImportError: broken export"}]}},
            {"lint": {"ok": False, "violations": [{"code": "F821"}], "error": None}},
            {"tests": {"ok": False, "executed": ["test_fix.py"], "error": "1 failed"}},
            {"activation": {"ok": False, "skipped": False, "error": "ValueError: invalid registration"}},
        )
        for checks in informative:
            with self.subTest(checks=checks):
                self.assertTrue(_informative_static_rejection({"verdict": "REJECTED", "checks": checks}))
        environmental = (
            {"lint": {"ok": False, "violations": [], "error": "ruff introuvable sur PATH"}},
            {"tests": {"ok": False, "executed": [], "error": "pytest indisponible"}},
            {"tests": {"ok": False, "executed": ["test_fix.py"], "timed_out": True,
                       "error": "budget dépassé"}},
            {"import": {"ok": False, "files": [{"ok": False,
                                                  "error": "ModuleNotFoundError: No module named dependency"}]}},
        )
        for checks in environmental:
            with self.subTest(checks=checks):
                self.assertFalse(_informative_static_rejection({"verdict": "REJECTED", "checks": checks}))

    def test_persistent_replay_retries_after_integrity_and_score(self) -> None:
        accepted = {"verdict": "ACCEPTED", "reasons": [], "checks": {}}
        persistent = {"stage": "action", "refused": False, "after_replay_error": None,
                      "outcome": "BUG_PERSISTANT", "after_replay": {
                          "status": "FAILURE", "validation_error": None,
                          "validation_comparison": {"replayed_failure_types": ["dispatcher_reported_failure"]},
                          "dispatcher_steps": ["strategy=radio_main attempted"],
                      }}
        confirmed = {**persistent, "outcome": "CORRECTIF_CONFIRME"}
        summary, calls, counts, safety, review, notify = self._synthetic_attempts(
            "synthetic_retry_replay", static_artifacts=[accepted, accepted],
            replay_artifacts=[persistent, confirmed],
            integrity_artifacts=[{"verdict": "ACCEPTED"}, {"verdict": "ACCEPTED"}],
            confidence_artifacts=[{"confidence": "REJECT"}, {"confidence": "HIGH"}],
        )
        self.assertEqual((len(calls), counts, safety, review, notify),
                         (2, {"static": 2, "replay": 2, "integrity": 2, "confidence": 2}, 1, 1, 0))
        self.assertIn("rejeu : BUG_PERSISTANT", calls[1]["prompt_text"])
        self.assertIn("dispatcher_reported_failure", calls[1]["prompt_text"])
        self.assertIn("strategy=radio_main attempted", calls[1]["prompt_text"])
        self.assertEqual(summary.attempt_count, 2)
        self.assertEqual(json.loads((self.root / "pipeline" / summary.case_id / "attempts" / "attempt_1"
                                     / "patch_replay" / "patch_replay.json").read_text(encoding="utf-8"))["outcome"],
                         "BUG_PERSISTANT")

    def test_noninformative_results_do_not_retry(self) -> None:
        accepted = {"verdict": "ACCEPTED", "reasons": [], "checks": {}}
        rejected_environment = {"verdict": "REJECTED", "reasons": ["lint : ruff introuvable sur PATH"],
                                "checks": {"lint": {"ok": False, "violations": [],
                                                    "timed_out": False, "error": "ruff introuvable sur PATH"}}}
        rejected_timeout = {"verdict": "REJECTED", "reasons": ["tests : budget dépassé"],
                            "checks": {"tests": {"ok": False, "executed": ["test_fix.py"],
                                                 "timed_out": True, "error": "budget dépassé"}}}
        replay = {"stage": "action", "refused": False, "after_replay_error": None,
                  "outcome": "BUG_PERSISTANT", "after_replay": {"status": "FAILURE", "validation_error": None,
                      "dispatcher_steps": [], "validation_comparison": {}}}
        scenarios = (
            ("env", [rejected_environment], None, None, None, None, None),
            ("timeout", [rejected_timeout], None, None, None, None, None),
            ("empty", [{**accepted, "changed_files": []}], None, None, None, None, None),
            ("session", [accepted], None, None, None, [STATUS_FAILURE], None),
            ("candidate", [accepted], [replay], [{"verdict": "ACCEPTED"}], [{"confidence": "REJECT"}], None, [True]),
            ("inconclusive", [accepted], [{**replay, "outcome": "NON_CONCLUANT"}],
             [{"verdict": "ACCEPTED"}], [{"confidence": "REJECT"}], None, None),
            ("confirmed", [accepted], [{**replay, "outcome": "CORRECTIF_CONFIRME"}],
             [{"verdict": "ACCEPTED"}], [{"confidence": "REJECT"}], None, None),
            ("refused", [accepted], [{**replay, "refused": True}],
             [{"verdict": "ACCEPTED"}], [{"confidence": "REJECT"}], None, None),
            ("replay_error", [accepted], [{**replay, "after_replay_error": "synthetic error"}],
             [{"verdict": "ACCEPTED"}], [{"confidence": "REJECT"}], None, None),
            ("high", [accepted], [replay],
             [{"verdict": "ACCEPTED"}], [{"confidence": "HIGH"}], None, None),
            ("integrity", [accepted], [replay], [{"verdict": "REJECTED"}],
             [{"confidence": "REJECT"}], None, None),
        )
        for suffix, static, replays, integrity, confidence, statuses, candidates in scenarios:
            with self.subTest(suffix=suffix):
                summary, calls, _, _, _, notify = self._synthetic_attempts(
                    "synthetic_no_retry_" + suffix, static_artifacts=static,
                    replay_artifacts=replays, integrity_artifacts=integrity,
                    confidence_artifacts=confidence, statuses=statuses, candidates=candidates,
                )
                self.assertEqual(len(calls), 1)
                self.assertEqual(summary.attempt_count, 1)
                self.assertEqual(notify, 1 if suffix == "empty" else 0)

    def test_cost_limits_unknown_cost_and_single_attempt_option(self) -> None:
        rejected = {"verdict": "REJECTED", "reasons": ["tests : 1 failed"],
                    "checks": {"tests": {"ok": False, "executed": ["test_fix.py"],
                                         "timed_out": False, "error": "1 failed"}}}
        summary, calls, _, _, _, _ = self._synthetic_attempts(
            "synthetic_unknown_cost", static_artifacts=[rejected], costs=[None],
        )
        self.assertEqual((len(calls), summary.attempt_count), (1, 1))
        summary, calls, _, _, _, _ = self._synthetic_attempts(
            "synthetic_cost_exhausted", static_artifacts=[rejected], costs=[4.5],
            max_case_budget_usd=5,
        )
        self.assertEqual(len(calls), 1)
        summary, calls, _, _, _, _ = self._synthetic_attempts(
            "synthetic_cost_reduced", static_artifacts=[rejected, rejected], costs=[5.0, 1.0],
            max_case_budget_usd=7,
        )
        self.assertEqual([call["max_budget_usd"] for call in calls], [5.0, 2.0])
        summary, calls, _, _, _, _ = self._synthetic_attempts(
            "synthetic_one_attempt", static_artifacts=[rejected], costs=[1.0],
            max_attempts=1, max_case_budget_usd=3,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_budget_usd"], DEFAULT_MAX_BUDGET_USD)
        self.assertNotIn("attempt_count", summary.as_dict())
        self.assertFalse((self.root / "pipeline" / summary.case_id / "attempts").exists())

        with patch("Survey.autofix.autofix_orchestrator._archive_case_attempt", return_value=False):
            summary, calls, _, _, _, _ = self._synthetic_attempts(
                "synthetic_archive_failure", static_artifacts=[rejected], costs=[1.0],
            )
        self.assertEqual(len(calls), 1)
        self.assertIn("archivage de tentative incomplet", summary.warnings[0])

    def test_same_patch_or_same_first_failure_stops_before_third_attempt(self) -> None:
        def rejected(reason: str) -> dict:
            return {"verdict": "REJECTED", "reasons": [f"tests : {reason}"],
                    "checks": {"tests": {"ok": False, "executed": ["test_fix.py"],
                                         "timed_out": False, "error": reason}}}
        for suffix, reasons, patches in (
            ("same_patch", ["1 failed", "2 failed"], ["same patch", "same patch"]),
            ("same_cause", ["1 failed", "1 failed"], ["first patch", "second patch"]),
        ):
            with self.subTest(suffix=suffix):
                summary, calls, _, _, _, _ = self._synthetic_attempts(
                    "synthetic_no_progress_" + suffix,
                    static_artifacts=[rejected(reason) for reason in reasons],
                    patch_contents=patches, costs=[1.0, 1.0], max_attempts=3,
                )
                self.assertEqual(len(calls), 2)
                self.assertEqual(summary.attempt_count, 2)

        summary, calls, _, _, _, _ = self._synthetic_attempts(
            "synthetic_max_three",
            static_artifacts=[rejected("1 failed"), rejected("2 failed"), rejected("3 failed")],
            patch_contents=["first patch", "second patch", "third patch"],
            costs=[1.0, 1.0, 1.0], max_attempts=3,
        )
        self.assertEqual((len(calls), summary.attempt_count), (3, 3))

        accepted = {"verdict": "ACCEPTED", "reasons": [], "checks": {}}
        persistent = {"stage": "action", "refused": False, "after_replay_error": None,
                      "outcome": "BUG_PERSISTANT", "after_replay": {
                          "status": "FAILURE", "validation_error": None,
                          "dispatcher_steps": ["strategy=radio_main attempted"],
                          "validation_comparison": {},
                      }}
        summary, calls, _, _, _, _ = self._synthetic_attempts(
            "synthetic_same_replay_steps", static_artifacts=[accepted, accepted],
            replay_artifacts=[persistent, persistent],
            integrity_artifacts=[{"verdict": "ACCEPTED"}, {"verdict": "ACCEPTED"}],
            confidence_artifacts=[{"confidence": "REJECT"}, {"confidence": "REJECT"}],
            patch_contents=["first patch", "second patch"], costs=[1.0, 1.0], max_attempts=3,
        )
        self.assertEqual((len(calls), summary.attempt_count), (2, 2))

    def test_second_attempt_no_changes_notifies_once_with_attempt_number(self) -> None:
        rejected = {"verdict": "REJECTED", "reasons": ["tests : 1 failed"],
                    "checks": {"tests": {"ok": False, "executed": ["test_fix.py"],
                                         "timed_out": False, "error": "1 failed"}}}
        empty = {"verdict": "ACCEPTED", "reasons": [], "checks": {}, "changed_files": []}
        summary, calls, _, _, review, notify = self._synthetic_attempts(
            "synthetic_second_empty", static_artifacts=[rejected, empty], costs=[1.0, 1.0],
        )
        self.assertEqual((len(calls), review, notify), (2, 0, 1))
        self.assertEqual(summary.stopped_at, STAGE_NO_CHANGES)
        self.assertIn("tentative 2", summary._test_notifications[0])

    def test_feedback_is_bounded_and_drops_unrecognized_replay_lines(self) -> None:
        accepted = {"verdict": "ACCEPTED", "reasons": [], "checks": {}}
        step = "apply ok=false strategy=" + "s" * 60 + " reason=" + "r" * 60
        replay = {"stage": "action", "refused": False, "after_replay_error": None,
                  "outcome": "BUG_PERSISTANT", "after_replay": {"status": "FAILURE", "validation_error": None,
                      "dispatcher_steps": [step] * 20,
                      "validation_comparison": {"replayed_failure_types": [
                          "dispatcher_reported_failure", "libellé secret", "https://example.invalid",
                      ]}}}
        summary, calls, _, _, _, _ = self._synthetic_attempts(
            "synthetic_feedback_bound", static_artifacts=[accepted, accepted],
            replay_artifacts=[replay, {**replay, "outcome": "NON_CONCLUANT"}],
            integrity_artifacts=[{"verdict": "ACCEPTED"}, {"verdict": "ACCEPTED"}],
            confidence_artifacts=[{"confidence": "REJECT"}, {"confidence": "REJECT"}],
        )
        self.assertEqual(len(calls), 2)
        addition = calls[1]["prompt_text"][len(calls[0]["prompt_text"]):]
        self.assertLessEqual(len(addition), 1200)
        self.assertNotIn("libellé secret", addition)
        self.assertNotIn("example.invalid", addition)
        self.assertTrue(addition.endswith("ne le réécris pas depuis zéro.\n"))
        unsafe = {**replay, "after_replay": {**replay["after_replay"],
                   "dispatcher_steps": ["question=libellé secret"]}}
        summary, calls, _, _, _, _ = self._synthetic_attempts(
            "synthetic_feedback_unsafe", static_artifacts=[accepted],
            replay_artifacts=[unsafe], integrity_artifacts=[{"verdict": "ACCEPTED"}],
            confidence_artifacts=[{"confidence": "REJECT"}],
        )
        self.assertEqual(len(calls), 1)

    def test_static_rejection_reason_uses_first_bounded_reason_only_when_readable(self) -> None:
        base = 'validation_static.json.verdict=\'REJECTED\' ("ACCEPTED" requis)'
        scenarios = (
            ("one", {"verdict": "REJECTED", "reasons": ["lint : outil absent"]},
             base + " — lint : outil absent"),
            ("many", {"verdict": "REJECTED", "reasons": ["compile : échec", "lint : échec", "tests : échec"]},
             base + " — compile : échec (+2 raison(s) supplémentaire(s))"),
            ("long", {"verdict": "REJECTED", "reasons": ["  lint\n\t" + "x" * 205]},
             base + " — lint " + "x" * 195),
            ("empty", {"verdict": "REJECTED", "reasons": []}, base),
            ("blank", {"verdict": "REJECTED", "reasons": [" \n\t "]}, base),
            ("missing", {"verdict": "REJECTED"}, base),
            ("unreadable", "{invalid json", "validation_static.json.verdict=None (\"ACCEPTED\" requis)"),
            ("malformed", ["unexpected"], "validation_static.json.verdict=None (\"ACCEPTED\" requis)"),
            ("unknown", {"verdict": "UNKNOWN", "reasons": []},
             "validation_static.json.verdict='UNKNOWN' (\"ACCEPTED\" requis)"),
        )
        for suffix, artifact, expected in scenarios:
            with self.subTest(suffix=suffix):
                prepared, _ = self._prepare("synthetic_static_" + suffix, {})
                invocation = ClaudeInvocationResult(
                    case_id=prepared.case_id, branch=prepared.branch,
                    worktree_path=str(prepared.worktree_path), status=STATUS_SUCCESS,
                    exit_code=0, timed_out=False, session_id=None, is_error=False,
                    subtype="success", command=[], raw_stdout="{}", raw_stderr="", error=None,
                )
                static_path = self.root / "static" / prepared.case_id / "validation_static.json"
                static_path.parent.mkdir(parents=True, exist_ok=True)
                content = artifact if isinstance(artifact, str) else json.dumps(artifact)
                static_path.write_text(content, encoding="utf-8")
                with patch("Survey.autofix.autofix_orchestrator._check_case_parallel_safety",
                           return_value=SafetyOutcome(checked=True, safe=True, reason=None)), \
                     patch("Survey.autofix.autofix_orchestrator.invoke_claude_headless",
                           return_value=invocation), \
                     patch("Survey.autofix.autofix_orchestrator.write_static_validation",
                           return_value=static_path), \
                     patch("Survey.autofix.autofix_orchestrator.write_patch_replay") as replay:
                    summary = self._process_prepared_case(prepared)
                self.assertEqual(summary.stop_reason, expected)
                self.assertEqual(summary.stopped_at, STAGE_STATIC_VALIDATION)
                self.assertFalse(summary.is_error)
                self.assertEqual(static_path.read_text(encoding="utf-8"), content)
                replay.assert_not_called()
                output = write_pipeline_run_summary(summary, out_root=self.root / "pipeline")
                self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["stop_reason"], expected)

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
        self.assertNotIn("agent_final_summary", run_data)
        candidate = json.loads(Path(first.artifacts["core_change_candidate"]).read_text(encoding="utf-8"))
        self.assertEqual(candidate["case_id"], result.case_id)
        self.assertEqual(candidate["signal"], "CORE_CHANGE_CANDIDATE")
        self.assertEqual(candidate["reason"], final_text)
        self.assertEqual(candidate["agent_final_text"], final_text)
        self.assertNotIn("agent_final_summary", candidate)
        self.assertEqual(first.stop_reason, final_text)
        self.assertEqual(
            notify.call_args.args[0],
            f"Autofix case={result.case_id} : aucun changement. {final_text}\n"
            f"Artefact : {first.artifacts['core_change_candidate']}",
        )

    def test_empty_diff_uses_final_summary_for_reason_notification_and_candidate(self) -> None:
        result, _ = self._prepare("synthetic_summary_no_changes", {})
        final_text = "Introduction longue sans conclusion utile. CORE_CHANGE_CANDIDATE\nRÉSUMÉ : Aucun correctif fiable établi."
        invocation = ClaudeInvocationResult(
            case_id=result.case_id, branch=result.branch, worktree_path=str(result.worktree_path),
            status=STATUS_SUCCESS, exit_code=0, timed_out=False, session_id=None,
            is_error=False, subtype="success", command=[], raw_stdout="{}", raw_stderr="",
            error=None, agent_final_text=final_text, agent_final_summary="Aucun correctif fiable établi.",
            declares_core_change_candidate=True,
        )
        with patch("Survey.autofix.autofix_orchestrator._check_case_parallel_safety",
                   return_value=SafetyOutcome(checked=True, safe=True, reason=None)), \
             patch("Survey.autofix.autofix_orchestrator.invoke_claude_headless", return_value=invocation), \
             patch("Survey.autofix.autofix_orchestrator.write_static_validation",
                   side_effect=lambda *_args, **_kwargs: self._write_synthetic_static(result.case_id, [])), \
             patch("Survey.autofix.autofix_orchestrator.send_status_notification") as notify:
            summary = self._process_prepared_case(result)
        self.assertEqual(summary.stopped_at, STAGE_NO_CHANGES)
        self.assertEqual(summary.stop_reason, "Aucun correctif fiable établi.")
        self.assertEqual(
            notify.call_args.args[0],
            f"Autofix case={result.case_id} : aucun changement. Aucun correctif fiable établi.\n"
            f"Artefact : {summary.artifacts['core_change_candidate']}",
        )
        run_data = json.loads((self.root / "runs" / result.case_id / "run_result.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["agent_final_text"], final_text)
        self.assertEqual(run_data["agent_final_summary"], "Aucun correctif fiable établi.")
        candidate = json.loads(Path(summary.artifacts["core_change_candidate"]).read_text(encoding="utf-8"))
        self.assertEqual(candidate["agent_final_summary"], "Aucun correctif fiable établi.")
        self.assertTrue(candidate["reason"].startswith("Introduction longue"))
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


class AutofixUpstreamFingerprintTests(unittest.TestCase):
    fingerprint = {"base_sha": "a" * 40, "dirty": False}

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _case(self, case_id: str, fingerprint=None) -> Path:
        case_dir = self.root / "failure_cases" / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "manifest.json").write_text("{}", encoding="utf-8")
        if fingerprint is not None:
            self._diagnosis(case_id, fingerprint)
        return case_dir

    def _diagnosis(self, case_id: str, fingerprint=None) -> Path:
        path = self.root / "diagnoses" / case_id / "diagnosis.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"case_id": case_id, "stage": "action"}
        if fingerprint is not None:
            data["code_fingerprint"] = fingerprint
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _structured_diagnosis(self, case_id: str, *, stage="action", fingerprint=None,
                              steps=None, modules=None) -> Path:
        path = self._diagnosis(case_id, self.fingerprint if fingerprint is None else fingerprint)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update({
            "stage": stage, "itype": "radio",
            "symptom": {"failure_types": ["action_not_applied"]},
            "real_dispatch_replay": {"dispatcher_steps": steps if steps is not None else [
                "strategy=target_id verification=failed",
                "strategy=target_id verification=failed",
                "apply ok=false strategy=none reason=no_strategy",
            ]},
            "modules_likely_involved": modules if modules is not None else [
                {"module": "Survey/synthetic_extractor.py", "matched_signals": ["context_flag:synthetic_widget"]},
            ],
            "target_id": "internal_target_secret", "provider_domain": "private.example",
        })
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _historical_outcome(self, case_id: str, outcome: str) -> None:
        roots = {
            "REJECT": ("confidence", "confidence_score.json", {"confidence": "REJECT"}),
            "NO_CHANGES": ("pipeline", "pipeline_run.json", {"stopped_at": "NO_CHANGES", "is_error": False}),
            "STATIC_REJECTED": ("static", "validation_static.json", {"verdict": "REJECTED"}),
        }
        directory, filename, payload = roots[outcome]
        path = self.root / directory / case_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _invoke(self, *, budget=5, writer=None, fingerprint_unavailable=False,
                retry_previous_failures=False):
        def write_synthetic(case_dir, *, out_root, force):
            return self._diagnosis(case_dir.name, self.fingerprint)

        with (
            patch("Survey.autofix.autofix_orchestrator.get_code_fingerprint",
                  return_value=None if fingerprint_unavailable else self.fingerprint),
            patch("Survey.autofix.autofix_orchestrator.write_diagnosis",
                  side_effect=writer or write_synthetic) as write,
            patch("Survey.autofix.autofix_orchestrator.write_case_groups",
                  return_value=(SimpleNamespace(groups=[]), self.root / "groups.json")) as grouping,
            patch("Survey.autofix.autofix_orchestrator._process_upstream_case",
                  side_effect=lambda case_id, **_kwargs: UpstreamCaseSummary(
                      case_id, "worktree", None, True, False)) as process,
        ):
            summaries = run_upstream_stage(
                failure_cases_root=self.root / "failure_cases",
                diagnoses_root=self.root / "diagnoses",
                context_selections_root=self.root / "context",
                prompts_root=self.root / "prompts",
                worktrees_root=self.root / "worktrees",
                pipeline_runs_root=self.root / "pipeline",
                confidence_scores_root=self.root / "confidence",
                static_validations_root=self.root / "static",
                human_reviews_root=self.root / "human",
                merge_reviews_root=self.root / "merge_reviews",
                merge_results_root=self.root / "merges",
                retry_previous_failures=retry_previous_failures,
                max_upstream_cases=budget,
            )
        return summaries, write, grouping, process

    def test_write_diagnosis_adds_optional_fingerprint_without_schema_change(self) -> None:
        result = SimpleNamespace(
            case_id="synthetic", cause_level="plausible", confidence_global="plausible",
            modules_likely_involved=[],
            as_dict=lambda: {"schema_version": "1.0", "case_id": "synthetic"},
        )
        with (
            patch("Survey.autofix.failure_diagnosis.get_code_fingerprint",
                  return_value=self.fingerprint),
            patch("Survey.autofix.failure_diagnosis.diagnose_failure_case", return_value=result),
        ):
            path = write_diagnosis(self.root / "synthetic", out_root=self.root / "diagnoses")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["code_fingerprint"], self.fingerprint)

    def test_diagnosis_remains_readable_when_git_fingerprint_is_unavailable(self) -> None:
        result = SimpleNamespace(
            case_id="synthetic", cause_level="plausible", confidence_global="plausible",
            modules_likely_involved=[],
            as_dict=lambda: {"schema_version": "1.0", "case_id": "synthetic"},
        )
        with (
            patch("Survey.autofix.failure_diagnosis.get_code_fingerprint", return_value=None),
            patch("Survey.autofix.failure_diagnosis.diagnose_failure_case", return_value=result),
        ):
            path = write_diagnosis(self.root / "synthetic", out_root=self.root / "diagnoses")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result.as_dict())

    def test_matching_fingerprint_reuses_diagnosis(self) -> None:
        self._case("case_matching", self.fingerprint)
        summaries, write, grouping, process = self._invoke()
        write.assert_not_called()
        grouping.assert_called_once()
        self.assertEqual([s.case_id for s in summaries], ["case_matching"])
        self.assertEqual(process.call_count, 1)

    def test_changed_missing_and_dirty_fingerprints_regenerate_once(self) -> None:
        for case_id, fingerprint in (
            ("case_changed", {"base_sha": "b" * 40, "dirty": False}),
            ("case_missing", None),
            ("case_dirty", {"base_sha": "a" * 40, "dirty": True}),
        ):
            self._case(case_id)
            self._diagnosis(case_id, fingerprint)
        with patch("Survey.autofix.autofix_orchestrator.log_info") as info:
            summaries, write, grouping, process = self._invoke(budget=3)
        self.assertEqual(write.call_count, 3)
        self.assertTrue(all(call.kwargs["force"] for call in write.call_args_list))
        self.assertEqual(
            len([call for call in info.call_args_list if "régénération diagnostic" in call.args[1]]),
            3,
        )
        self.assertEqual(process.call_count, 3)
        self.assertEqual({s.case_id for s in summaries},
                         {"case_changed", "case_missing", "case_dirty"})
        grouping.assert_called_once()

    def test_exhausted_budget_defers_stale_case_without_forwarding_it(self) -> None:
        self._case("case_a_new")
        self._case("case_b_stale", {"base_sha": "b" * 40, "dirty": False})
        summaries, write, grouping, process = self._invoke(budget=1)
        write.assert_called_once()
        self.assertEqual(write.call_args.kwargs["force"], False)
        grouping.assert_not_called()
        self.assertEqual([call.args[0] for call in process.call_args_list], ["case_a_new"])
        self.assertEqual([s.case_id for s in summaries], ["case_a_new"])
        self.assertTrue((self.root / "diagnoses" / "case_b_stale" / "diagnosis.json").is_file())

    def test_regeneration_failure_stops_at_diagnosis(self) -> None:
        self._case("case_failed", {"base_sha": "b" * 40, "dirty": False})
        def fail_diagnosis(*_args, **_kwargs):
            raise DiagnosisError("échec synthétique")
        summaries, write, grouping, process = self._invoke(
            writer=fail_diagnosis,
        )
        write.assert_called_once()
        grouping.assert_not_called()
        process.assert_not_called()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].stopped_at, STAGE_UPSTREAM_DIAGNOSIS)
        self.assertTrue(summaries[0].is_error)
        self.assertIn("échec synthétique", summaries[0].stop_reason)

    def test_case_with_worktree_is_not_reexamined(self) -> None:
        self._case("case_with_worktree", {"base_sha": "b" * 40, "dirty": False})
        manifest = self.root / "worktrees" / "case_with_worktree" / "worktree.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}", encoding="utf-8")
        summaries, write, grouping, process = self._invoke()
        self.assertEqual(summaries, [])
        write.assert_not_called()
        process.assert_not_called()

    def test_terminal_case_is_not_reexamined(self) -> None:
        self._case("case_terminal", {"base_sha": "b" * 40, "dirty": False})
        run = self.root / "pipeline" / "case_terminal" / "upstream_run.json"
        run.parent.mkdir(parents=True)
        run.write_text(json.dumps({"terminal": True}), encoding="utf-8")
        summaries, write, grouping, process = self._invoke()
        self.assertEqual(summaries, [])
        write.assert_not_called()
        process.assert_not_called()

    def test_unavailable_current_fingerprint_is_conservatively_stale(self) -> None:
        self._case("case_unknown", self.fingerprint)
        summaries, write, grouping, process = self._invoke(fingerprint_unavailable=True)
        write.assert_called_once()
        self.assertTrue(write.call_args.kwargs["force"])
        grouping.assert_not_called()
        process.assert_not_called()
        self.assertEqual(summaries, [])

    def test_historical_reject_stops_before_context_with_safe_artifact(self) -> None:
        self._structured_diagnosis("case_old")
        self._historical_outcome("case_old", "REJECT")
        self._case("case_new")
        self._structured_diagnosis("case_new")
        with patch("Survey.autofix.autofix_orchestrator.log_info") as info, \
             patch("Survey.autofix.autofix_orchestrator.send_status_notification") as notify:
            summaries, write, _grouping, process = self._invoke()
        write.assert_not_called()
        process.assert_not_called()
        notify.assert_not_called()
        self.assertEqual(len([call for call in info.call_args_list
                              if "historical_duplicate" in call.args[1]]), 1)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].stopped_at, STAGE_UPSTREAM_HISTORY_DUPLICATE)
        self.assertTrue(summaries[0].terminal)
        artifact = json.loads((self.root / "pipeline" / "case_new" / "upstream_run.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["equivalent_case_id"], "case_old")
        self.assertEqual(artifact["equivalent_outcome"], "REJECT")
        self.assertIn("--retry-previous-failures", artifact["retry_hint"])
        self.assertNotIn("internal_target_secret", json.dumps(artifact))
        self.assertNotIn("private.example", json.dumps(artifact))
        for root in ("context", "prompts", "worktrees", "runs"):
            self.assertFalse((self.root / root / "case_new").exists())

    def test_historical_no_changes_and_static_rejection_are_reused(self) -> None:
        self._structured_diagnosis("case_no_changes")
        self._historical_outcome("case_no_changes", "NO_CHANGES")
        self._structured_diagnosis("case_static_rejected", stage="extraction")
        self._historical_outcome("case_static_rejected", "STATIC_REJECTED")
        self._case("new_action")
        self._structured_diagnosis("new_action")
        self._case("new_extraction")
        self._structured_diagnosis("new_extraction", stage="extraction")
        summaries, _write, _grouping, process = self._invoke()
        process.assert_not_called()
        self.assertEqual(
            {(s.case_id, s.equivalent_outcome) for s in summaries},
            {("new_action", "NO_CHANGES"), ("new_extraction", "REJECTED")},
        )

    def test_changed_fingerprint_or_missing_signature_continues_normally(self) -> None:
        self._structured_diagnosis("case_old", fingerprint={"base_sha": "b" * 40, "dirty": False})
        self._historical_outcome("case_old", "REJECT")
        for case_id in ("changed_code", "action_without_steps", "extraction_without_module"):
            self._case(case_id)
        self._structured_diagnosis("changed_code")
        self._structured_diagnosis("action_without_steps", steps=[])
        self._structured_diagnosis("extraction_without_module", stage="extraction", modules=[])
        summaries, _write, _grouping, process = self._invoke()
        self.assertEqual(process.call_count, 3)
        self.assertTrue(all(s.stopped_at == "worktree" for s in summaries))

    def test_merged_approved_and_in_flight_history_do_not_deduplicate(self) -> None:
        for case_id in ("merged", "approved", "in_flight"):
            self._structured_diagnosis(case_id)
        self._historical_outcome("merged", "REJECT")
        self._historical_outcome("approved", "REJECT")
        for directory, case_id, filename, payload in (
            ("merges", "merged", "merge_result.json", {"status": "MERGED"}),
            ("human", "approved", "decision.json", {"decision": "APPROVED"}),
        ):
            path = self.root / directory / case_id / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        self._case("new_case")
        self._structured_diagnosis("new_case")
        summaries, _write, _grouping, process = self._invoke()
        process.assert_called_once()
        self.assertEqual(summaries[0].stopped_at, "worktree")

    def test_history_limit_abandons_deduplication_and_retry_option_bypasses_it(self) -> None:
        for case_id in ("old_a", "old_b"):
            self._structured_diagnosis(case_id)
            self._historical_outcome(case_id, "REJECT")
        self._case("new_case")
        self._structured_diagnosis("new_case")
        with patch("Survey.autofix.autofix_orchestrator._MAX_COMPLETED_HISTORY_CASES", 1):
            summaries, _write, _grouping, process = self._invoke()
        process.assert_called_once()
        self.assertEqual(summaries[0].stopped_at, "worktree")

        # Une relance explicite ignore aussi un arrêt historical_duplicate déjà écrit.
        run = self.root / "pipeline" / "new_case" / "upstream_run.json"
        run.write_text(json.dumps({"terminal": True, "stopped_at": STAGE_UPSTREAM_HISTORY_DUPLICATE}), encoding="utf-8")
        summaries, _write, _grouping, process = self._invoke(retry_previous_failures=True)
        process.assert_called_once()
        self.assertEqual(summaries[0].stopped_at, "worktree")

    def test_signature_contains_only_technical_codes(self) -> None:
        path = self._structured_diagnosis("action_signature", steps=[
            "action_fix selected fix_id=secretname",
            "action_fix selected fix_id=anothersecret",
            "strategy=target_id verification=failed",
        ])
        diagnosis = json.loads(path.read_text(encoding="utf-8"))
        signature = _diagnosis_signature(diagnosis)
        self.assertIsNotNone(signature)
        self.assertEqual(signature[-1], ("action_fix selected", "strategy=target_id verification=failed"))
        for secret in ("secretname", "anothersecret", "internal_target_secret", "private.example"):
            self.assertNotIn(secret, repr(signature))
        path = self._structured_diagnosis("extraction_signature", stage="extraction")
        diagnosis = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsNotNone(_diagnosis_signature(diagnosis))
        diagnosis["modules_likely_involved"][0]["matched_signals"] = ["group_key=survey_private_answer"]
        self.assertIsNone(_diagnosis_signature(diagnosis))

    def test_cli_retry_option_is_explicit_and_disabled_by_default(self) -> None:
        from tools.run_autofix_pipeline import main

        with patch("tools.run_autofix_pipeline.run_autofix_pipeline", return_value=([], [], [])) as run:
            self.assertEqual(main([]), 0)
            self.assertFalse(run.call_args.kwargs["retry_previous_failures"])
            self.assertEqual(main(["--retry-previous-failures"]), 0)
            self.assertTrue(run.call_args.kwargs["retry_previous_failures"])
