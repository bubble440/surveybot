from __future__ import annotations

"""Tests synthétiques BDL-3 : observation par action dans action_trace.

Aucun navigateur, aucune saisie clavier. Vérifie le contrat de
browser_capsule.build_action_trace : résultat par action, valeur demandée vs
valeur préparée pour le dispatcher, états avant/après et signaux du validator.

Ce fichier est le contrôle automatisé de BDL-3. Le contrôle sur un run réel IFOP
zip2city se fait avec tools/check_bdl3_trace.py (lecture seule des traces).
"""

import unittest

from Survey.browser_capsule import build_action_trace

_BEFORE = {"facts": {"t1": {"value": ""}}}
_AFTER = {"facts": {"t1": {"value": "Paris"}}}
_REPORT = {
    "ok": False,
    "issues": [{
        "failure_type": "dispatcher_false_negative",
        "dom_signal": "value_present",
        "observed": {"value": "Paris"},
    }],
}


class BdlActionTraceTests(unittest.TestCase):
    def _trace(self, action_results):
        return build_action_trace(
            actions=[{"target_id": "t1", "itype": "text", "value": "75001", "qid": "q1"}],
            dispatcher_success=False,
            report=_REPORT,
            before_state=_BEFORE,
            after_state=_AFTER,
            mutations={"mutations": []},
            action_results=action_results,
        )

    def test_per_action_results_expose_requested_and_dispatched_values(self) -> None:
        trace = self._trace([
            {"index": 0, "target_id": "t1", "itype": "text", "qid": "q1",
             "requested_value": "75001", "dispatcher_value": "Paris",
             "dispatcher_action_success": False},
            {"index": 1, "target_id": "t2", "itype": "radio", "qid": "q2",
             "requested_value": "A", "dispatcher_value": "A",
             "dispatcher_action_success": True},
        ])
        first, second = trace["requested_actions"]
        self.assertEqual(first["requested_value"], "75001")
        self.assertEqual(first["value"], "Paris")  # valeur préparée pour le dispatcher
        self.assertFalse(first["dispatcher_action_success"])
        self.assertTrue(second["dispatcher_action_success"])  # statut par action, pas agrégé

    def test_before_after_and_validator_signals_are_kept(self) -> None:
        trace = self._trace([{"index": 0, "target_id": "t1", "itype": "text", "qid": "q1",
                              "requested_value": "75001", "dispatcher_value": "Paris",
                              "dispatcher_action_success": False}])
        self.assertEqual(trace["before"], _BEFORE)
        self.assertEqual(trace["after"], _AFTER)
        self.assertEqual(trace["validator_signals"][0]["failure_type"], "dispatcher_false_negative")
        self.assertFalse(trace["dispatcher_success"])

    def test_without_action_results_falls_back_to_raw_plan(self) -> None:
        for empty in (None, []):
            trace = self._trace(empty)
            entry = trace["requested_actions"][0]
            self.assertEqual(entry["value"], "75001")
            self.assertNotIn("requested_value", entry)


if __name__ == "__main__":
    unittest.main()
