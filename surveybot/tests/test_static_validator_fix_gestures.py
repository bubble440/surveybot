from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from Survey.autofix.static_validator import (
    FIX_GESTURES_TIMEOUT_LIMIT_MS,
    GESTURE_DEFECT_MISSING_TIMEOUT,
    GESTURE_DEFECT_NON_POSITIVE_TIMEOUT,
    GESTURE_DEFECT_TIMEOUT_TOO_LARGE,
    _check_fix_gestures,
)


class StaticValidatorFixGesturesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.package_root = Path(self.temp.name) / "surveybot"
        self.survey = self.package_root / "Survey"
        self.survey.mkdir(parents=True)

    def _write_module(self, source: str, *, name: str = "external_fix_sample.py") -> Path:
        path = self.survey / name
        path.write_text(source, encoding="utf-8")
        return path

    def _check(self, *paths: Path) -> dict:
        return _check_fix_gestures(tuple(paths), package_root=self.package_root)

    def test_click_without_timeout_is_a_defect(self) -> None:
        path = self._write_module(
            "def handler(driver, dom, action):\n"
            "    dom.click()\n"
            "    return None\n"
        )
        result = self._check(path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["files"], ["Survey/external_fix_sample.py"])
        self.assertEqual(result["limit_ms"], FIX_GESTURES_TIMEOUT_LIMIT_MS)
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["defects"]), 1)
        defect = result["defects"][0]
        self.assertEqual(defect["file"], "Survey/external_fix_sample.py")
        self.assertEqual(defect["line"], 2)
        self.assertEqual(defect["method"], "click")
        self.assertEqual(defect["kind"], GESTURE_DEFECT_MISSING_TIMEOUT)

    def test_hover_then_click_without_timeout_are_two_defects(self) -> None:
        path = self._write_module(
            "def handler(driver, dom, action):\n"
            "    dom.hover()\n"
            "    dom.click()\n"
            "    return None\n"
        )
        result = self._check(path)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["defects"]), 2)
        self.assertEqual([d["method"] for d in result["defects"]], ["hover", "click"])
        self.assertTrue(all(d["kind"] == GESTURE_DEFECT_MISSING_TIMEOUT for d in result["defects"]))

    def test_non_positive_and_too_large_timeouts_are_defects(self) -> None:
        path = self._write_module(
            "def handler(driver, dom, action):\n"
            "    dom.click(timeout=0)\n"
            "    dom.hover(timeout=-100)\n"
            "    dom.fill('x', timeout=3000)\n"
            "    return None\n"
        )
        result = self._check(path)
        self.assertFalse(result["ok"])
        kinds = [d["kind"] for d in result["defects"]]
        self.assertEqual(kinds, [
            GESTURE_DEFECT_NON_POSITIVE_TIMEOUT,
            GESTURE_DEFECT_NON_POSITIVE_TIMEOUT,
            GESTURE_DEFECT_TIMEOUT_TOO_LARGE,
        ])

    def test_timeout_resolved_from_a_module_constant_of_1000_is_fine(self) -> None:
        path = self._write_module(
            "GESTURE_TIMEOUT_MS = 1000\n\n"
            "def handler(driver, dom, action):\n"
            "    dom.click(timeout=GESTURE_TIMEOUT_MS)\n"
            "    return None\n"
        )
        result = self._check(path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["defects"], [])

    def test_dynamic_unresolvable_timeout_is_fine(self) -> None:
        path = self._write_module(
            "import time\n\n"
            "def handler(driver, dom, action):\n"
            "    dom.click(timeout=compute_budget())\n"
            "    dom.hover(timeout=time.sleep(0))\n"
            "    return None\n"
        )
        result = self._check(path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["defects"], [])

    def test_module_without_any_gesture_is_fine(self) -> None:
        path = self._write_module(
            "def handler(driver, dom, action):\n"
            "    return dom.query_selector('div')\n"
        )
        result = self._check(path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["defects"], [])

    def test_more_than_five_defects_are_bounded_to_five(self) -> None:
        body = "\n".join("    dom.click()" for _ in range(8))
        path = self._write_module(f"def handler(driver, dom, action):\n{body}\n    return None\n")
        result = self._check(path)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["defects"]), 5)

    def test_no_fix_module_and_empty_patch_are_successful_and_empty(self) -> None:
        result = self._check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["files"], [])
        self.assertEqual(result["defects"], [])
        self.assertIsNone(result["error"])

    def test_unparsable_module_yields_no_defect(self) -> None:
        path = self._write_module("def handler(driver, dom, action:\n    dom.click()\n")
        result = self._check(path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["defects"], [])
        self.assertEqual(result["files"], ["Survey/external_fix_sample.py"])

    def test_star_kwargs_expansion_is_not_treated_as_missing_timeout(self) -> None:
        path = self._write_module(
            "def handler(driver, dom, action):\n"
            "    opts = {}\n"
            "    dom.click(**opts)\n"
            "    return None\n"
        )
        result = self._check(path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["defects"], [])

    def test_unrelated_method_names_are_never_matched(self) -> None:
        path = self._write_module(
            "def handler(driver, dom, action):\n"
            "    dom.query_selector('div')\n"
            "    dom.evaluate('() => 1')\n"
            "    return None\n"
        )
        result = self._check(path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["defects"], [])

    def test_result_contains_no_survey_content(self) -> None:
        path = self._write_module(
            "def handler(driver, dom, action):\n"
            "    dom.click()  # value='private answer secret'\n"
            "    return None\n"
        )
        result = self._check(path)
        self.assertNotIn("private answer secret", str(result))


if __name__ == "__main__":
    unittest.main()
