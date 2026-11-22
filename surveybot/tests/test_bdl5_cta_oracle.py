from __future__ import annotations

"""Tests synthétiques de l'oracle CTA BDL-5 v2 (étape 2, sans navigateur).

Fixtures sous forme d'enregistrements minimaux (chaînes tronquées, aucune
donnée de session), au format exact persisté par
``failure_recorder.record_collected_cta`` (voir ``tests/test_bdl5_cta_collect.py``
pour la liste blanche de champs). Un test par règle et par raison, plus les
propriétés de pureté exigées par le module.
"""

import ast
import copy
import json
import unittest
from pathlib import Path

from Survey import cta_oracle
from Survey.cta_oracle import (
    INCONCLUSIVE,
    PASS,
    REASON_CLICK_RAISED,
    REASON_INTERCEPT_MODE_OBSOLETE,
    REASON_MALFORMED_RECORD,
    REASON_MARKER_UNRELIABLE,
    REASON_NOT_A_CLICK_RECORD,
    REASON_NO_SIGNAL_MARKER_OK,
    REASON_STRONG_SIGNAL_CHANGED,
    REASON_UNOBSERVED_PATH,
    REASON_WEAK_SIGNALS_ONLY,
    SUSPECTED,
    evaluate_cta_click,
)


def _click(mode="normal", raised=None, signals=(), marker_status="ok",
           page_before_url="https://s.example/p1", page_after_url="https://s.example/p1",
           **extra) -> dict:
    rec = {
        "v": "1", "kind": "click", "fn": "_click_with_intercept",
        "caller": "try_click_navigation_cta", "mode": mode,
        "returned": True, "raised": raised,
        "marker_status": marker_status, "signals_changed": list(signals),
        "target": {"tag": "div", "id": "next_button", "name": "", "className": "btn", "text": "Suivant"},
        "marker_before": {"url": page_before_url, "txt": "Q1", "qNodes": 3, "notifSig": "", "progGridSig": None},
        "marker_after": {"url": page_after_url, "txt": "Q2", "qNodes": 4, "notifSig": "", "progGridSig": None},
        "page_before": {"url": page_before_url, "doc": "100.5"},
        "page_after": {"url": page_after_url, "doc": "200.5"},
        "ts": "2026-11-17T10:00:00", "pid": 1234,
    }
    rec.update(extra)
    return rec


def _entry(outcome="unobserved_path", **extra) -> dict:
    rec = {
        "v": "1", "kind": "entry", "fn": "try_click_qps_skip_to_survey",
        "outcome": outcome, "mode": "normal", "clicks_observed": 0,
        "t_start": 1.0, "duration_ms": 5, "ts": "2026-11-17T10:00:00", "pid": 1234,
    }
    rec.update(extra)
    return rec


class RuleOneCoverageRecordTests(unittest.TestCase):
    def test_unobserved_path_coverage_record_is_inconclusive(self) -> None:
        r = evaluate_cta_click(_entry())
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_UNOBSERVED_PATH))

    def test_unknown_kind_is_not_a_click_record(self) -> None:
        r = evaluate_cta_click({"kind": "mystery"})
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_NOT_A_CLICK_RECORD))

    def test_entry_with_other_outcome_is_not_a_click_record(self) -> None:
        # Seul unobserved_path est persisté en pratique (voir failure_recorder),
        # mais l'oracle ne doit pas inventer un verdict de clic pour autant.
        r = evaluate_cta_click(_entry(outcome="no_click"))
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_NOT_A_CLICK_RECORD))


class RuleTwoInterceptModeTests(unittest.TestCase):
    def test_intercept_mode_is_inconclusive(self) -> None:
        r = evaluate_cta_click(_click(mode="intercept", signals=["doc"]))
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_INTERCEPT_MODE_OBSOLETE))

    def test_rule_order_intercept_mode_wins_over_strong_signals(self) -> None:
        # Mêmes signaux qu'un cas PASS en mode normal : l'ordre des règles
        # doit garder INCONCLUSIVE malgré la présence de signaux forts.
        r = evaluate_cta_click(_click(mode="intercept", signals=["doc", "qNodes"], marker_status="ok"))
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_INTERCEPT_MODE_OBSOLETE))


class RuleThreeClickRaisedTests(unittest.TestCase):
    def test_click_raised_is_inconclusive(self) -> None:
        r = evaluate_cta_click(_click(raised="TimeoutError", signals=["doc"]))
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_CLICK_RAISED))
        self.assertEqual(r["evidence"]["raised"], "TimeoutError")


class RuleFourStrongSignalTests(unittest.TestCase):
    def test_doc_and_qnodes_changed_passes(self) -> None:
        r = evaluate_cta_click(_click(signals=["doc", "qNodes"]))
        self.assertEqual((r["verdict"], r["reason"]), (PASS, REASON_STRONG_SIGNAL_CHANGED))

    def test_doc_alone_changed_passes(self) -> None:
        r = evaluate_cta_click(_click(signals=["doc"]))
        self.assertEqual(r["verdict"], PASS)

    def test_marker_after_empty_but_doc_changed_still_passes(self) -> None:
        rec = _click(signals=["doc"], marker_status="empty_after",
                     page_before_url="https://s.example/p1", page_after_url="https://s.example/p2")
        r = evaluate_cta_click(rec)
        self.assertEqual((r["verdict"], r["reason"]), (PASS, REASON_STRONG_SIGNAL_CHANGED))

    def test_url_signal_counts_when_really_different(self) -> None:
        rec = _click(signals=["url"], page_before_url="https://s.example/p1", page_after_url="https://s.example/p2")
        r = evaluate_cta_click(rec)
        self.assertEqual((r["verdict"], r["reason"]), (PASS, REASON_STRONG_SIGNAL_CHANGED))
        self.assertTrue(r["evidence"]["url_changed"])


class RuleFiveWeakSignalsOnlyTests(unittest.TestCase):
    def test_text_only_changed_is_inconclusive_even_if_persisted_text_identical(self) -> None:
        # L'oracle ne doit JAMAIS recalculer txt lui-même : il fait confiance
        # à signals_changed, même si les valeurs persistées semblent identiques.
        rec = _click(signals=["txt"])
        rec["marker_before"]["txt"] = "Merci de répondre"
        rec["marker_after"]["txt"] = "Merci de répondre"
        r = evaluate_cta_click(rec)
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_WEAK_SIGNALS_ONLY))

    def test_text_changed_with_required_field_message_is_inconclusive(self) -> None:
        rec = _click(signals=["txt", "notifSig"])
        rec["marker_after"]["notifSig"] = "error:Ce champ est obligatoire"
        r = evaluate_cta_click(rec)
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_WEAK_SIGNALS_ONLY))

    def test_ignored_url_with_weak_signal_stays_weak_signals_only(self) -> None:
        rec = _click(signals=["url", "txt"], page_before_url="https://s.example/p1",
                      page_after_url="https://s.example/p1")
        r = evaluate_cta_click(rec)
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_WEAK_SIGNALS_ONLY))


class RuleSixAndSevenNoSignalTests(unittest.TestCase):
    def test_no_signal_marker_ok_is_suspected(self) -> None:
        r = evaluate_cta_click(_click(signals=[], marker_status="ok"))
        self.assertEqual((r["verdict"], r["reason"]), (SUSPECTED, REASON_NO_SIGNAL_MARKER_OK))

    def test_no_signal_marker_unreliable_is_inconclusive(self) -> None:
        for status in ("both_empty", "empty_before", "empty_after", "not_collected"):
            r = evaluate_cta_click(_click(signals=[], marker_status=status))
            self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_MARKER_UNRELIABLE), status)

    def test_url_listed_but_urls_identical_is_ignored(self) -> None:
        # url est listé comme signal mais les URL persistées sont identiques :
        # il doit être ignoré, pas compté comme PASS.
        rec = _click(signals=["url"], marker_status="ok",
                      page_before_url="https://s.example/p1", page_after_url="https://s.example/p1")
        r = evaluate_cta_click(rec)
        self.assertEqual((r["verdict"], r["reason"]), (SUSPECTED, REASON_NO_SIGNAL_MARKER_OK))


class MalformedAndNonDictTests(unittest.TestCase):
    def test_malformed_record_missing_signals_changed(self) -> None:
        rec = {"v": "1", "kind": "click", "mode": "normal", "raised": None}
        r = evaluate_cta_click(rec)
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_MALFORMED_RECORD))

    def test_malformed_record_wrong_type_signals_changed(self) -> None:
        rec = _click()
        rec["signals_changed"] = "doc"  # chaîne au lieu d'une liste
        r = evaluate_cta_click(rec)
        self.assertEqual((r["verdict"], r["reason"]), (INCONCLUSIVE, REASON_MALFORMED_RECORD))

    def test_non_dict_input_returns_none(self) -> None:
        for value in ("not-a-dict", None, ["x"], 42, 3.14):
            self.assertIsNone(evaluate_cta_click(value))


class RealCaseFixtureTests(unittest.TestCase):
    """Cas réel figé (tronqué), voir Utils/BUG_DETECTION_LAYER_TRACKING.md,
    section BDL-5 v2 : eu.surveyme.online, div#next_button via
    try_click_navigation_cta, mode normal, marker_status=ok, signaux changés
    url, doc, txt, qNodes."""

    def test_real_case_2026_11_17_passes(self) -> None:
        rec = _click(
            signals=["url", "doc", "txt", "qNodes"],
            marker_status="ok",
            page_before_url="https://eu.surveyme.online/s/p1",
            page_after_url="https://eu.surveyme.online/s/p2",
        )
        rec["target"] = {"tag": "div", "id": "next_button", "name": "", "className": "", "text": ""}
        rec["caller"] = "try_click_navigation_cta"
        r = evaluate_cta_click(rec)
        self.assertEqual(r["verdict"], PASS)


class PurityPropertyTests(unittest.TestCase):
    def test_input_is_never_mutated(self) -> None:
        for rec in (_click(signals=["doc"]), _click(mode="intercept"), _entry(), {"kind": "x"}):
            before = copy.deepcopy(rec)
            evaluate_cta_click(rec)
            self.assertEqual(rec, before)

    def test_output_is_json_serializable(self) -> None:
        cases = [
            _click(signals=["doc", "qNodes"]),
            _click(mode="intercept"),
            _click(raised="TimeoutError"),
            _click(signals=[], marker_status="ok"),
            _click(signals=[], marker_status="both_empty"),
            _entry(),
            {"kind": "mystery"},
            {"v": "1", "kind": "click", "mode": "normal", "raised": None},
        ]
        for rec in cases:
            json.dumps(evaluate_cta_click(rec))  # ne lève pas
        self.assertIsNone(evaluate_cta_click("x"))

    def test_module_imports_no_browser_or_cta_handler(self) -> None:
        source = Path(cta_oracle.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for name in imported:
            lowered = name.lower()
            self.assertNotIn("playwright", lowered)
            self.assertNotIn("selenium", lowered)
            self.assertNotIn("cta_handler", lowered)

    def test_rule_order_is_respected_for_intercept_mode_with_signals(self) -> None:
        # Doublon volontaire de la propriété exigée par la consigne : un
        # enregistrement en mode interception avec des signaux reste
        # INCONCLUSIVE (la règle 2 précède la règle 4).
        r = evaluate_cta_click(_click(mode="intercept", signals=["doc", "qNodes", "url"]))
        self.assertEqual(r["verdict"], INCONCLUSIVE)
        self.assertEqual(r["reason"], REASON_INTERCEPT_MODE_OBSOLETE)


if __name__ == "__main__":
    unittest.main()
