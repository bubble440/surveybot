from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from Survey.extractor_integrity import _hash_function
from Survey.autofix.confidence_score import (
    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CRITERION_MISSING,
    _confidence_and_reason, _integrity_criterion, score_patch_confidence,
)
from Survey.autofix.extractor_integrity_gate import (
    BASELINE_MISMATCH, EXPECTED_CHANGE, UNCHANGED, UNEXPECTED_CHANGE,
    check_extractor_integrity,
)
from Survey.autofix.human_review import _compose_message


_SOURCE = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"


class IntegrityStatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q", cwd=self.repo)
        self._git("config", "user.email", "test@example.invalid", cwd=self.repo)
        self._git("config", "user.name", "Test", cwd=self.repo)
        survey = self.repo / "Survey"
        survey.mkdir()
        (self.repo / "tools").mkdir()
        (self.repo / "tools" / "placeholder.txt").write_text("test", encoding="utf-8")
        source_integrity = Path(__file__).resolve().parents[1] / "Survey" / "extractor_integrity.py"
        (survey / "extractor_integrity.py").write_bytes(source_integrity.read_bytes())
        (survey / "core.py").write_text(_SOURCE, encoding="utf-8")
        self.registry = {
            f"core.py::{name}": {"hash": _hash_function(survey, "core.py", name)}
            for name in ("alpha", "beta")
        }
        self._write_registry(self.registry)

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=cwd or self.repo, capture_output=True, text=True, check=True,
        )
        return proc.stdout.strip()

    def _write_registry(self, registry: dict) -> None:
        (self.repo / "Survey" / "extractor_integrity.json").write_text(
            json.dumps(registry), encoding="utf-8",
        )

    def _prepare(self, *, case_id: str = "case_a") -> None:
        self._git("add", ".")
        self._git("commit", "-qm", "base")
        self.base_sha = self._git("rev-parse", "HEAD")
        self.worktree = self.root / "patch"
        self.branch = f"autofix/{case_id}"
        self._git("worktree", "add", "-qb", self.branch, str(self.worktree), self.base_sha)
        self.case_id = case_id
        self.manifest = self.root / "autofix_worktrees" / case_id / "worktree.json"
        self.static = self.root / "autofix_static_validations" / case_id / "validation_static.json"
        self._json(self.manifest, {
            "case_id": case_id, "branch": self.branch, "base_sha": self.base_sha,
            "worktree_path": str(self.worktree),
        })
        self._json(self.static, {
            "case_id": case_id, "branch": self.branch, "base_sha": self.base_sha,
            "verdict": "ACCEPTED",
        })

    @staticmethod
    def _json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def _patch(self, source: str) -> None:
        (self.worktree / "Survey" / "core.py").write_text(source, encoding="utf-8")

    def _check(self, **kwargs):
        return check_extractor_integrity(self.manifest, self.static, **kwargs)

    def _evidence(self, key: str) -> dict:
        base_hash = self.registry[key]["hash"]
        name = key.split("::", 1)[1]
        patched_hash = _hash_function(self.worktree / "Survey", "core.py", name)
        intent = self.root / "expected_core_changes" / self.case_id / "expected_change.json"
        diagnosis = self.root / "diagnoses" / self.case_id / "diagnosis.json"
        replay = self.root / "patch_replays" / self.case_id / "patch_replay.json"
        self._json(intent, {
            "schema_version": "1.0", "case_id": self.case_id, "base_sha": self.base_sha,
            "changes": [{"function_id": key, "base_hash": base_hash, "patched_hash": patched_hash}],
        })
        self._json(diagnosis, {
            "case_id": self.case_id, "stage": "extraction",
            "confidence_global": "certain", "case_incomplete": False,
            "replay": {"verdict": "REPRODUIT"},
        })
        self._json(replay, {
            "case_id": self.case_id, "stage": "extraction", "refused": False,
            "outcome": "CORRECTIF_CONFIRME",
            "patch_validated": True,
            "worktree": {"branch": self.branch, "base_sha": self.base_sha, "path": str(self.worktree)},
        })
        return {"expected_changes_path": intent, "diagnosis_path": diagnosis, "patch_replay_path": replay}

    def test_unchanged_and_distinct_keys(self) -> None:
        self._prepare()
        result = self._check()
        self.assertEqual(result.verdict, "ACCEPTED")
        self.assertEqual([r["state"] for r in result.functions], [UNCHANGED, UNCHANGED])
        self.assertEqual(len({r["key"] for r in result.functions}), 2)

    def test_cli_keeps_binary_exit_contract_and_writes_states(self) -> None:
        self._prepare()
        cli = Path(__file__).resolve().parents[1] / "tools" / "check_extractor_integrity.py"
        output = self.root / "extractor_integrity_checks"
        proc = subprocess.run(
            [sys.executable, str(cli), str(self.manifest), str(self.static), "--out-root", str(output)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads((output / self.case_id / "extractor_integrity_check.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], "2.0")
        self.assertEqual(data["state_counts"][UNCHANGED], 2)

    def test_unexpected_change_and_deletion(self) -> None:
        self._prepare()
        self._patch(_SOURCE.replace("return 1", "return 3"))
        result = self._check()
        self.assertEqual(result.verdict, "REJECTED")
        self.assertEqual(result.functions[0]["state"], UNEXPECTED_CHANGE)
        self._patch("def beta():\n    return 2\n")
        deleted = self._check()
        self.assertEqual(deleted.functions[0]["state"], UNEXPECTED_CHANGE)
        self.assertIn("patch_error", deleted.functions[0])

    def test_committed_patch_is_compared_to_original_base_sha(self) -> None:
        self._prepare()
        self._patch(_SOURCE.replace("return 1", "return 3"))
        self._git("add", "Survey/core.py", cwd=self.worktree)
        self._git("commit", "-qm", "patch", cwd=self.worktree)
        result = self._check()
        self.assertEqual(result.functions[0]["state"], UNEXPECTED_CHANGE)
        self.assertEqual(result.base_sha, self.base_sha)

    def test_preexisting_mismatch_retains_patch_delta(self) -> None:
        self.registry["core.py::alpha"]["hash"] = "0" * 64
        self._write_registry(self.registry)
        self._prepare()
        before = self._check()
        self.assertEqual(before.functions[0]["state"], BASELINE_MISMATCH)
        self.assertFalse(before.functions[0]["changed_by_patch"])
        self._patch(_SOURCE.replace("return 1", "return 3"))
        after = self._check()
        self.assertEqual(after.functions[0]["state"], BASELINE_MISMATCH)
        self.assertTrue(after.functions[0]["changed_by_patch"])
        self.assertEqual(after.verdict, "REJECTED")

    def test_expected_change_requires_all_evidence_and_exact_hashes(self) -> None:
        self._prepare()
        self._patch(_SOURCE.replace("return 1", "return 3"))
        evidence = self._evidence("core.py::alpha")
        result = self._check(**evidence)
        self.assertEqual(result.functions[0]["state"], EXPECTED_CHANGE)
        self.assertEqual(result.functions[1]["state"], UNCHANGED)
        self.assertEqual(result.verdict, "ACCEPTED")
        self.assertTrue(result.as_dict()["review_required"])
        evidence["patch_replay_path"].unlink()
        self.assertEqual(self._check(**evidence).functions[0]["state"], UNEXPECTED_CHANGE)
        self.assertEqual(self._check(**evidence).verdict, "REJECTED")

    def test_mixed_expected_and_unexpected_rejected(self) -> None:
        self._prepare()
        self._patch(_SOURCE.replace("return 1", "return 3").replace("return 2", "return 4"))
        result = self._check(**self._evidence("core.py::alpha"))
        self.assertEqual([r["state"] for r in result.functions], [EXPECTED_CHANGE, UNEXPECTED_CHANGE])
        self.assertEqual(result.verdict, "REJECTED")

    def test_expected_declaration_in_patch_or_wrong_hash_is_rejected(self) -> None:
        self._prepare()
        self._patch(_SOURCE.replace("return 1", "return 3"))
        evidence = self._evidence("core.py::alpha")
        intent = json.loads(evidence["expected_changes_path"].read_text(encoding="utf-8"))
        intent["changes"][0]["patched_hash"] = "f" * 64
        self._json(evidence["expected_changes_path"], intent)
        self.assertEqual(self._check(**evidence).functions[0]["state"], UNEXPECTED_CHANGE)
        intent["changes"][0]["patched_hash"] = _hash_function(self.worktree / "Survey", "core.py", "alpha")
        inside = self.worktree / "expected_change.json"
        self._json(inside, intent)
        evidence["expected_changes_path"] = inside
        self.assertEqual(self._check(**evidence).verdict, "REJECTED")

    def test_patch_cannot_change_registry_or_hash_algorithm(self) -> None:
        self._prepare()
        registry_file = self.worktree / "Survey" / "extractor_integrity.json"
        registry_file.write_text(json.dumps({}), encoding="utf-8")
        self.assertEqual(self._check().verdict, "REJECTED")
        self.assertTrue(any(e["key"] == "<registry>" for e in self._check().errors))
        self.registry["core.py::gamma"] = {"hash": "a" * 64}
        registry_file.write_text(json.dumps(self.registry), encoding="utf-8")
        self.assertTrue(any(e["key"] == "<registry>" for e in self._check().errors))
        self.registry.pop("core.py::gamma")
        registry_file.write_text(json.dumps(self.registry), encoding="utf-8")
        algorithm = self.worktree / "Survey" / "extractor_integrity.py"
        algorithm.write_text(algorithm.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        self.assertTrue(any(e["key"] == "<hash_algorithm>" for e in self._check().errors))

    def test_budget_and_missing_integrity_cannot_produce_high(self) -> None:
        self._prepare()
        result = self._check(time_budget_s=1e-12)
        self.assertEqual(result.verdict, "REJECTED")
        self.assertTrue(result.budget_exceeded)
        integrity, _ = _integrity_criterion(None, "missing")
        self.assertEqual(integrity, CRITERION_MISSING)
        self.assertEqual(_confidence_and_reason("PASS", integrity, "PASS")[0], CONFIDENCE_MEDIUM)
        self.assertEqual(_confidence_and_reason("PASS", "PASS", "PASS")[0], CONFIDENCE_HIGH)
        self.assertEqual(_integrity_criterion({"schema_version": "1.0", "verdict": "ACCEPTED"}, None)[0], CRITERION_MISSING)
        self.assertNotEqual(_integrity_criterion({
            "schema_version": "2.0", "verdict": "ACCEPTED", "functions": [{"key": "x", "state": "UNCHANGED"}],
            "checked_entries": 1, "total_entries": 1, "budget_exceeded": False, "errors": [],
        }, None)[0], "PASS")
        self.assertEqual(_integrity_criterion({
            "schema_version": "2.0", "verdict": "ACCEPTED", "functions": [{"key": [], "state": "UNCHANGED"}],
            "checked_entries": 1, "total_entries": 1, "budget_exceeded": False, "errors": [],
        }, None)[0], "FAIL")

    def test_phase12_requires_current_integrity_artifact(self) -> None:
        self._prepare()
        self._patch(_SOURCE.replace("return 1", "return 3"))
        evidence = self._evidence("core.py::alpha")
        integrity = self.root / "extractor_integrity_checks" / self.case_id / "extractor_integrity_check.json"
        self._json(integrity, self._check(**evidence).as_dict())
        score = score_patch_confidence(
            worktree_manifest_path=self.manifest, validation_static_path=self.static,
            patch_replay_path=evidence["patch_replay_path"], extractor_integrity_check_path=integrity,
        )
        self.assertEqual(score.confidence, CONFIDENCE_HIGH)
        self.assertTrue(score.core_review_required)
        message = _compose_message(
            case_id=self.case_id, diagnosis=json.loads(evidence["diagnosis_path"].read_text(encoding="utf-8")),
            confidence_score=score.as_dict(),
        )
        self.assertIn("Changement attendu du core", message)
        self.assertIn("core.py::alpha", message)
        integrity.unlink()
        missing = score_patch_confidence(
            worktree_manifest_path=self.manifest, validation_static_path=self.static,
            patch_replay_path=evidence["patch_replay_path"], extractor_integrity_check_path=integrity,
        )
        self.assertEqual(missing.confidence, CONFIDENCE_MEDIUM)


if __name__ == "__main__":
    unittest.main()
