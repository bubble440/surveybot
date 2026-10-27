"""Tests unitaires autonomes de l'outil d'inventaire BDL-1."""

import json
import tempfile
import unittest
from pathlib import Path

from tools.bdl1_inventory import build_report


def block(itype, target_id="target-1", options=None, **extra):
    value = {
        "itype": itype,
        "target_id": target_id,
        "question": "Question de test",
        "options": [] if options is None else options,
    }
    value.update(extra)
    return value


class BDL1InventoryTests(unittest.TestCase):
    def _run_report(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "question_blocks.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return build_report([path])

    def test_inventory_counts_known_types(self):
        payload = [
            block("radio", options=["Oui", "Non"], max_select=1),
            block("checkbox", options=["A", "B"], max_select=2),
            block("text"),
            block("textarea"),
            block("number"),
            block("matrix", options=["Colonne 1", "Colonne 2"]),
            block("dropdown", options=["A", "B"]),
            block("select", options=[]),
            block("select_rps", options=["A", "B"]),
            block("button"),
        ]
        report = self._run_report(payload)
        self.assertEqual(report["blocks_scanned"], 10)
        self.assertEqual(len(report["itype_counts"]), 10)
        self.assertNotIn("undocumented_itype", {i["code"] for i in report["issues"]})

    def test_unknown_type_is_reported(self):
        report = self._run_report([block("new_future_type")])
        self.assertIn("undocumented_itype", {i["code"] for i in report["issues"]})

    def test_missing_target_id_is_reported(self):
        report = self._run_report([block("text", target_id="")])
        self.assertIn("missing_target_id", {i["code"] for i in report["issues"]})

    def test_checkbox_limit_over_options_is_reported(self):
        report = self._run_report([
            block("checkbox", options=["A", "B"], max_select=3)
        ])
        self.assertIn("max_select_exceeds_options", {i["code"] for i in report["issues"]})

    def test_select_with_empty_options_is_not_assumed_invalid(self):
        report = self._run_report([block("select", options=[])])
        codes = {i["code"] for i in report["issues"]}
        self.assertNotIn("choice_without_options", codes)

    def test_invalid_min_max_is_reported(self):
        report = self._run_report([
            block("checkbox", options=["A", "B"], min_select=3, max_select=2)
        ])
        self.assertIn("invalid_selection_limits", {i["code"] for i in report["issues"]})


if __name__ == "__main__":
    unittest.main()
