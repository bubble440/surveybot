from __future__ import annotations

"""BDL-3 : le chemin PASS (collecte BDL-2b) doit conserver les observations par action.

Régression : seule la branche d'échec de page_snapshot transmettait
`action_results` à build_action_trace ; les traces *_action_pass retombaient sur
le plan brut (sans requested_value ni statut par action). Aucun navigateur.
"""

import unittest
from unittest.mock import Mock, patch

import Survey.page_snapshot as page_snapshot


class FakeDriver:
    url = "https://example.test/survey"
    _bdl_action_results = [{
        "index": 0, "target_id": "t1", "itype": "radio", "qid": "q1",
        "requested_value": "Un homme", "dispatcher_value": "Un homme",
        "dispatcher_action_success": True,
    }]


class BdlPassPathTests(unittest.TestCase):
    def test_collected_pass_trace_contains_per_action_results(self) -> None:
        recorder = Mock()
        actions = [{"target_id": "t1", "itype": "radio", "value": "Un homme", "qid": "q1"}]
        with patch("Survey.browser_capsule.capture_runtime_state", return_value={"facts": {}}), \
                patch("Survey.browser_capsule.collect_mutation_observer", return_value=None), \
                patch("Survey.failure_recorder.record_collected_action", recorder):
            page_snapshot._collect_action_pass(
                FakeDriver(), actions, True, {"ok": True, "issues": []}, {"facts": {}}
            )

        recorder.assert_called_once()
        entry = recorder.call_args.kwargs["action_trace"]["requested_actions"][0]
        self.assertEqual(entry["requested_value"], "Un homme")
        self.assertTrue(entry["dispatcher_action_success"])
        self.assertEqual(entry["index"], 0)


if __name__ == "__main__":
    unittest.main()
