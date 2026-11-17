from __future__ import annotations

"""BDL-5 étape 3 : tests de non-régression du chemin CTA (caractérisation).

Ces tests FIGENT le comportement actuel de cta_handler avant toute évolution
(oracle CTA, neutralisation de _did_progress, etc.). Aucun navigateur.

Convention : un test marqué « PIN » fige un comportement actuel connu pour être
discutable (voir l'audit BDL-5). Il ne valide pas ce comportement : il garantit
qu'un changement futur sera DÉCIDÉ explicitement, pas subi. Si l'un d'eux doit
changer, modifier le test dans le même patch, avec validation.
"""

import inspect
import os
import types
import unittest
from unittest import mock

import Survey.cta_handler as cta_handler
from Survey import cta_trace
from Survey.cta_trace import ENTRY_FUNCTIONS, build_observed_click, drain

_ENV = cta_handler.CTA_INTERCEPT_ENV_VAR


def _orig(name):
    """Fonction d'origine, même si une enveloppe BDL a été installée."""
    fn = getattr(cta_handler, name)
    return getattr(fn, "_phase1a_original", fn)


def _m(url="https://s.example/p1", txt="Q1", q=3, notif="", grid=""):
    return {"url": url, "txt": txt, "qNodes": q, "notifSig": notif, "progGridSig": grid}


_EMPTY = {"url": "", "txt": "", "qNodes": -1, "notifSig": "", "progGridSig": ""}


class _EnvMixin:
    def setUp(self):
        self._old_env = os.environ.pop(_ENV, None)
        drain()
        cta_trace._local.state = None

    def tearDown(self):
        os.environ.pop(_ENV, None)
        if self._old_env is not None:
            os.environ[_ENV] = self._old_env


class InterceptFlagTests(_EnvMixin, unittest.TestCase):
    def test_truthy_values(self):
        for raw in ("1", "true", "TRUE", "yes", "on", " On "):
            os.environ[_ENV] = raw
            self.assertTrue(cta_handler._cta_intercept_enabled(), raw)

    def test_falsy_values(self):
        for raw in ("", "0", "false", "off", "no", "2", "enabled"):
            os.environ[_ENV] = raw
            self.assertFalse(cta_handler._cta_intercept_enabled(), raw)

    def test_unset_is_off(self):
        self.assertFalse(cta_handler._cta_intercept_enabled())


class DidProgressTruthTable(unittest.TestCase):
    f = staticmethod(cta_handler._did_progress)

    def test_non_dict_is_false(self):
        self.assertFalse(self.f(None, _m()))
        self.assertFalse(self.f(_m(), None))
        self.assertFalse(self.f("x", "y"))

    def test_url_change_is_progress(self):
        self.assertTrue(self.f(_m(), _m(url="https://s.example/p2")))

    def test_no_change_is_no_progress(self):
        self.assertFalse(self.f(_m(), _m()))

    def test_each_dom_signal_is_progress(self):
        self.assertTrue(self.f(_m(), _m(txt="Q2")))
        self.assertTrue(self.f(_m(), _m(notif="error::x")))
        self.assertTrue(self.f(_m(), _m(q=4)))

    def test_progressive_grid_signal_needs_both_sides(self):
        self.assertTrue(self.f(_m(grid="1::a"), _m(grid="2::a")))
        # Une seule signature non vide : le garde-fou ne s'applique pas.
        self.assertFalse(self.f(_m(grid=""), _m(grid="2::a")))
        self.assertFalse(self.f(_m(grid="1::a"), _m(grid="")))

    def test_url_ignored_when_one_side_empty(self):
        self.assertFalse(self.f(_m(url=""), _m(url="https://s.example/p1")))

    # --- PIN : comportements actuels discutables (audit BDL-5, Q2) -----------

    def test_pin_both_empty_markers_return_true(self):
        # Frein anti-double-clic, PAS une preuve de progression (cas a).
        self.assertTrue(self.f(dict(_EMPTY), dict(_EMPTY)))

    def test_pin_after_marker_empty_only_returns_true(self):
        # Marqueur « après » vide (exception d'evaluate) alors que « avant » est
        # exploitable : txt diffère donc True, sans preuve (cas b).
        self.assertTrue(self.f(_m(), dict(_EMPTY)))

    def test_pin_zero_question_nodes_equals_minus_one(self):
        # int(x or -1) : qNodes == 0 est confondu avec -1 (cas c).
        self.assertFalse(self.f(_m(q=0), _m(q=-1)))
        self.assertFalse(self.f(_m(q=-1), _m(q=0)))
        # 0 -> 1 reste détecté.
        self.assertTrue(self.f(_m(q=0), _m(q=1)))


class DomProgressMarkerTests(unittest.TestCase):
    def _driver(self, value=None, raises=False):
        def evaluate(js):
            if raises:
                raise RuntimeError("context destroyed")
            return value
        return types.SimpleNamespace(evaluate=evaluate)

    def test_exception_gives_empty_marker(self):
        self.assertEqual(cta_handler._dom_progress_marker(self._driver(raises=True)), _EMPTY)

    def test_non_dict_gives_empty_marker(self):
        self.assertEqual(cta_handler._dom_progress_marker(self._driver(value="x")), _EMPTY)

    def test_dict_passthrough(self):
        marker = _m()
        self.assertEqual(cta_handler._dom_progress_marker(self._driver(value=marker)), marker)

    def test_frame_context_is_used_when_present(self):
        seen = []
        frame = types.SimpleNamespace(evaluate=lambda js: seen.append("frame") or _m())
        driver = types.SimpleNamespace(evaluate=lambda js: seen.append("root") or _m(), _current_frame=frame)
        cta_handler._dom_progress_marker(driver)
        self.assertEqual(seen, ["frame"])


class _Scenario:
    """Pilote le mode normal : marqueur avant/après selon le nombre de clics."""

    def __init__(self, after_by_press):
        self.presses = 0
        self.after_by_press = after_by_press  # {1: marker, 2: marker}
        self.before = _m()

    def marker(self, driver):
        if self.presses == 0:
            return dict(self.before)
        return dict(self.after_by_press.get(self.presses, self.before))

    def press(self, driver, el):
        self.presses += 1
        return True, True

    def wait(self, driver, el, before, timeout_s=5.0):
        return self.marker(driver), False, "stable"


class _El:
    def __init__(self, evaluate_raises=False):
        self.evaluate_calls = 0
        self._raises = evaluate_raises

    def evaluate(self, *a, **k):
        self.evaluate_calls += 1
        if self._raises:
            raise RuntimeError("dispatch failed")
        return True


def _driver(page=None):
    page = page or {"url": "https://s.example/p1", "doc": "1"}
    return types.SimpleNamespace(evaluate=lambda js: page)


class NormalModeTests(_EnvMixin, unittest.TestCase):
    def _run(self, sc):
        with mock.patch.object(cta_handler, "_dom_progress_marker", sc.marker), \
             mock.patch.object(cta_handler, "_press_click_release", sc.press), \
             mock.patch.object(cta_handler, "_wait_post_click_stabilization", sc.wait):
            return _orig("_click_with_intercept")(_driver(), _El())

    def test_progress_after_first_click_stops_at_one_press(self):
        sc = _Scenario({1: _m(url="https://s.example/p2")})
        self.assertIs(self._run(sc), True)
        self.assertEqual(sc.presses, 1)

    def test_progress_after_second_click(self):
        sc = _Scenario({1: _m(), 2: _m(txt="Q2")})
        self.assertIs(self._run(sc), True)
        self.assertEqual(sc.presses, 2)

    def test_no_progress_budget_is_exactly_two_presses(self):
        sc = _Scenario({})
        self.assertIs(self._run(sc), False)
        self.assertEqual(sc.presses, 2)  # 1 clic + 1 re-clic maximum

    def test_pin_empty_markers_stop_after_one_press(self):
        # Conséquence du cas (a) : pas de 2e clic quand les marqueurs sont vides.
        sc = _Scenario({1: dict(_EMPTY)})
        sc.before = dict(_EMPTY)
        self.assertIs(self._run(sc), True)
        self.assertEqual(sc.presses, 1)

    def test_press_receives_driver_and_element(self):
        seen = []
        sc = _Scenario({1: _m(txt="Q2")})
        drv, el = _driver(), _El()
        with mock.patch.object(cta_handler, "_dom_progress_marker", sc.marker), \
             mock.patch.object(cta_handler, "_press_click_release",
                               lambda d, e: seen.append((d, e)) or sc.press(d, e)), \
             mock.patch.object(cta_handler, "_wait_post_click_stabilization", sc.wait):
            _orig("_click_with_intercept")(drv, el)
        self.assertEqual(seen, [(drv, el)])


class InterceptModeTests(_EnvMixin, unittest.TestCase):
    """CTA_INTERCEPT_ONLY : aucun clic réel, aucun marqueur, résultat = rapport."""

    def setUp(self):
        super().setUp()
        os.environ[_ENV] = "1"
        self.calls = []

    def _run(self, *, arm=True, probe=None, report=None, el=None, arm_raises=False):
        probe = {"hasState": True, "installed": True, "armed": True, "armedOk": True} if probe is None else probe

        def arm_fn(driver):
            if arm_raises:
                raise RuntimeError("arm failed")
            return arm

        patches = [
            mock.patch.object(cta_handler, "arm_interceptor", arm_fn),
            mock.patch.object(cta_handler, "_probe_interceptor_state", lambda d: probe),
            mock.patch.object(cta_handler, "disarm_interceptor", lambda d: self.calls.append("disarm") or True),
            mock.patch.object(cta_handler, "read_intercept_report", lambda d: report),
            mock.patch.object(cta_handler, "_read_arm_error", lambda d: ""),
            mock.patch.object(cta_handler, "_press_click_release", lambda d, e: self.calls.append("press") or (True, True)),
            mock.patch.object(cta_handler, "_dom_progress_marker", lambda d: self.calls.append("marker") or _m()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        el = el or _El()
        self.el = el
        return _orig("_click_with_intercept")(_driver(), el)

    def test_captured_and_prevented_is_ok_without_real_click_or_marker(self):
        out = self._run(report={"clickCaptured": True, "prevented": True})
        self.assertIs(out, True)
        self.assertNotIn("press", self.calls)
        self.assertNotIn("marker", self.calls)
        self.assertEqual(self.calls.count("disarm"), 1)

    def test_captured_but_not_prevented_is_impossible(self):
        out = self._run(report={"clickCaptured": True, "prevented": False})
        self.assertIs(out, False)
        self.assertEqual(self.calls.count("disarm"), 1)

    def test_not_captured_is_impossible(self):
        out = self._run(report={"clickCaptured": False, "prevented": False})
        self.assertIs(out, False)
        self.assertNotIn("press", self.calls)

    def test_probe_not_armed_means_no_dispatch(self):
        el = _El()
        out = self._run(probe={"hasState": True, "installed": True, "armed": False, "armedOk": True}, el=el)
        self.assertIs(out, False)
        self.assertEqual(el.evaluate_calls, 0)  # aucun événement envoyé à la cible
        self.assertEqual(self.calls.count("disarm"), 1)

    def test_arm_return_value_is_not_trusted_only_probe_counts(self):
        # arm_interceptor peut retourner False alors que le probe dit armé : on poursuit.
        out = self._run(arm=False, report={"clickCaptured": True, "prevented": True})
        self.assertIs(out, True)
        # Et inversement : arm True mais probe non armé => impossible.
        out = self._run(arm=True, probe={"hasState": False})
        self.assertIs(out, False)

    def test_arm_exception_is_absorbed(self):
        out = self._run(arm_raises=True, probe={"hasState": False})
        self.assertIs(out, False)

    def test_dispatch_error_is_impossible_and_disarms(self):
        out = self._run(el=_El(evaluate_raises=True), report={"clickCaptured": True, "prevented": True})
        self.assertIs(out, False)
        self.assertEqual(self.calls.count("disarm"), 1)


class WrapperTransparencyOnRealFunction(_EnvMixin, unittest.TestCase):
    """L'enveloppe BDL ne change rien au comportement de la vraie fonction."""

    def _normal(self, wrap):
        sc = _Scenario({1: _m(), 2: _m(txt="Q2")})
        with mock.patch.object(cta_handler, "_dom_progress_marker", sc.marker), \
             mock.patch.object(cta_handler, "_press_click_release", sc.press), \
             mock.patch.object(cta_handler, "_wait_post_click_stabilization", sc.wait):
            fn = _orig("_click_with_intercept")
            if wrap:
                fn = build_observed_click(fn, cta_handler)
            return fn(_driver(), _El()), sc.presses

    def test_normal_mode_same_result_and_same_clicks(self):
        self.assertEqual(self._normal(wrap=False), self._normal(wrap=True))
        drain()
        self._normal(wrap=True)
        (ev,) = drain()
        self.assertEqual(ev["kind"], "click")
        self.assertEqual(ev["mode"], "normal")
        self.assertTrue(ev["returned"])

    def test_intercept_mode_same_result(self):
        os.environ[_ENV] = "1"
        report = {"clickCaptured": True, "prevented": True, "submitCaptured": False, "navigationBlocked": False}
        results = []
        for wrap in (False, True):
            with mock.patch.object(cta_handler, "arm_interceptor", lambda d: True), \
                 mock.patch.object(cta_handler, "_probe_interceptor_state",
                                   lambda d: {"hasState": True, "installed": True, "armed": True, "armedOk": True}), \
                 mock.patch.object(cta_handler, "disarm_interceptor", lambda d: True), \
                 mock.patch.object(cta_handler, "read_intercept_report", lambda d: report), \
                 mock.patch.object(cta_handler, "_read_arm_error", lambda d: ""):
                fn = _orig("_click_with_intercept")
                if wrap:
                    fn = build_observed_click(fn, cta_handler)
                results.append(fn(_driver(), _El()))
        self.assertEqual(results, [True, True])
        events = drain()
        self.assertEqual(events[-1]["mode"], "intercept")
        self.assertTrue(events[-1]["intercept"]["clickCaptured"])

    def test_exception_of_real_function_is_propagated(self):
        def boom(driver):
            raise RuntimeError("marker boom")

        with mock.patch.object(cta_handler, "_dom_progress_marker", boom):
            wrapped = build_observed_click(_orig("_click_with_intercept"), cta_handler)
            with self.assertRaises(RuntimeError):
                wrapped(_driver(), _El())


class StructuralGuards(unittest.TestCase):
    def test_click_with_intercept_signature_is_stable(self):
        sig = inspect.signature(_orig("_click_with_intercept"))
        self.assertEqual(list(sig.parameters), ["driver", "el"])

    def test_entry_functions_exist_and_are_callable(self):
        for name in ENTRY_FUNCTIONS:
            self.assertTrue(callable(getattr(cta_handler, name, None)), name)

    def test_public_functions_resolve_click_by_module_global(self):
        # Condition pour que l'enveloppe posée sur le module couvre tous les appels internes.
        self.assertIs(cta_handler.click_primary_cta.__globals__, vars(cta_handler))
        self.assertIn("_click_with_intercept", vars(cta_handler))

    def test_cta_handler_has_no_reverse_dependency_on_bdl_modules(self):
        # Principe BDL-5 : cta_handler.py reste inchangé et ignore la couche d'observation.
        # On inspecte les instructions d'import (AST), pas le texte : page_snapshot est
        # légitimement cité dans un commentaire du module.
        import ast

        path = inspect.getsourcefile(cta_handler)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        forbidden = ("cta_trace", "page_snapshot", "extraction_rule_trace")
        offenders = sorted(m for m in imported if any(f in m for f in forbidden))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
