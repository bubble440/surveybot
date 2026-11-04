from __future__ import annotations

"""Tests synthétiques de l'oracle d'action BDL-3 (observation seule, sans navigateur).

Les faits « après » reproduisent la forme des traces réelles (clé
"target:<id>:option:<libellé>" avec `checked`, "target:<id>" avec `value`).
"""

import unittest

from Survey.action_oracle import (
    CONFIRMED, CONFIRMED_FAILURE, FALSE_NEGATIVE, FALSE_POSITIVE, INCONCLUSIVE,
    evaluate_action_trace,
)
from Survey.browser_capsule import build_action_trace


def _radio_facts(checked_label: str) -> dict:
    labels = ["moins de 2 heures", "plus de 6 heures"]
    return {f"target:g1:option:{l}": {"checked": l == checked_label, "value": "x"} for l in labels}


def _trace(entry: dict, facts) -> dict:
    return {
        "requested_actions": [entry],
        "dispatcher_success": entry.get("dispatcher_action_success"),
        "after": None if facts is None else {"facts": facts},
    }


def _radio(claimed: bool, value="Plus de 6 heures", **extra) -> dict:
    return {"index": 0, "target_id": "g1", "itype": "radio", "value": value,
            "requested_value": value, "dispatcher_action_success": claimed, **extra}


class ActionOracleTests(unittest.TestCase):
    def _verdict(self, entry, facts):
        return evaluate_action_trace(_trace(entry, facts))["actions"][0]

    def test_radio_confirmed_matches_real_trace_shape(self) -> None:
        r = self._verdict(_radio(True), _radio_facts("plus de 6 heures"))
        self.assertEqual((r["verdict"], r["observed"]), (CONFIRMED, "applied"))

    def test_radio_false_positive_when_claimed_success_but_not_checked(self) -> None:
        r = self._verdict(_radio(True), _radio_facts("moins de 2 heures"))
        self.assertEqual(r["verdict"], FALSE_POSITIVE)

    def test_radio_false_negative_when_claimed_failure_but_checked(self) -> None:
        r = self._verdict(_radio(False), _radio_facts("plus de 6 heures"))
        self.assertEqual(r["verdict"], FALSE_NEGATIVE)

    def test_radio_confirmed_failure(self) -> None:
        r = self._verdict(_radio(False), _radio_facts("moins de 2 heures"))
        self.assertEqual(r["verdict"], CONFIRMED_FAILURE)

    def test_label_match_ignores_case_spaces_and_unicode_form(self) -> None:
        facts = {"target:g1:option:é  test": {"checked": True}}
        r = self._verdict(_radio(True, value="E\u0301 TEST"), facts)
        self.assertEqual(r["verdict"], CONFIRMED)

    def test_requested_value_used_when_prepared_value_does_not_match(self) -> None:
        entry = _radio(True, value="valeur transformée")
        entry["requested_value"] = "Plus de 6 heures"
        self.assertEqual(self._verdict(entry, _radio_facts("plus de 6 heures"))["verdict"], CONFIRMED)

    def test_no_matching_option_abstains(self) -> None:
        r = self._verdict(_radio(True, value="inconnue"), _radio_facts("plus de 6 heures"))
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, "no_matching_option"))

    def test_missing_checked_state_abstains(self) -> None:
        facts = {"target:g1:option:plus de 6 heures": {"checked": None, "ariaChecked": "true"}}
        r = self._verdict(_radio(True), facts)
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, "no_checked_state"))

    def test_text_cases(self) -> None:
        def text(claimed, after_value):
            entry = {"index": 0, "target_id": "t1", "itype": "text", "value": "75001",
                     "requested_value": "75001", "dispatcher_action_success": claimed}
            return self._verdict(entry, {"target:t1": {"value": after_value}})
        self.assertEqual(text(True, "75001")["verdict"], CONFIRMED)
        self.assertEqual(text(True, "")["verdict"], FALSE_POSITIVE)
        self.assertEqual(text(False, "75001")["verdict"], FALSE_NEGATIVE)
        # Valeur différente non vide (masque, formatage) : on s'abstient.
        r = text(True, "75 001")
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, "value_differs_possible_formatting"))

    def test_unsupported_itype_and_missing_after_state_abstain(self) -> None:
        dropdown = {"index": 0, "target_id": "d1", "itype": "dropdown", "value": "A",
                    "dispatcher_action_success": True}
        self.assertEqual(self._verdict(dropdown, {})["reason"], "unsupported_itype")
        self.assertEqual(self._verdict(_radio(True), None)["reason"], "no_after_state")

    def test_legacy_trace_without_per_action_results_returns_none(self) -> None:
        legacy = {"requested_actions": [{"target_id": "g1", "itype": "radio", "value": "A"}]}
        self.assertIsNone(evaluate_action_trace(legacy))
        self.assertIsNone(evaluate_action_trace(None))
        self.assertIsNone(evaluate_action_trace({"requested_actions": "x"}))

    def test_summary_counts_verdicts_over_several_actions(self) -> None:
        trace = _trace(_radio(True), _radio_facts("plus de 6 heures"))
        trace["requested_actions"].append(_radio(True, value="inconnue", index=1))
        out = evaluate_action_trace(trace)
        self.assertEqual(out["summary"], {CONFIRMED: 1, INCONCLUSIVE: 1})

    def test_build_action_trace_embeds_oracle_only_with_action_results(self) -> None:
        kwargs = dict(actions=[], dispatcher_success=True, report={"ok": True, "issues": []},
                      before_state=None, after_state={"facts": _radio_facts("plus de 6 heures")},
                      mutations=None)
        with_results = build_action_trace(action_results=[_radio(True)], **kwargs)
        self.assertEqual(with_results["oracle"]["actions"][0]["verdict"], CONFIRMED)
        self.assertNotIn("oracle", build_action_trace(action_results=None, **kwargs))


if __name__ == "__main__":
    unittest.main()
