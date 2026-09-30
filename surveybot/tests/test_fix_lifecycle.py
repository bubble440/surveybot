from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from Survey.extractor_integrity import _hash_function
from Survey.external_fix_registry import DomCondition, ExternalFix, ExternalFixRegistry
from Survey.autofix.fix_lifecycle import (
    EVOLUTION_CANDIDATE, NEW, OBSERVED, STABLE, VALIDATED, FixLifecycleError,
    advance_fix_lifecycle, create_fix_lifecycle, load_fix_lifecycle,
    mark_core_change_candidate, prepare_evolution_candidate,
)


class FixLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.survey = self.root / "Survey"
        self.survey.mkdir()
        (self.survey / "core.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        self.core_hash = _hash_function(self.survey, "core.py", "alpha")
        (self.survey / "extractor_integrity.json").write_text(json.dumps({
            "core.py::alpha": {"hash": self.core_hash},
        }), encoding="utf-8")
        self.registry = ExternalFixRegistry(root=self.survey)
        self.lifecycle = self.root / "fix_lifecycle"
        self.candidates = self.root / "evolution_candidates"
        self.fix = self._register("sample_fix", ("case_one",))
        self.fix_path = create_fix_lifecycle(self.fix, registry=self.registry, out_root=self.lifecycle)

    def _register(self, fix_id: str, cases: tuple[str, ...], *, stage: str = "extraction") -> ExternalFix:
        fix = ExternalFix(
            fix_id=fix_id, core_function_id="core.py::alpha", expected_core_hash=self.core_hash,
            anchor_function_id="core.py::alpha", position="before", case_ids=cases,
            condition=DomCondition(required_selectors=("div.synthetic",)),
            handler=lambda *_: None, stage=stage,
        )
        self.registry.register(fix)
        return fix

    def _json(self, name: str, value: dict) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _observed(self, *, unavailable: bool = True) -> None:
        if unavailable:
            evidence = {"usage_snapshot_paths": [], "usage_unavailable": True}
        else:
            metric = self._json("metrics/producer.json", {
                "schema_version": "1.0", "producer_id": "producer_one", "functions": [{
                    "function_id": "core.py::alpha", "stage": "extraction", "code_hash": self.core_hash,
                    "call_count": 10, "first_called_at": "2026-09-01T00:00:00+00:00",
                    "last_called_at": "2026-09-02T00:00:00+00:00",
                }, {
                    "function_id": "core.py::alpha", "stage": "extraction", "code_hash": "f" * 64,
                    "call_count": 500, "first_called_at": "2026-09-01T00:00:00+00:00",
                    "last_called_at": "2026-09-02T00:00:00+00:00",
                }],
            })
            evidence = {"usage_snapshot_paths": [str(metric)], "usage_unavailable": False}
        advance_fix_lifecycle(self.fix.fix_id, OBSERVED, evidence, out_root=self.lifecycle)

    def _validation_evidence(self, cases: tuple[str, ...] = ("case_one",)) -> dict:
        static = self._json("static.json", {
            "case_id": "case_one", "verdict": "ACCEPTED", "branch": "autofix/case_one", "base_sha": "abc",
        })
        integrity = self._json("integrity.json", {
            "schema_version": "2.0", "case_id": "case_one", "verdict": "ACCEPTED",
            "branch": "autofix/case_one", "base_sha": "abc", "errors": [],
            "budget_exceeded": False, "checked_entries": 1, "total_entries": 1,
            "functions": [{"key": "core.py::alpha", "state": "UNCHANGED", "patched_hash": self.core_hash}],
        })
        diagnoses, replays = {}, {}
        for case_id in cases:
            diagnoses[case_id] = str(self._json(f"diagnoses/{case_id}.json", {
                "case_id": case_id, "stage": "extraction", "confidence_global": "certain",
                "case_incomplete": False, "replay": {"verdict": "REPRODUIT"},
            }))
            replays[case_id] = str(self._json(f"replays/{case_id}.json", {
                "case_id": case_id, "stage": "extraction", "refused": False,
                "patch_validated": True, "outcome": "CORRECTIF_CONFIRME",
                "before_signal": {"replay_verdict": "REPRODUIT"},
                "worktree": {"base_sha": "abc", "branch": "autofix/case_one"},
            }))
        return {
            "diagnosis_paths": diagnoses, "patch_replay_paths": replays,
            "static_validation_path": str(static), "integrity_path": str(integrity),
            "neighbor_replay_paths": {}, "no_neighbor_cases_reason": "none_identified",
        }

    def _validated(self) -> None:
        self._observed()
        advance_fix_lifecycle(self.fix.fix_id, VALIDATED, self._validation_evidence(), out_root=self.lifecycle)

    def _stable(self) -> None:
        self._validated()
        human = self._json("human.json", {"case_id": "case_one", "decision": "APPROVED"})
        review = self._json("merge_review.json", {"case_id": "case_one", "decision": "APPROVED"})
        merge = self._json("merge.json", {"case_id": "case_one", "status": "MERGED"})
        observation = self._json("observation.json", {
            "schema_version": "1.0", "fix_id": self.fix.fix_id,
            "core_function_id": self.fix.core_function_id, "core_hash": self.core_hash,
            "period_start": "2026-09-01T00:00:00+00:00", "period_end": "2026-09-30T00:00:00+00:00",
            "evaluation_count": 100, "activation_count": 10, "incident_count": 0,
            "contradictions_checked": True,
        })
        self.stability_evidence = {
            "adoption_case_id": "case_one", "human_review_path": str(human),
            "merge_review_path": str(review), "merge_result_path": str(merge),
            "fix_observation_path": str(observation),
        }
        advance_fix_lifecycle(self.fix.fix_id, STABLE, self.stability_evidence, out_root=self.lifecycle)

    def test_create_and_observe_are_idempotent_and_version_separated(self) -> None:
        self.assertEqual(self.fix_path, create_fix_lifecycle(self.fix, registry=self.registry, out_root=self.lifecycle))
        self.assertEqual(load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)["state"], NEW)
        self._observed(unavailable=False)
        record = load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)
        self.assertEqual(record["history"][-1]["evidence"]["call_count"], 10)
        self.assertEqual(record["state"], OBSERVED)
        evidence = {"usage_snapshot_paths": [str(self.root / "metrics/producer.json")], "usage_unavailable": False}
        before = self.fix_path.read_bytes()
        advance_fix_lifecycle(self.fix.fix_id, OBSERVED, evidence, out_root=self.lifecycle)
        self.assertEqual(self.fix_path.read_bytes(), before)

    def test_invalid_transition_and_missing_usage_are_rejected(self) -> None:
        with self.assertRaises(FixLifecycleError):
            advance_fix_lifecycle(self.fix.fix_id, STABLE, {}, out_root=self.lifecycle)
        with self.assertRaises(FixLifecycleError):
            advance_fix_lifecycle(self.fix.fix_id, OBSERVED, {"usage_snapshot_paths": []}, out_root=self.lifecycle)
        self.assertEqual(load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)["state"], NEW)

    def test_validation_requires_all_cases_and_before_after_proofs(self) -> None:
        self._observed()
        evidence = self._validation_evidence()
        replay = Path(evidence["patch_replay_paths"]["case_one"])
        data = json.loads(replay.read_text(encoding="utf-8"))
        data["before_signal"] = None
        replay.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(FixLifecycleError):
            advance_fix_lifecycle(self.fix.fix_id, VALIDATED, evidence, out_root=self.lifecycle)
        data["before_signal"] = {"replay_verdict": "REPRODUIT"}
        replay.write_text(json.dumps(data), encoding="utf-8")
        advance_fix_lifecycle(self.fix.fix_id, VALIDATED, evidence, out_root=self.lifecycle)
        self.assertEqual(load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)["state"], VALIDATED)

    def test_stability_needs_adoption_and_real_observation_fields(self) -> None:
        self._stable()
        record = load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)
        self.assertEqual(record["state"], STABLE)
        self.assertEqual(record["history"][-1]["evidence"]["activation_count"], 10)
        self.assertNotIn("symptom", json.dumps(record))

    def test_action_validation_requires_real_dispatch_before_signal(self) -> None:
        action_fix = self._register("action_fix", ("case_one",), stage="action")
        create_fix_lifecycle(action_fix, registry=self.registry, out_root=self.lifecycle)
        advance_fix_lifecycle(action_fix.fix_id, OBSERVED,
                              {"usage_snapshot_paths": [], "usage_unavailable": True},
                              out_root=self.lifecycle)
        evidence = self._validation_evidence()
        diagnosis_path = Path(evidence["diagnosis_paths"]["case_one"])
        replay_path = Path(evidence["patch_replay_paths"]["case_one"])
        diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
        diagnosis["stage"] = "action"
        diagnosis["real_dispatch_replay"] = {"validation_comparison": {"outcome": "BUG_PERSISTANT"}}
        diagnosis_path.write_text(json.dumps(diagnosis), encoding="utf-8")
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        replay["stage"] = "action"
        replay_path.write_text(json.dumps(replay), encoding="utf-8")
        with self.assertRaises(FixLifecycleError):
            advance_fix_lifecycle(action_fix.fix_id, VALIDATED, evidence, out_root=self.lifecycle)
        replay["before_signal"] = {"real_dispatch_outcome": "BUG_PERSISTANT"}
        replay_path.write_text(json.dumps(replay), encoding="utf-8")
        advance_fix_lifecycle(action_fix.fix_id, VALIDATED, evidence, out_root=self.lifecycle)
        self.assertEqual(load_fix_lifecycle(action_fix.fix_id, out_root=self.lifecycle)["state"], VALIDATED)

    def test_candidate_is_review_only_and_overlap_blocks_joint_readiness(self) -> None:
        self._stable()
        other = self._register("other_fix", ("case_two",))
        create_fix_lifecycle(other, registry=self.registry, out_root=self.lifecycle)
        note = self.root / "rule_review.md"
        note.write_text("Reviewed common rule", encoding="utf-8")
        path = prepare_evolution_candidate(
            [self.fix.fix_id, other.fix_id], reason="multiple_fixes", rule_review_path=note,
            lifecycle_root=self.lifecycle, out_root=self.candidates,
        )
        dossier = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(dossier["review_status"], "BLOCKED")
        self.assertIn("compatibility_unresolved", dossier["blockers"])
        self.assertIn("fix_not_validated", dossier["blockers"])
        self.assertEqual(path, prepare_evolution_candidate(
            [other.fix_id, self.fix.fix_id], reason="multiple_fixes", rule_review_path=note,
            lifecycle_root=self.lifecycle, out_root=self.candidates,
        ))
        with self.assertRaises(FixLifecycleError):
            prepare_evolution_candidate(
                [other.fix_id, self.fix.fix_id], reason="multiple_fixes", rule_review_path=note,
                compatibility={"other_fix::sample_fix": "DISJOINT"},
                lifecycle_root=self.lifecycle, out_root=self.candidates,
            )
        with self.assertRaises(FixLifecycleError):
            advance_fix_lifecycle(self.fix.fix_id, EVOLUTION_CANDIDATE,
                                  {"candidate_path": str(path)}, out_root=self.lifecycle)
        ready = prepare_evolution_candidate(
            [self.fix.fix_id], reason="activations", rule_review_path=note,
            lifecycle_root=self.lifecycle, out_root=self.candidates,
        )
        self.assertEqual(json.loads(ready.read_text(encoding="utf-8"))["review_status"],
                         "READY_FOR_HUMAN_ANALYSIS")
        advance_fix_lifecycle(self.fix.fix_id, EVOLUTION_CANDIDATE,
                              {"candidate_path": str(ready)}, out_root=self.lifecycle)
        self.assertEqual(load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)["state"], EVOLUTION_CANDIDATE)

    def test_core_change_signal_is_distinct_from_lifecycle_state(self) -> None:
        note = self.root / "core_review.md"
        note.write_text("External fix would duplicate the core", encoding="utf-8")
        before = load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)
        mark_core_change_candidate(self.fix.fix_id, reason="duplicated_core_logic",
                                   evidence_path=note, out_root=self.lifecycle)
        record = load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)
        self.assertEqual(record["state"], before["state"])
        self.assertEqual(record["signals"][0]["code"], "CORE_CHANGE_CANDIDATE")
        self.assertNotIn("External fix", json.dumps(record))
        self.assertNotIn("div.synthetic", json.dumps(record))
        mark_core_change_candidate(self.fix.fix_id, reason="duplicated_core_logic",
                                   evidence_path=note, out_root=self.lifecycle)
        self.assertEqual(len(load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)["signals"]), 1)

    def test_candidate_binds_exact_lifecycle_snapshot(self) -> None:
        self._stable()
        note = self.root / "rule_review.md"
        note.write_text("Reviewed common rule", encoding="utf-8")
        old = prepare_evolution_candidate(
            [self.fix.fix_id], reason="activations", rule_review_path=note,
            lifecycle_root=self.lifecycle, out_root=self.candidates,
        )
        signal = self.root / "signal.md"
        signal.write_text("Core review", encoding="utf-8")
        mark_core_change_candidate(self.fix.fix_id, reason="duplicated_core_logic",
                                   evidence_path=signal, out_root=self.lifecycle)
        with self.assertRaises(FixLifecycleError):
            advance_fix_lifecycle(self.fix.fix_id, EVOLUTION_CANDIDATE,
                                  {"candidate_path": str(old)}, out_root=self.lifecycle)
        new = prepare_evolution_candidate(
            [self.fix.fix_id], reason="activations", rule_review_path=note,
            lifecycle_root=self.lifecycle, out_root=self.candidates,
        )
        self.assertNotEqual(old, new)
        self.assertEqual(json.loads(new.read_text(encoding="utf-8"))["fixes"][0]
                         ["core_change_signals"][0]["code"], "CORE_CHANGE_CANDIDATE")

    def test_new_incident_reopens_stable_and_requires_new_validation(self) -> None:
        self._stable()
        new_case = self._json("diagnoses/new_case.json", {
            "case_id": "new_case", "stage": "extraction", "confidence_global": "plausible",
        })
        advance_fix_lifecycle(self.fix.fix_id, OBSERVED, {
            "new_case_id": "new_case", "attribution": "suspected", "diagnosis_path": str(new_case),
        }, out_root=self.lifecycle)
        record = load_fix_lifecycle(self.fix.fix_id, out_root=self.lifecycle)
        self.assertEqual(record["linked_cases"], ["case_one", "new_case"])
        with self.assertRaises(FixLifecycleError):
            advance_fix_lifecycle(self.fix.fix_id, VALIDATED, self._validation_evidence(), out_root=self.lifecycle)


if __name__ == "__main__":
    unittest.main()
