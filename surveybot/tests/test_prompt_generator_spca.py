from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from Survey.autofix.prompt_generator import (
    MANUAL_REVIEW_FILENAME,
    PROMPT_FILENAME,
    PromptGenerationError,
    generate_prompt,
    write_prompt,
)


class PromptGeneratorSpcaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _case(
        self, case_id: str, *, stage: str = "extraction", confidence: str = "certain",
        code_files: list[str] | None = None,
    ) -> tuple[Path, Path]:
        diag_dir = self.root / "diagnoses" / case_id
        selection_dir = self.root / "context_selections" / case_id
        diag_dir.mkdir(parents=True)
        selection_dir.mkdir(parents=True)
        diagnosis = {
            "case_id": case_id,
            "stage": stage,
            "confidence_global": confidence,
            "itype": "radio",
            "provider_domain": "private.example",
            "snapshot_path": "private_snapshot.html",
            "symptom": {
                "failure_types": ["synthetic_failure"],
                "issues": [{
                    "itype": "radio", "value": "synthetic option", "question": "synthetic question",
                    "target_id": "internal_target_secret", "action_index": 1,
                }],
            },
            "expected_behavior": [{
                "failure_type": "synthetic_failure", "description": "synthetic expected behavior"
            }],
            "replay": {"verdict": "REPRODUIT", "replayed_failure_types": ["synthetic_failure"]},
        }
        selection = {"case_id": case_id, "code_files": [{"file": path} for path in (
            code_files if code_files is not None else ["Survey/synthetic_extractor.py"]
        )]}
        (diag_dir / "diagnosis.json").write_text(json.dumps(diagnosis), encoding="utf-8")
        (selection_dir / "context_selection.json").write_text(json.dumps(selection), encoding="utf-8")
        return diag_dir, selection_dir

    def test_extraction_prompt_uses_external_before_after_rules_and_case_metadata(self) -> None:
        case_id = "synthetic_extraction_case"
        diag_dir, selection_dir = self._case(case_id)
        before = (diag_dir / "diagnosis.json").read_bytes()
        result = generate_prompt(diag_dir, selection_dir)

        self.assertTrue(result.eligible)
        self.assertIn("DOM non couvert", result.content)
        self.assertIn("correctif `after`", result.content)
        self.assertIn("Faux positif", result.content)
        self.assertIn("correctif `before`", result.content)
        self.assertIn("liste explicite de Survey/external_fix_loader.py", result.content)
        self.assertIn("seul point de chargement à modifier", result.content)
        self.assertIn(f"case_id à rattacher au correctif : {case_id}", result.content)
        bug_section = result.content.split("BUG IDENTIFIÉ\n", 1)[1].split("\nRÈGLES STRICTES", 1)[0]
        self.assertNotIn(case_id, bug_section)
        self.assertNotIn("private.example", result.content)
        self.assertNotIn("private_snapshot.html", result.content)
        self.assertNotIn("internal_target_secret", result.content)
        self.assertNotIn("ordre additif", result.content)
        self.assertNotIn("demander une validation explicite", result.content)
        self.assertIn("Survey/BOT_EVOLUTION_MEMORY.md", result.content)
        self.assertIn("La Phase 11-A compare la baseline, base_sha et le patch", result.content)
        self.assertIn("ne crée pas toi-même la déclaration", result.content)
        self.assertEqual((diag_dir / "diagnosis.json").read_bytes(), before)

    def test_action_prompt_preserves_three_outcomes_cta_and_headless_boundary(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_action_case", stage="action", confidence="probable")
        result = generate_prompt(diag_dir, selection_dir)

        self.assertTrue(result.eligible)
        for word in ("`before`", "`action/after`", "`DECLINED`", "`HANDLED_SUCCESS`", "`HANDLED_FAILURE`"):
            self.assertIn(word, result.content)
        self.assertIn("sans fallback ni second clic", result.content)
        self.assertIn("CTA_INTERCEPT_ONLY", result.content)
        self.assertIn("sans modifier cette baseline", result.content)
        self.assertIn("ne pas poser de question interactive", result.content)
        self.assertIn("Le worker local/dev diagnostique", result.content)
        self.assertIn("liste explicite de Survey/external_fix_loader.py", result.content)
        self.assertIn("Titre de commit suggéré", result.content)

    def test_manual_review_gate_and_phase7_output_names_are_preserved(self) -> None:
        eligible_diag, eligible_selection = self._case("synthetic_eligible_case")
        prompt_path = write_prompt(eligible_diag, eligible_selection, out_root=self.root / "prompts")
        self.assertEqual(prompt_path.name, PROMPT_FILENAME)
        self.assertIn("BUG IDENTIFIÉ", prompt_path.read_text(encoding="utf-8"))

        for case_id, confidence, files in (
            ("synthetic_low_confidence", "plausible", ["Survey/synthetic_extractor.py"]),
            ("synthetic_no_code", "certain", []),
        ):
            with self.subTest(case_id=case_id):
                diag_dir, selection_dir = self._case(case_id, confidence=confidence, code_files=files)
                result = generate_prompt(diag_dir, selection_dir)
                self.assertFalse(result.eligible)
                output = write_prompt(diag_dir, selection_dir, out_root=self.root / "prompts")
                self.assertEqual(output.name, MANUAL_REVIEW_FILENAME)
                content = output.read_text(encoding="utf-8")
                self.assertIn("NE PAS TRANSMETTRE À CODEX", content)
                self.assertNotIn("VOIE DE CORRECTION POUR CE STAGE", content)

    def test_invalid_case_id_cannot_be_injected_into_an_eligible_prompt(self) -> None:
        diag_dir, selection_dir = self._case("bad@case")
        with self.assertRaisesRegex(PromptGenerationError, "case_id invalide"):
            generate_prompt(diag_dir, selection_dir)

    def test_mismatched_case_ids_cannot_be_attached_to_a_fix(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_consistent_case")
        selection_path = selection_dir / "context_selection.json"
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        selection["case_id"] = "another_case"
        selection_path.write_text(json.dumps(selection), encoding="utf-8")
        with self.assertRaisesRegex(PromptGenerationError, "case_id incohérent"):
            generate_prompt(diag_dir, selection_dir)

    def test_unknown_stage_does_not_choose_a_hook(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_unknown_stage", stage="unknown")
        result = generate_prompt(diag_dir, selection_dir)
        self.assertTrue(result.eligible)
        self.assertIn("Stage non identifié", result.content)
        self.assertIn("ne déclare pas un correctif actif sans preuve", result.content)


if __name__ == "__main__":
    unittest.main()
