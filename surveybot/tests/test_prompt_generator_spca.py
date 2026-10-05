from __future__ import annotations

import json
import re
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
        self.assertIn("une seule évaluation qui retourne toutes les données utiles", result.content)
        self.assertIn("jamais un champ de saisie libre associé à son libellé", result.content)
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
        self.assertIn("Termine ta réponse finale par une dernière ligne unique « RÉSUMÉ : <conclusion> »", result.content)
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
        self.assertIn("Ne lance aucune exécution de code ni commande shell", result.content)
        self.assertIn("Ne crée aucun fichier temporaire", result.content)
        self.assertIn("tests/test_<nom_du_module>.py", result.content)
        self.assertIn("données DOM synthétiques", result.content)
        self.assertIn("Le worker local/dev diagnostique", result.content)
        self.assertIn("liste explicite de Survey/external_fix_loader.py", result.content)
        self.assertIn("Titre de commit suggéré", result.content)
        self.assertIn("Termine ta réponse finale par une dernière ligne unique « RÉSUMÉ : <conclusion> »", result.content)

    def test_action_prompt_says_an_unconfirmed_hypothesis_is_not_a_reason_to_abstain(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_action_hypothesis", stage="action")
        result = generate_prompt(diag_dir, selection_dir)

        self.assertTrue(result.eligible)
        for fragment in (
            "n'est pas un motif d'abstention",
            "jamais à un faux succès",
            "est classé non concluant et ouvre une validation live",
            "ce n'est pas un rejet",
            "décline (`DECLINED`, sans effet)",
            "jamais un état d'après-geste",
            "Ne t'abstiens que si aucune preuve de succès indépendante ne peut être codée",
            "candidat de changement du cœur",
            "toute hypothèse sur laquelle repose le correctif",
        ):
            self.assertIn(fragment, result.content)
        # Les règles existantes ne sont ni affaiblies ni contredites.
        self.assertIn("sans modifier cette baseline", result.content)
        self.assertIn("ne déclare pas un succès non vérifié", result.content)
        self.assertIn("sans fallback ni second clic", result.content)

    def test_action_stage_guidance_addition_is_short_generic_and_after_the_existing_text(self) -> None:
        from Survey.autofix.prompt_generator import _STAGE_GUIDANCE

        guidance = _STAGE_GUIDANCE["action"]
        existing = (
            "Respecte CTA_INTERCEPT_ONLY et vérifie l'état réel de l'action. "
        )
        head, _, addition = guidance.partition(existing)
        self.assertTrue(head.startswith("Stage action : seul un correctif `before` est admissible"))
        self.assertTrue(addition)
        sentences = [part for part in re.split(r"(?<=[.!?])\s+", addition.strip()) if part]
        self.assertLessEqual(len(sentences), 5)
        lowered = addition.lower()
        for forbidden in (
            "metrixlab", "toluna", "qualtrics", "radioqt", "checkboxqt", "q1001",
            "group_", "input_on", "option_radio", "20261", "provider",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_action_hypothesis_guidance_does_not_leak_into_other_stages(self) -> None:
        extraction_dir, extraction_sel = self._case("synthetic_extraction_hypothesis", stage="extraction")
        unknown_dir, unknown_sel = self._case("synthetic_unknown_hypothesis", stage="unknown")
        extraction = generate_prompt(extraction_dir, extraction_sel)
        unknown = generate_prompt(unknown_dir, unknown_sel)
        for result in (extraction, unknown):
            self.assertNotIn("n'est pas un motif d'abstention", result.content)
            self.assertNotIn("ouvre une validation live", result.content)
            self.assertNotIn("Ne t'abstiens que si", result.content)

    def test_action_dispatcher_steps_are_optional_bounded_facts_in_prompt(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_dispatch_steps", stage="action")
        original = generate_prompt(diag_dir, selection_dir).content
        path = diag_dir / "diagnosis.json"
        diagnosis = json.loads(path.read_text(encoding="utf-8"))
        diagnosis["real_dispatch_replay"] = {"status": "FAILURE"}
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, original)

        diagnosis["real_dispatch_replay"]["dispatcher_steps"] = (
            ["strategy=target_id verification=failed", "apply ok=false strategy=none reason=no_strategy"]
            + ["secret question/answer/label https://private.example", "strategy=" + "x" * 200 + " result=failed"]
            + ["strategy=radio_main result=failed"] * 30
        )
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        enriched = generate_prompt(diag_dir, selection_dir).content
        self.assertIn("Étapes techniques observées lors de la réexécution réelle du dispatcher", enriched)
        self.assertIn("- strategy=target_id verification=failed", enriched)
        self.assertIn("- apply ok=false strategy=none reason=no_strategy", enriched)
        self.assertEqual(enriched.count("- strategy=radio_main result=failed"), 20)
        self.assertNotIn("secret question/answer/label", enriched)
        self.assertNotIn("private.example", enriched)

    def test_action_prompt_accepts_only_closed_click_failure_reasons(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_click_failure_reasons", stage="action")
        path = diag_dir / "diagnosis.json"
        diagnosis = json.loads(path.read_text(encoding="utf-8"))
        valid = [
            "click=native_failed", "click=hover_failed",
            "click=native_failed reason=not_visible",
            "click=hover_failed reason=intercepted",
            "click=native_failed reason=not_enabled",
            "click=hover_failed reason=unstable",
            "click=native_failed reason=detached",
        ]
        invalid = [
            "click=native_failed reason=unknown",
            "click=hover_failed reason=detached secret answer",
            "click=native_failed reason=not_visible\nsecret answer",
            "click=other_failed reason=detached",
            "click=native_failed reason=detached_more",
        ]
        diagnosis["real_dispatch_replay"] = {"dispatcher_steps": valid + invalid}
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        content = generate_prompt(diag_dir, selection_dir).content
        for step in valid:
            self.assertIn(f"- {step}\n", content)
        for step in invalid:
            self.assertNotIn(f"- {step}\n", content)
        self.assertNotIn("secret answer", content)

    def test_action_target_shapes_enrich_only_action_prompt(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_action_shapes", stage="action")
        original = generate_prompt(diag_dir, selection_dir).content
        path = diag_dir / "diagnosis.json"
        diagnosis = json.loads(path.read_text(encoding="utf-8"))
        diagnosis["real_dispatch_replay"] = {"target_shapes": [{
            "kind": "group", "itype": "radio", "frame_chain_present": True,
            "frame_depth": 1, "options_count": 2, "group_key_shape": "radio:name:dom:<GROUP>",
            "locator_shapes": [{"shape": "//*[@id=<LITERAL>]/ancestor::*[@class=' answer_options ']",
                                "count": 2}],
        }]}
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        enriched = generate_prompt(diag_dir, selection_dir).content
        self.assertIn("Forme des cibles observée dans le registre", enriched)
        self.assertIn("groupe=radio:name:dom:<GROUP>", enriched)
        self.assertIn("localisateur (2 occurrence(s))", enriched)
        self.assertIn("answer_options", enriched)
        self.assertIn("Les tests doivent reproduire la forme réelle des cibles", enriched)
        self.assertIn("signale toute hypothèse sur cette forme", enriched)
        self.assertNotIn("Forme des cibles observée dans le registre", original)
        self.assertNotIn("Les tests doivent reproduire la forme réelle des cibles", original)

        diagnosis["real_dispatch_replay"]["target_shapes"][0]["locator_shapes"][0]["shape"] = (
            '//*[@id="private_id"]'
        )
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, original)

        diagnosis["stage"] = "extraction"
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        extraction_with_key = generate_prompt(diag_dir, selection_dir).content
        diagnosis["real_dispatch_replay"].pop("target_shapes")
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, extraction_with_key)

    def test_requested_option_dom_facts_enrich_only_action_prompt(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_action_dom_facts", stage="action")
        original = generate_prompt(diag_dir, selection_dir).content
        path = diag_dir / "diagnosis.json"
        diagnosis = json.loads(path.read_text(encoding="utf-8"))
        diagnosis["real_dispatch_replay"] = {"requested_option_dom_facts": [{
            "element": {"tag": "input", "input_type": "radio", "classes": ["input_radioQT<N>"],
                        "visible": False, "width": 0, "height": 0},
            "siblings": [
                {"tag": "span", "input_type": None, "classes": ["option_radio"],
                 "visible": True, "width": 16, "height": 16},
                {"tag": "span", "input_type": None, "classes": ["option_label", "input_label_on"],
                 "visible": True, "width": 120, "height": 20},
            ],
        }]}
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        enriched = generate_prompt(diag_dir, selection_dir).content
        self.assertIn("Faits DOM mesurés pour l'élément de l'option demandée", enriched)
        self.assertIn("document figé rejoué sans scripts de la page", enriched)
        self.assertIn("ne disent rien d'un effet produit par le JavaScript du site", enriched)
        self.assertIn(
            "- option demandée : balise=input, type=radio, classes=input_radioQT<N>, "
            "visible=False, taille=0x0", enriched,
        )
        self.assertIn(
            "  - frère : balise=span, classes=option_radio, visible=True, taille=16x16", enriched,
        )
        self.assertNotIn("Faits DOM mesurés", original)

        # Clé présente mais forme inattendue (type invalide) : prompt strictement identique.
        diagnosis["real_dispatch_replay"]["requested_option_dom_facts"][0]["element"]["width"] = "zero"
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, original)

        # Un autre stage que action n'affiche jamais ce bloc, clé présente ou non.
        diagnosis["real_dispatch_replay"]["requested_option_dom_facts"][0]["element"]["width"] = 0
        diagnosis["stage"] = "extraction"
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        extraction_with_key = generate_prompt(diag_dir, selection_dir).content
        diagnosis["real_dispatch_replay"].pop("requested_option_dom_facts")
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, extraction_with_key)

    def test_dispatcher_steps_do_not_change_extraction_prompt(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_extraction_dispatch_steps")
        original = generate_prompt(diag_dir, selection_dir).content
        path = diag_dir / "diagnosis.json"
        diagnosis = json.loads(path.read_text(encoding="utf-8"))
        diagnosis["real_dispatch_replay"] = {"dispatcher_steps": [
            "strategy=target_id verification=failed",
            "action_fix selected fix_id=fix_radio_qt",
            "action_fix verdict=HANDLED_FAILURE",
        ]}
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, original)

    def test_action_fix_decision_steps_are_closed_facts_in_action_prompt(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_action_fix_decision", stage="action")
        original = generate_prompt(diag_dir, selection_dir).content
        path = diag_dir / "diagnosis.json"
        diagnosis = json.loads(path.read_text(encoding="utf-8"))
        diagnosis["real_dispatch_replay"] = {"dispatcher_steps": [
            "action_fix selected fix_id=fix_radio_qt",
            "action_fix verdict=HANDLED_FAILURE",
            "action_fix verdict=HANDLED_FAILURE reason=handler_returned_failure",
            "action_fix verdict=HANDLED_FAILURE reason=handler_exception",
            "action_fix verdict=HANDLED_FAILURE reason=invalid_result",
            "action_fix verdict=HANDLED_FAILURE reason=post_handler_timeout",
            "action_fix selected fix_id=bad-id",
            "action_fix verdict=UNKNOWN",
            "action_fix verdict=DECLINED value=secret_answer",
            "action_fix verdict=HANDLED_FAILURE reason=unknown",
            "action_fix verdict=HANDLED_SUCCESS reason=handler_returned_failure",
            "action_fix verdict=HANDLED_FAILURE reason=handler_returned_failure value=secret_answer",
        ]}
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        enriched = generate_prompt(diag_dir, selection_dir).content
        self.assertIn("- action_fix selected fix_id=fix_radio_qt", enriched)
        self.assertIn("- action_fix verdict=HANDLED_FAILURE", enriched)
        for reason in ("handler_returned_failure", "handler_exception", "invalid_result", "post_handler_timeout"):
            self.assertIn(f"- action_fix verdict=HANDLED_FAILURE reason={reason}", enriched)
        self.assertNotIn("bad-id", enriched)
        self.assertNotIn("UNKNOWN", enriched)
        self.assertNotIn("secret_answer", enriched)
        self.assertNotIn("reason=unknown", enriched)
        self.assertNotIn("HANDLED_SUCCESS reason=", enriched)

        diagnosis["real_dispatch_replay"].pop("dispatcher_steps")
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, original)

    def test_action_script_mode_fact_is_optional_and_stage_scoped(self) -> None:
        diag_dir, selection_dir = self._case("synthetic_action_script_mode", stage="action")
        original = generate_prompt(diag_dir, selection_dir).content
        path = diag_dir / "diagnosis.json"
        diagnosis = json.loads(path.read_text(encoding="utf-8"))

        diagnosis["real_dispatch_replay"] = {"status": "FAILURE"}
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, original)

        diagnosis["real_dispatch_replay"]["execute_scripts"] = False
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        enriched = generate_prompt(diag_dir, selection_dir).content
        replay_fact = (
            "L'incident d'origine a été capturé par le bot en exécution réelle, avec les scripts "
            "de la page actifs. Seule la réexécution de diagnostic charge le document figé sans "
            "scripts : elle ne peut reproduire ni confirmer un état créé par le JavaScript du site. "
            "Cette limite du rejeu n'explique pas l'incident d'origine et ne prouve pas que "
            "l'échec rapporté était attendu."
        )
        self.assertEqual(
            enriched,
            original.replace(
                "\n\nFichiers probablement concernés :",
                f"\n\n{replay_fact}\n\nFichiers probablement concernés :",
                1,
            ),
        )

        diagnosis["real_dispatch_replay"]["execute_scripts"] = True
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, original)

        diagnosis["stage"] = "extraction"
        diagnosis["real_dispatch_replay"]["execute_scripts"] = False
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        extraction_with_key = generate_prompt(diag_dir, selection_dir).content
        diagnosis["real_dispatch_replay"].pop("execute_scripts")
        path.write_text(json.dumps(diagnosis), encoding="utf-8")
        self.assertEqual(generate_prompt(diag_dir, selection_dir).content, extraction_with_key)

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