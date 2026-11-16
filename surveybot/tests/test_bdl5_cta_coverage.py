from __future__ import annotations

"""Tests synthétiques BDL-5 étape 2 : couverture déclarée des appels CTA.

Aucun navigateur. Vérifie la classification (observed / unobserved_path /
no_click / raised), le comptage de l'appel de plus haut niveau uniquement, la
transparence de l'enveloppe (retour, exceptions, signature) et le rebind des
modules ayant importé les fonctions par valeur.
"""

import types
import unittest

from Survey import cta_trace
from Survey.cta_trace import (
    ENTRY_FUNCTIONS,
    build_observed_click,
    build_observed_entry,
    classify_entry,
    coverage,
    drain,
    drain_coverage,
    rebind_aliases,
)


def _reset():
    drain()
    cta_trace._local.state = None


class ClassifyTests(unittest.TestCase):
    def test_truth_table(self):
        self.assertEqual(classify_entry(False, True, 2), "observed")
        self.assertEqual(classify_entry(False, False, 1), "observed")
        self.assertEqual(classify_entry(False, True, 0), "unobserved_path")
        self.assertEqual(classify_entry(False, False, 0), "no_click")
        self.assertEqual(classify_entry(True, None, 0), "raised")
        self.assertEqual(classify_entry(True, True, 3), "raised")

    def test_entry_functions_list_is_closed_and_public(self):
        self.assertEqual(len(ENTRY_FUNCTIONS), len(set(ENTRY_FUNCTIONS)))
        self.assertTrue(all(not n.startswith("_") for n in ENTRY_FUNCTIONS))


class _Handler:
    def __init__(self, intercept=False):
        self._i = intercept

    def _cta_intercept_enabled(self):
        return self._i


def _fake_click_event():
    """Simule un clic tracé par l'enveloppe de l'étape 1."""
    cta_trace.note_cta_event({"kind": "click"})


class EntryWrapperTests(unittest.TestCase):
    def setUp(self):
        _reset()

    def _wrap(self, fn, name="click_primary_cta", handler=None):
        return build_observed_entry(fn, name, handler or _Handler())

    def test_observed_when_a_click_was_traced(self):
        def fn(driver):
            _fake_click_event()
            return True

        self.assertIs(self._wrap(fn)(object()), True)
        t = coverage()["totals"]
        self.assertEqual((t["observed"], t["unobserved_path"], t["no_click"]), (1, 0, 0))

    def test_unobserved_path_when_true_without_click(self):
        self.assertIs(self._wrap(lambda d: True, "try_click_qps_skip_to_survey")(object()), True)
        snap = coverage()
        self.assertEqual(snap["counts"]["try_click_qps_skip_to_survey"], {"unobserved_path": 1})
        (detail,) = snap["unobserved"]
        self.assertEqual(detail["fn"], "try_click_qps_skip_to_survey")
        self.assertEqual(detail["kind"], "entry")
        self.assertEqual(detail["mode"], "normal")
        self.assertEqual(detail["clicks_observed"], 0)

    def test_unobserved_detail_records_intercept_mode(self):
        self._wrap(lambda d: True, handler=_Handler(intercept=True))(object())
        self.assertEqual(coverage()["unobserved"][0]["mode"], "intercept")

    def test_no_click_when_false_without_click(self):
        self.assertIs(self._wrap(lambda d: False)(object()), False)
        snap = coverage()
        self.assertEqual(snap["totals"]["no_click"], 1)
        self.assertEqual(snap["unobserved"], [])  # pas de détail pour no_click

    def test_raised_is_counted_and_reraised_identically(self):
        err = RuntimeError("boom")

        def fn(driver):
            raise err

        with self.assertRaises(RuntimeError) as ctx:
            self._wrap(fn)(object())
        self.assertIs(ctx.exception, err)
        self.assertEqual(coverage()["totals"]["raised"], 1)

    def test_only_outermost_call_is_classified(self):
        inner = self._wrap(lambda d: True, "click_primary_cta")

        def outer_fn(driver):
            inner(driver)          # appel imbriqué : non compté
            _fake_click_event()
            return True

        outer = self._wrap(outer_fn, "try_click_navigation_cta_any_context")
        outer(object())
        snap = coverage()
        self.assertEqual(snap["totals"]["total"], 1)
        self.assertEqual(list(snap["counts"]), ["try_click_navigation_cta_any_context"])
        self.assertEqual(snap["totals"]["observed"], 1)

    def test_depth_restored_after_exception(self):
        def boom(driver):
            raise ValueError()

        wrapped = self._wrap(boom)
        with self.assertRaises(ValueError):
            wrapped(object())
        # La profondeur est revenue à 0 : l'appel suivant est de nouveau classé.
        self._wrap(lambda d: False)(object())
        self.assertEqual(coverage()["totals"]["total"], 2)

    def test_signature_and_name_preserved_and_args_forwarded(self):
        seen = {}

        def click_cta_strong_any_context(driver, text=None, label_hint=None, depth=2, **kw):
            seen.update(text=text, label_hint=label_hint, depth=depth, kw=kw)
            return False

        wrapped = self._wrap(click_cta_strong_any_context, "click_cta_strong_any_context")
        self.assertEqual(wrapped.__name__, "click_cta_strong_any_context")
        wrapped(object(), "Suivant", label_hint="x", depth=3, extra=1)
        self.assertEqual(seen, {"text": "Suivant", "label_hint": "x", "depth": 3, "kw": {"extra": 1}})

    def test_drain_coverage_resets(self):
        self._wrap(lambda d: True)(object())
        first = drain_coverage()
        self.assertEqual(first["totals"]["unobserved_path"], 1)
        self.assertEqual(coverage()["totals"]["total"], 0)
        self.assertEqual(coverage()["unobserved"], [])

    def test_unobserved_details_ring_is_bounded(self):
        wrapped = self._wrap(lambda d: True)
        for _ in range(cta_trace.MAX_UNOBSERVED_DETAILS + 10):
            wrapped(object())
        snap = coverage()
        self.assertEqual(len(snap["unobserved"]), cta_trace.MAX_UNOBSERVED_DETAILS)
        self.assertEqual(snap["totals"]["unobserved_path"], cta_trace.MAX_UNOBSERVED_DETAILS + 10)

    def test_entry_counting_does_not_flood_click_buffer(self):
        wrapped = self._wrap(lambda d: False)
        for _ in range(500):
            wrapped(object())
        self.assertEqual(cta_trace.stats()["total"], 0)  # aucun événement de clic créé


class IntegrationWithClickWrapperTests(unittest.TestCase):
    """Enveloppe de clic (étape 1) + enveloppe d'entrée (étape 2) ensemble."""

    def setUp(self):
        _reset()

    def test_click_through_common_path_is_observed(self):
        class H(_Handler):
            def _resolve_ctx(self, d):
                return types.SimpleNamespace(evaluate=lambda js: {"url": "u", "doc": "1"})

            def _dom_progress_marker(self, d):
                return {"url": "u", "txt": "t", "qNodes": 1, "notifSig": "", "progGridSig": ""}

        h = H()
        click = build_observed_click(lambda d, el: True, h)
        el = types.SimpleNamespace(evaluate=lambda js: {"tag": "button"})

        public = build_observed_entry(lambda d: click(d, el), "click_primary_cta", h)
        self.assertIs(public(object()), True)
        self.assertEqual(coverage()["totals"]["observed"], 1)
        self.assertEqual(coverage()["totals"]["unobserved_path"], 0)


class RebindTests(unittest.TestCase):
    def test_rebinds_by_value_imports_only(self):
        handler = types.ModuleType("fake_handler")

        def original():
            return 1

        def wrapped():
            return 2

        by_value = types.ModuleType("by_value")
        by_value.click = original                 # from x import click
        by_value.alias = original                 # alias vers la même fonction
        by_attr = types.ModuleType("by_attr")     # import x; x.click : rien à rebinder
        by_attr.handler = handler
        other = types.ModuleType("other")
        other.click = lambda: 3                   # autre fonction homonyme : intacte
        handler.click = original                  # ignoré (le handler est traité par setattr)

        n = rebind_aliases(handler, original, wrapped, modules=[handler, by_value, by_attr, other, None])
        self.assertEqual(n, 2)
        self.assertIs(by_value.click, wrapped)
        self.assertIs(by_value.alias, wrapped)
        self.assertIs(handler.click, original)
        self.assertEqual(other.click(), 3)


class InstallCoverageTests(unittest.TestCase):
    def test_install_wraps_all_entry_functions_idempotently_and_rebinds(self):
        import Survey.cta_handler as cta_handler
        import Survey.page_snapshot as page_snapshot

        names = [n for n in ENTRY_FUNCTIONS if hasattr(cta_handler, n)]
        self.assertEqual(names, list(ENTRY_FUNCTIONS))  # toutes existent dans cta_handler

        saved = {n: getattr(cta_handler, n) for n in names}
        alias_mod = types.ModuleType("alias_mod_bdl5")
        try:
            for n, fn in saved.items():
                setattr(cta_handler, n, getattr(fn, "_phase1a_original", fn))
            originals = {n: getattr(cta_handler, n) for n in names}
            alias_mod.click_primary_cta = originals["click_primary_cta"]
            import sys
            sys.modules["alias_mod_bdl5"] = alias_mod

            page_snapshot._install_cta_coverage_observer()
            for n in names:
                wrapped = getattr(cta_handler, n)
                self.assertTrue(getattr(wrapped, "_phase1a_observer", False), n)
                self.assertIs(wrapped._phase1a_original, originals[n], n)
            self.assertIs(alias_mod.click_primary_cta, cta_handler.click_primary_cta)

            first = {n: getattr(cta_handler, n) for n in names}
            page_snapshot._install_cta_coverage_observer()  # 2e installation : sans effet
            for n in names:
                self.assertIs(getattr(cta_handler, n), first[n], n)
        finally:
            import sys
            sys.modules.pop("alias_mod_bdl5", None)
            for n, fn in saved.items():
                setattr(cta_handler, n, fn)


if __name__ == "__main__":
    unittest.main()
