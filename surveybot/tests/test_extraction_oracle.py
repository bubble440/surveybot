from __future__ import annotations

"""Tests synthétiques de l'oracle d'extraction BDL-4 (étape 3, sans navigateur)."""

import unittest

from Survey.extraction_oracle import (
    BUG_CONFIRMED, INCONCLUSIVE, PASS, SUSPECTED, evaluate_extraction_pass,
)


def _block(itype="radio", options=("A", "B"), **extra) -> dict:
    b = {"itype": itype, "question": "Q", "target_id": "t1", "options": list(options),
         "options_count": len(options), "min_select": 0, "max_select": 0}
    b.update(extra)
    return b


def _one(block: dict) -> dict:
    return evaluate_extraction_pass({"blocks": [block]})["blocks"][0]


class ExtractionOracleTests(unittest.TestCase):
    def test_healthy_radio_passes(self) -> None:
        self.assertEqual(_one(_block())["verdict"], PASS)

    def test_empty_options_radio_and_checkbox_confirmed(self) -> None:
        for itype in ("radio", "checkbox"):
            r = _one(_block(itype, options=()))
            self.assertEqual((r["verdict"], r["signals"]), (BUG_CONFIRMED, ["choice_without_options"]))

    def test_dropdown_empty_options_abstains(self) -> None:
        r = _one(_block("dropdown", options=()))
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, "unsupported_itype"))

    def test_non_serializable_options_not_flagged_empty(self) -> None:
        # Collecte : options non scalaires écartées -> liste vide mais options_count > 0.
        r = _one(_block("radio", options=(), options_count=3))
        self.assertNotEqual(r["verdict"], BUG_CONFIRMED)

    def test_min_greater_than_max_confirmed(self) -> None:
        r = _one(_block("checkbox", options=("A", "B", "C"), min_select=3, max_select=2))
        self.assertEqual(r["verdict"], BUG_CONFIRMED)
        self.assertIn("invalid_selection_limits", r["signals"])

    def test_checkbox_max_exceeds_options_confirmed(self) -> None:
        r = _one(_block("checkbox", options=("A", "B"), max_select=5))
        self.assertEqual((r["verdict"], r["signals"]), (BUG_CONFIRMED, ["max_select_exceeds_options"]))

    def test_checkbox_max_exceeds_abstained_when_options_truncated(self) -> None:
        r = _one(_block("checkbox", options=("A", "B"), options_count=150, max_select=120))
        self.assertEqual(r["verdict"], PASS)

    def test_radio_max_select_gt_1_suspected(self) -> None:
        self.assertEqual(_one(_block("radio", max_select=2))["verdict"], SUSPECTED)

    def test_duplicate_options_suspected_after_normalization(self) -> None:
        r = _one(_block("radio", options=("Oui", " oui ")))
        self.assertEqual((r["verdict"], r["signals"]), (SUSPECTED, ["duplicate_options"]))

    def test_text_with_options_suspected_and_clean_text_passes(self) -> None:
        self.assertEqual(_one(_block("text", options=("A",)))["verdict"], SUSPECTED)
        self.assertEqual(_one(_block("textarea", options=()))["verdict"], PASS)

    def test_strongest_verdict_wins(self) -> None:
        r = _one(_block("radio", options=("A", "a"), min_select=3, max_select=2))
        self.assertEqual(r["verdict"], BUG_CONFIRMED)
        self.assertEqual(set(r["signals"]), {"invalid_selection_limits", "duplicate_options", "radio_max_select_gt_1"})

    def test_undocumented_and_missing_itype_abstain(self) -> None:
        self.assertEqual(_one(_block("hologram"))["reason"], "undocumented_itype")
        self.assertEqual(_one(_block(""))["reason"], "missing_itype")

    def test_matrix_and_button_abstain(self) -> None:
        for itype in ("matrix", "button", "select", "select_rps"):
            self.assertEqual(_one(_block(itype, options=()))["verdict"], INCONCLUSIVE)

    def test_shared_group_key_abstains(self) -> None:
        ctx = {"group_key": "slide:1"}
        out = evaluate_extraction_pass({"blocks": [_block(options=(), context=ctx), _block(context=ctx)]})
        self.assertEqual([b["reason"] for b in out["blocks"]], ["shared_group_key"] * 2)

    def test_summary_and_malformed_payloads_never_raise(self) -> None:
        out = evaluate_extraction_pass({"blocks": [_block(), _block(options=()), "junk", {"itype": None}]})
        self.assertEqual(out["summary"], {PASS: 1, BUG_CONFIRMED: 1, INCONCLUSIVE: 1})
        self.assertIsNone(evaluate_extraction_pass(None))
        self.assertIsNone(evaluate_extraction_pass({"blocks": "x"}))
        self.assertEqual(evaluate_extraction_pass({"blocks": []})["summary"], {})


if __name__ == "__main__":
    unittest.main()