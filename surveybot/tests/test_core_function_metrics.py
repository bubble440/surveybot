from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from Survey.core_function_metrics import CoreFunctionMetrics
from Survey.extractor_integrity import _hash_function


class CoreFunctionMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Survey"
        self.root.mkdir()
        self.output = Path(self.temp.name) / "metrics"
        (self.root / "sample.py").write_text(
            "def first():\n    return 1\n\ndef second():\n    return 2\n", encoding="utf-8"
        )
        registry = {
            f"sample.py::{name}": {"hash": _hash_function(self.root, "sample.py", name)}
            for name in ("first", "second")
        }
        (self.root / "extractor_integrity.json").write_text(json.dumps(registry), encoding="utf-8")

    def _reports(self) -> list[dict]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in self.output.glob("*.json")]

    def test_stable_distinct_ids_counts_and_version_isolation(self) -> None:
        first = CoreFunctionMetrics(root=self.root, output_dir=self.output, register_atexit=False)
        for _ in range(3):
            first.record_call("sample.py::first", stage="extraction")
        first.record_call("sample.py::first", stage="action")
        first.record_call("sample.py::second", stage="action")
        first.record_call("user@example.com", stage="extraction")
        first.record_call("sample.py::first", stage="https://example.test/?session=secret")
        self.assertTrue(first.flush())

        report1 = self._reports()[0]
        rows1 = {(row["function_id"], row["stage"]): row for row in report1["functions"]}
        self.assertEqual(set(rows1), {
            ("sample.py::first", "extraction"), ("sample.py::first", "action"), ("sample.py::second", "action")
        })
        first_extraction = rows1[("sample.py::first", "extraction")]
        self.assertEqual(first_extraction["call_count"], 3)
        self.assertEqual(rows1[("sample.py::first", "action")]["call_count"], 1)
        self.assertEqual(rows1[("sample.py::second", "action")]["call_count"], 1)
        self.assertEqual(first_extraction["baseline_hash"], first_extraction["code_hash"])

        (self.root / "sample.py").write_text(
            "def first():\n    return 3\n\ndef second():\n    return 2\n", encoding="utf-8"
        )
        second = CoreFunctionMetrics(root=self.root, output_dir=self.output, register_atexit=False)
        second.record_call("sample.py::first", stage="extraction")
        self.assertTrue(second.flush())

        reports = self._reports()
        self.assertEqual(len(reports), 2)
        row2 = next(
            row for report in reports if report["producer_id"] != report1["producer_id"]
            for row in report["functions"]
        )
        self.assertEqual(row2["function_id"], first_extraction["function_id"])
        self.assertEqual(row2["baseline_hash"], first_extraction["baseline_hash"])
        self.assertNotEqual(row2["code_hash"], first_extraction["code_hash"])
        self.assertEqual(row2["call_count"], 1)
        self.assertNotIn("user@example.com", json.dumps(reports))
        self.assertNotIn("session=secret", json.dumps(reports))

    def test_metrics_failure_does_not_stop_caller(self) -> None:
        blocked = Path(self.temp.name) / "blocked"
        blocked.write_text("file, not directory", encoding="utf-8")
        metrics = CoreFunctionMetrics(root=self.root, output_dir=blocked, register_atexit=False)

        def caller() -> int:
            metrics.record_call("sample.py::first", stage="extraction")
            return 42

        self.assertEqual(caller(), 42)
        self.assertFalse(metrics.flush())

    def test_two_processes_keep_separate_snapshots(self) -> None:
        script = (
            "import sys; from pathlib import Path; "
            "from Survey.core_function_metrics import CoreFunctionMetrics; "
            "m=CoreFunctionMetrics(root=Path(sys.argv[1]), output_dir=Path(sys.argv[2])); "
            "m.record_call('sample.py::first', stage='extraction'); "
            "sys.exit(0 if m.flush() else 1)"
        )
        commands = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(self.root), str(self.output)],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        for process in commands:
            _stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
        reports = self._reports()
        self.assertEqual(len(reports), 2)
        self.assertEqual(len({report["producer_id"] for report in reports}), 2)
        self.assertEqual([report["functions"][0]["call_count"] for report in reports], [1, 1])


if __name__ == "__main__":
    unittest.main()
