"""Tests de l'outil de mesure BDL-4 étape 4 (corpus synthétique, sans navigateur)."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from Survey.extraction_oracle import evaluate_extraction_pass
from tools.bdl4_measure import build_report, cmd_template, read_annotations


def _block(itype="radio", options=("A", "B"), **extra):
    b = {"itype": itype, "question": "Q", "target_id": "t", "options": list(options),
         "options_count": len(options), "min_select": 0, "max_select": 0}
    b.update(extra)
    return b


def _write_page(root: Path, name: str, url: str, blocks: list) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    payload = {"url": url, "blocks": blocks, "blocks_total": len(blocks), "rule_events": []}
    payload["oracle"] = evaluate_extraction_pass(payload)
    (folder / "extraction_pass.json").write_text(json.dumps(payload), encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"timestamp": "2026-11-15T10:00:00", "url": url}), encoding="utf-8")


def _annotate(path: Path, truths: dict) -> None:
    """truths : {(folder, block_index): truth} ; '' pour une ligne page."""
    rows = read_annotations(path)
    for r in rows:
        key = (r["folder"], r["block_index"])
        if key in truths:
            r["truth"] = truths[key]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)


class Bdl4MeasureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "corpus"
        self.csv = Path(self._tmp.name) / "ann.csv"

    def test_template_has_block_and_page_rows(self):
        _write_page(self.root, "p1", "https://a.example/x", [_block(), _block("text", options=())])
        _write_page(self.root, "p2", "https://b.example/y", [])
        self.assertEqual(cmd_template(self.root, self.csv), 0)
        rows = read_annotations(self.csv)
        self.assertEqual(len(rows), 3 + 1)  # 2 blocs + ligne page ; 0 bloc + ligne page
        self.assertEqual(rows[-1]["question"], "(aucun bloc extrait)")

    def test_template_never_overwrites_annotations(self):
        _write_page(self.root, "p1", "https://a.example/x", [_block()])
        cmd_template(self.root, self.csv)
        _annotate(self.csv, {("p1", "0"): "OK"})
        _write_page(self.root, "p2", "https://b.example/y", [_block()])
        cmd_template(self.root, self.csv)
        rows = read_annotations(self.csv)
        self.assertEqual([r["truth"] for r in rows if r["folder"] == "p1" and r["block_index"] == "0"], ["OK"])
        self.assertTrue(any(r["folder"] == "p2" for r in rows))

    def test_c2_false_positive_is_ko(self):
        _write_page(self.root, "p1", "https://a.example/x", [_block(options=())])
        cmd_template(self.root, self.csv)
        _annotate(self.csv, {("p1", "0"): "OK"})
        rep = build_report(self.root, self.csv)
        self.assertEqual(rep["C2"]["status"], "KO")
        self.assertEqual(rep["C3"]["signals"]["choice_without_options"]["decision"], "CORRIGER_RÈGLE")

    def test_c2_true_positive_ok_and_unreviewed_incomplete(self):
        _write_page(self.root, "p1", "https://a.example/x", [_block(options=()), _block("text")])
        cmd_template(self.root, self.csv)
        _annotate(self.csv, {("p1", "1"): "OK"})  # page annotée, bloc BUG non revu
        self.assertEqual(build_report(self.root, self.csv)["C2"]["status"], "INCOMPLET")
        _annotate(self.csv, {("p1", "0"): "BAD"})
        self.assertEqual(build_report(self.root, self.csv)["C2"]["status"], "OK")

    def test_c3_signal_decisions(self):
        pages = {f"p{i}": [_block(options=("a", "A"))] for i in range(4)}  # 4x duplicate_options
        for name, blocks in pages.items():
            _write_page(self.root, name, f"https://h{name}.example/", blocks)
        cmd_template(self.root, self.csv)
        _annotate(self.csv, {("p0", "0"): "OK", ("p1", "0"): "OK", ("p2", "0"): "OK", ("p3", "0"): "BAD"})
        rep = build_report(self.root, self.csv)
        sig = rep["C3"]["signals"]["duplicate_options"]
        self.assertEqual((sig["tp"], sig["fp"], sig["decision"]), (1, 3, "RETIRER"))
        _annotate(self.csv, {("p0", "0"): "BAD", ("p1", "0"): "BAD"})
        self.assertEqual(build_report(self.root, self.csv)["C3"]["signals"]["duplicate_options"]["decision"], "GARDER")

    def test_c3_non_evalue_below_three_reviews(self):
        _write_page(self.root, "p1", "https://a.example/x", [_block(options=("a", "A"))])
        cmd_template(self.root, self.csv)
        _annotate(self.csv, {("p1", "0"): "OK"})
        self.assertEqual(build_report(self.root, self.csv)["C3"]["signals"]["duplicate_options"]["decision"], "NON_ÉVALUÉ")

    def test_c4_lists_blind_spots_without_gate(self):
        _write_page(self.root, "p1", "https://a.example/x", [_block()])
        cmd_template(self.root, self.csv)
        _annotate(self.csv, {("p1", "0"): "BAD", ("p1", ""): "MISSING"})
        rep = build_report(self.root, self.csv)
        self.assertEqual(rep["C4"]["count"], 2)

    def test_c1_cap_per_host_and_missing_itypes(self):
        for i in range(5):
            _write_page(self.root, f"p{i}", "https://same.example/x", [_block()])
        cmd_template(self.root, self.csv)
        _annotate(self.csv, {(f"p{i}", "0"): "OK" for i in range(5)})
        c1 = build_report(self.root, self.csv)["C1"]
        self.assertEqual((c1["pages_annotated"], c1["pages_effective"]), (5, 3))
        self.assertEqual(c1["hosts_over_cap"], ["same.example"])
        self.assertEqual(c1["status"], "KO")
        self.assertEqual(set(c1["missing_itypes"]), {"checkbox", "text", "dropdown"})

    def test_unannotated_pages_ignored_and_invalid_truth_reported(self):
        _write_page(self.root, "p1", "https://a.example/x", [_block(options=())])
        _write_page(self.root, "p2", "https://b.example/y", [_block(options=())])
        cmd_template(self.root, self.csv)
        _annotate(self.csv, {("p1", "0"): "peut-être"})
        rep = build_report(self.root, self.csv)
        self.assertEqual(rep["invalid_truth_rows"], 1)
        self.assertEqual(rep["C2"]["bug_confirmed_rows"], 0)  # p1 invalide, p2 non annotée
        self.assertEqual(rep["corpus"]["pages"], 2)


if __name__ == "__main__":
    unittest.main()