from __future__ import annotations

"""Tests synthétiques BDL-5 étape 1 : trace CTA passive en mémoire.

Aucun navigateur. Vérifie :
- les fonctions pures (marker_status, signals_changed) — dont le cas qNodes == 0
  que le booléen _did_progress confond avec un marqueur vide ;
- le buffer borné ;
- l'enveloppe : retour et exceptions inchangés, une seule exécution de la
  fonction observée, erreurs d'observation absorbées, mode intercept sans marqueur ;
- l'installation idempotente par page_snapshot, sans modifier cta_handler.
"""

import unittest

from Survey import cta_trace
from Survey.cta_trace import (
    MAX_EVENTS,
    build_observed_click,
    compact_marker,
    drain,
    marker_status,
    note_cta_event,
    signals_changed,
    stats,
)

_EMPTY = {"url": "", "txt": "", "qNodes": -1, "notifSig": "", "progGridSig": ""}


def _m(url="https://s.example/p1", txt="Question 1", q=3, notif="", grid=""):
    return {"url": url, "txt": txt, "qNodes": q, "notifSig": notif, "progGridSig": grid}


class MarkerStatusTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(marker_status(_m(), _m(txt="Question 2")), "ok")

    def test_both_empty(self):
        self.assertEqual(marker_status(dict(_EMPTY), dict(_EMPTY)), "both_empty")

    def test_empty_after_only(self):
        self.assertEqual(marker_status(_m(), dict(_EMPTY)), "empty_after")

    def test_empty_before_only(self):
        self.assertEqual(marker_status(dict(_EMPTY), _m()), "empty_before")

    def test_non_dict_is_empty(self):
        self.assertEqual(marker_status(None, None), "both_empty")

    def test_zero_question_nodes_is_not_empty(self):
        # qNodes == 0 est légitime (page sans champ) : ce n'est pas un marqueur vide.
        self.assertEqual(marker_status(_m(q=0), _m(q=0)), "ok")


class SignalsChangedTests(unittest.TestCase):
    def test_no_change(self):
        self.assertEqual(signals_changed(_m(), _m()), [])

    def test_each_signal(self):
        self.assertEqual(signals_changed(_m(), _m(url="https://s.example/p2")), ["url"])
        self.assertEqual(signals_changed(_m(), _m(txt="Autre")), ["txt"])
        self.assertEqual(signals_changed(_m(), _m(q=4)), ["qNodes"])
        self.assertEqual(signals_changed(_m(), _m(notif="error::x")), ["notifSig"])
        self.assertEqual(signals_changed(_m(), _m(grid="1::a::b")), ["progGridSig"])

    def test_zero_to_positive_question_nodes_detected(self):
        # Comparaison brute : 0 -> 2 est un changement (pas de conversion `or -1`).
        self.assertEqual(signals_changed(_m(q=0), _m(q=2)), ["qNodes"])

    def test_empty_marker_yields_no_marker_signal(self):
        # Marqueur après vide : aucune preuve de progression n'est inventée.
        self.assertEqual(signals_changed(_m(), dict(_EMPTY)), [])
        self.assertEqual(signals_changed(dict(_EMPTY), dict(_EMPTY)), [])

    def test_document_signal_independent_of_marker(self):
        pb = {"url": "u", "doc": "100.5"}
        pa = {"url": "u", "doc": "200.5"}
        self.assertEqual(signals_changed(dict(_EMPTY), dict(_EMPTY), pb, pa), ["doc"])
        # SPA : même document, pas de signal doc.
        self.assertEqual(signals_changed(_m(), _m(), pb, {"url": "u", "doc": "100.5"}), [])

    def test_document_signal_needs_both_identities(self):
        self.assertEqual(signals_changed(_m(), _m(), {"url": "u", "doc": ""}, {"url": "u", "doc": "9"}), [])

    def test_order_follows_closed_list(self):
        out = signals_changed(_m(), _m(url="https://s.example/p2", txt="B", q=9),
                              {"doc": "1"}, {"doc": "2"})
        self.assertEqual(out, ["url", "doc", "txt", "qNodes"])


class CompactTests(unittest.TestCase):
    def test_compact_marker_truncates(self):
        out = compact_marker(_m(txt="x" * 500))
        self.assertEqual(len(out["txt"]), 120)
        self.assertIsNone(compact_marker(None))

    def test_compact_report(self):
        out = cta_trace.compact_intercept_report({"clickCaptured": 1, "prevented": True, "target": {"a": 1}})
        self.assertEqual(out, {
            "clickCaptured": True, "submitCaptured": False,
            "prevented": True, "navigationBlocked": False,
        })
        self.assertIsNone(cta_trace.compact_intercept_report(None))


class BufferTests(unittest.TestCase):
    def setUp(self):
        drain()
        cta_trace._local.state = None

    def test_ring_buffer_bounded_and_counts_drops(self):
        for i in range(MAX_EVENTS + 5):
            note_cta_event({"i": i})
        st = stats()
        self.assertEqual(st["total"], MAX_EVENTS + 5)
        self.assertEqual(st["buffered"], MAX_EVENTS)
        self.assertEqual(st["dropped"], 5)
        events = drain()
        self.assertEqual(events[0]["i"], 5)  # les plus anciens sont écartés
        self.assertEqual(stats()["buffered"], 0)

    def test_non_dict_ignored(self):
        note_cta_event("x")
        note_cta_event(None)
        self.assertEqual(stats()["total"], 0)


class _Ctx:
    def __init__(self, pages):
        self._pages = list(pages)

    def evaluate(self, js):
        if not self._pages:
            raise RuntimeError("no more pages")
        value = self._pages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class _Handler:
    """Faux module cta_handler."""

    def __init__(self, *, intercept=False, markers=(), pages=(), report=None, intercept_raises=False):
        self._intercept = intercept
        self._markers = list(markers)
        self._ctx = _Ctx(pages)
        self._report = report
        self._intercept_raises = intercept_raises
        self.marker_calls = 0

    def _cta_intercept_enabled(self):
        if self._intercept_raises:
            raise RuntimeError("boom")
        return self._intercept

    def _resolve_ctx(self, driver):
        return self._ctx

    def _dom_progress_marker(self, driver):
        self.marker_calls += 1
        return self._markers.pop(0) if self._markers else dict(_EMPTY)

    def read_intercept_report(self, driver):
        return self._report


class _El:
    def __init__(self, target=None, raises=False):
        self._target = target
        self._raises = raises

    def evaluate(self, js):
        if self._raises:
            raise RuntimeError("stale")
        return self._target


_TARGET = {"tag": "button", "id": "next", "name": "", "className": "btn", "text": "Suivant"}


class WrapperTests(unittest.TestCase):
    def setUp(self):
        drain()
        cta_trace._local.state = None

    def _wrap(self, handler, fn):
        return build_observed_click(fn, handler)

    def test_normal_mode_event_and_unchanged_return(self):
        calls = []

        def original(driver, el):
            calls.append((driver, el))
            return True

        handler = _Handler(
            markers=[_m(), _m(url="https://s.example/p2", txt="Q2")],
            pages=[{"url": "https://s.example/p1", "doc": "1"},
                   {"url": "https://s.example/p2", "doc": "1"}],
        )
        drv, el = object(), _El(_TARGET)
        out = self._wrap(handler, original)(drv, el)
        self.assertIs(out, True)
        self.assertEqual(calls, [(drv, el)])  # exactement un appel, mêmes arguments
        (ev,) = drain()
        self.assertEqual(ev["fn"], "_click_with_intercept")
        self.assertEqual(ev["mode"], "normal")
        self.assertTrue(ev["returned"])
        self.assertIsNone(ev["raised"])
        self.assertEqual(ev["target"], _TARGET)
        self.assertEqual(ev["marker_status"], "ok")
        self.assertEqual(ev["signals_changed"], ["url", "txt"])
        self.assertIsNone(ev["intercept"])

    def test_false_return_preserved(self):
        handler = _Handler(markers=[_m(), _m()], pages=[{"url": "u", "doc": "1"}, {"url": "u", "doc": "1"}])
        out = self._wrap(handler, lambda d, e: False)(object(), _El(_TARGET))
        self.assertIs(out, False)
        (ev,) = drain()
        self.assertIs(ev["returned"], False)
        self.assertEqual(ev["signals_changed"], [])

    def test_empty_markers_are_not_presented_as_progress(self):
        handler = _Handler(markers=[dict(_EMPTY), dict(_EMPTY)], pages=[])
        # Le booléen runtime de la fonction observée reste True (anti-double-clic) :
        # la trace, elle, expose l'absence de preuve.
        out = self._wrap(handler, lambda d, e: True)(object(), _El(_TARGET))
        self.assertIs(out, True)
        (ev,) = drain()
        self.assertEqual(ev["marker_status"], "both_empty")
        self.assertEqual(ev["signals_changed"], [])

    def test_exception_reraised_identically_and_traced(self):
        err = ValueError("clic impossible")

        def original(driver, el):
            raise err

        handler = _Handler(markers=[_m(), _m()], pages=[{"url": "u", "doc": "1"}, {"url": "u", "doc": "1"}])
        with self.assertRaises(ValueError) as ctx:
            self._wrap(handler, original)(object(), _El(_TARGET))
        self.assertIs(ctx.exception, err)
        (ev,) = drain()
        self.assertEqual(ev["raised"], "ValueError")
        self.assertIsNone(ev["returned"])

    def test_observation_errors_never_change_behavior(self):
        # Cible illisible, pages illisibles, marqueurs illisibles, intercept qui lève.
        handler = _Handler(intercept_raises=True, pages=[RuntimeError("x"), RuntimeError("y")])
        out = self._wrap(handler, lambda d, e: True)(object(), _El(raises=True))
        self.assertIs(out, True)

    def test_intercept_mode_takes_no_marker(self):
        handler = _Handler(
            intercept=True,
            pages=[{"url": "u", "doc": "1"}],
            report={"clickCaptured": True, "prevented": True, "navigationBlocked": False,
                    "submitCaptured": False, "target": {"tag": "button"}},
        )
        out = self._wrap(handler, lambda d, e: True)(object(), _El(_TARGET))
        self.assertIs(out, True)
        self.assertEqual(handler.marker_calls, 0)  # aucun marqueur en mode intercept
        (ev,) = drain()
        self.assertEqual(ev["mode"], "intercept")
        self.assertEqual(ev["marker_status"], "not_collected")
        self.assertEqual(ev["signals_changed"], [])
        self.assertEqual(ev["intercept"]["clickCaptured"], True)
        self.assertEqual(ev["intercept"]["prevented"], True)

    def test_intercept_impossible_recorded_as_not_captured(self):
        handler = _Handler(intercept=True, pages=[{"url": "u", "doc": "1"}],
                           report={"clickCaptured": False, "prevented": False})
        out = self._wrap(handler, lambda d, e: False)(object(), _El(_TARGET))
        self.assertIs(out, False)
        (ev,) = drain()
        self.assertFalse(ev["intercept"]["clickCaptured"])
        self.assertIs(ev["returned"], False)

    def test_caller_name_is_recorded(self):
        handler = _Handler(markers=[_m(), _m()], pages=[{"url": "u", "doc": "1"}, {"url": "u", "doc": "1"}])
        wrapped = self._wrap(handler, lambda d, e: True)

        def click_primary_cta(driver, el):  # simule une fonction publique de cta_handler
            return wrapped(driver, el)

        click_primary_cta(object(), _El(_TARGET))
        (ev,) = drain()
        self.assertEqual(ev["caller"], "click_primary_cta")


class InstallTests(unittest.TestCase):
    def test_install_is_idempotent_and_does_not_edit_cta_handler(self):
        import Survey.cta_handler as cta_handler
        import Survey.page_snapshot as page_snapshot

        saved = cta_handler._click_with_intercept
        try:
            # Repart de la fonction d'origine, quel que soit l'état d'import.
            cta_handler._click_with_intercept = getattr(saved, "_phase1a_original", saved)
            origin = cta_handler._click_with_intercept
            self.assertFalse(getattr(origin, "_phase1a_observer", False))

            page_snapshot._install_cta_observer()
            first = cta_handler._click_with_intercept
            self.assertTrue(getattr(first, "_phase1a_observer", False))
            self.assertIs(first._phase1a_original, origin)

            page_snapshot._install_cta_observer()  # 2e installation : sans effet
            self.assertIs(cta_handler._click_with_intercept, first)

            # Les fonctions publiques résolvent _click_with_intercept par lookup
            # global du module : remplacer l'attribut suffit à toutes les couvrir.
            self.assertIs(cta_handler.click_primary_cta.__globals__, vars(cta_handler))
        finally:
            cta_handler._click_with_intercept = saved

    def test_flag_default_and_off(self):
        import os
        import Survey.page_snapshot as page_snapshot

        old = os.environ.pop("SURVEY_BDL_CTA_TRACE", None)
        try:
            self.assertTrue(page_snapshot._bdl_cta_trace_enabled())
            os.environ["SURVEY_BDL_CTA_TRACE"] = "0"
            self.assertFalse(page_snapshot._bdl_cta_trace_enabled())
        finally:
            os.environ.pop("SURVEY_BDL_CTA_TRACE", None)
            if old is not None:
                os.environ["SURVEY_BDL_CTA_TRACE"] = old


if __name__ == "__main__":
    unittest.main()
