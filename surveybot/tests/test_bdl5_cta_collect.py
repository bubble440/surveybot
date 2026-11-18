from __future__ import annotations

"""Tests synthétiques BDL-5 v2 étape 1 : collecte des événements CTA.

Aucun navigateur. Vérifie : flag, liste blanche de champs, assainissement des
URLs, exclusion du mode intercept, plafond en octets, absence d'exception,
persistance à la création de l'événement par l'enveloppe, persistance des seuls
unobserved_path côté couverture, et transparence vis-à-vis du clic.
"""

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from Survey import cta_trace, failure_recorder as fr
from Survey.cta_trace import build_observed_click, build_observed_entry, drain

_EMPTY = {"url": "", "txt": "", "qNodes": -1, "notifSig": "", "progGridSig": ""}


def _marker(url="https://s.example/p1?sid=SECRET#frag", txt="Q1", q=3):
    return {"url": url, "txt": txt, "qNodes": q, "notifSig": "", "progGridSig": ""}


def _click_event(**over):
    ev = {
        "v": "1", "kind": "click", "fn": "_click_with_intercept", "caller": "click_primary_cta",
        "mode": "normal",
        "target": {"tag": "div", "id": "next_button", "name": "", "className": "btn", "text": "Suivant",
                   "secret": "NE_DOIT_PAS_ETRE_PERSISTE"},
        "returned": True, "raised": None, "marker_status": "ok",
        "signals_changed": ["url", "doc"],
        "marker_before": _marker(), "marker_after": _marker(url="https://s.example/p2?sid=SECRET", txt="Q2"),
        "page_before": {"url": "https://s.example/p1?a=1", "doc": "100.5"},
        "page_after": {"url": "https://s.example/p2?a=2", "doc": "200.5"},
        "intercept": None, "t_start": 1.0, "duration_ms": 42,
        "champ_inconnu": "ignoré",
    }
    ev.update(over)
    return ev


def _entry_event(**over):
    ev = {"v": "1", "kind": "entry", "fn": "try_click_qps_skip_to_survey", "outcome": "unobserved_path",
          "mode": "normal", "clicks_observed": 0, "t_start": 1.0, "duration_ms": 5}
    ev.update(over)
    return ev


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "cta"
        self._env = mock.patch.dict(os.environ, {
            "SURVEY_BDL_CTA_COLLECT": "1",
            "SURVEY_BDL_CTA_COLLECT_DIR": str(self.root),
        })
        self._env.start()
        os.environ.pop("SURVEY_BDL_CTA_COLLECT_MAX_BYTES", None)
        fr._cta_bytes_by_root.clear()
        fr._cta_file_by_root.clear()
        fr._cta_cap_logged.clear()
        drain()
        cta_trace._local.state = None

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def lines(self):
        out = []
        for f in sorted(self.root.glob("*.jsonl")) if self.root.is_dir() else []:
            out += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        return out


class FlagTests(_Base):
    def test_default_on_and_off_values(self):
        os.environ.pop("SURVEY_BDL_CTA_COLLECT")
        self.assertTrue(fr._bdl_cta_collect_enabled())
        for raw in ("0", "false", "off", "no", ""):
            os.environ["SURVEY_BDL_CTA_COLLECT"] = raw
            self.assertFalse(fr._bdl_cta_collect_enabled(), raw)

    def test_disabled_writes_nothing(self):
        os.environ["SURVEY_BDL_CTA_COLLECT"] = "0"
        self.assertFalse(fr.record_collected_cta(_click_event()))
        self.assertFalse(self.root.exists())


class LightEventTests(_Base):
    def test_click_whitelist_and_sanitized_urls(self):
        light = fr._light_cta_event(_click_event())
        self.assertNotIn("champ_inconnu", light)
        self.assertNotIn("intercept", light)
        self.assertNotIn("secret", light["target"])
        self.assertEqual(light["target"]["id"], "next_button")
        self.assertEqual(light["signals_changed"], ["url", "doc"])
        # Query et fragment jetés partout où une URL est stockée.
        self.assertEqual(light["marker_before"]["url"], "https://s.example/p1")
        self.assertEqual(light["marker_after"]["url"], "https://s.example/p2")
        self.assertEqual(light["page_before"]["url"], "https://s.example/p1")
        self.assertEqual(light["page_after"]["url"], "https://s.example/p2")
        self.assertEqual(light["page_after"]["doc"], "200.5")
        self.assertNotIn("SECRET", json.dumps(light))
        self.assertEqual(light["pid"], os.getpid())

    def test_long_strings_are_truncated(self):
        ev = _click_event(marker_after=_marker(txt="x" * 1000))
        self.assertEqual(len(fr._light_cta_event(ev)["marker_after"]["txt"]), 120)

    def test_intercept_mode_is_never_persisted(self):
        self.assertIsNone(fr._light_cta_event(_click_event(mode="intercept")))
        self.assertFalse(fr.record_collected_cta(_click_event(mode="intercept")))
        self.assertFalse(self.root.exists())

    def test_unknown_kind_and_non_dict_ignored(self):
        self.assertIsNone(fr._light_cta_event({"kind": "autre"}))
        self.assertIsNone(fr._light_cta_event(None))
        self.assertIsNone(fr._light_cta_event("x"))
        self.assertFalse(fr.record_collected_cta(None))

    def test_entry_has_no_url_and_no_target(self):
        light = fr._light_cta_event(_entry_event())
        self.assertEqual(light["outcome"], "unobserved_path")
        for forbidden in ("target", "page_before", "page_after", "marker_before", "marker_after"):
            self.assertNotIn(forbidden, light)

    def test_entry_with_unknown_mode_is_kept(self):
        self.assertIsNotNone(fr._light_cta_event(_entry_event(mode=None)))


class RecordTests(_Base):
    def test_one_json_line_per_event_in_one_file(self):
        self.assertTrue(fr.record_collected_cta(_click_event()))
        self.assertTrue(fr.record_collected_cta(_entry_event()))
        files = list(self.root.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0].name, r"^cta_events_\d{8}_\d{6}_\d+\.jsonl$")
        rows = self.lines()
        self.assertEqual([r["kind"] for r in rows], ["click", "entry"])

    def test_byte_cap_stops_collection_and_logs_once(self):
        os.environ["SURVEY_BDL_CTA_COLLECT_MAX_BYTES"] = "1500"
        results = [fr.record_collected_cta(_click_event()) for _ in range(10)]
        self.assertTrue(results[0])
        self.assertFalse(results[-1])
        total = sum(f.stat().st_size for f in self.root.glob("*.jsonl"))
        self.assertLessEqual(total, 1500)
        with mock.patch.object(fr, "log_info") as log_info:
            fr.record_collected_cta(_click_event())
            fr.record_collected_cta(_click_event())
        log_info.assert_not_called()  # déjà loggé une fois

    def test_cap_counts_files_already_on_disk(self):
        self.root.mkdir(parents=True)
        (self.root / "cta_events_old_1.jsonl").write_bytes(b"x" * 2000)
        os.environ["SURVEY_BDL_CTA_COLLECT_MAX_BYTES"] = "2100"
        self.assertFalse(fr.record_collected_cta(_click_event()))
        # Aucun fichier créé ni ajout au fichier existant.
        self.assertEqual([f.name for f in self.root.glob("*.jsonl")], ["cta_events_old_1.jsonl"])
        self.assertEqual((self.root / "cta_events_old_1.jsonl").stat().st_size, 2000)

    def test_invalid_cap_falls_back_to_default(self):
        for raw in ("abc", "0", "-5"):
            os.environ["SURVEY_BDL_CTA_COLLECT_MAX_BYTES"] = raw
            self.assertEqual(fr._bdl_cta_collect_max_bytes(), fr._BDL_CTA_DEFAULT_MAX_BYTES)

    def test_never_raises_on_unwritable_root(self):
        blocker = Path(self._tmp.name) / "file"
        blocker.write_text("x")
        os.environ["SURVEY_BDL_CTA_COLLECT_DIR"] = str(blocker / "sub")
        self.assertFalse(fr.record_collected_cta(_click_event()))

    def test_unserializable_value_does_not_raise(self):
        ev = _click_event(duration_ms=object(), target={"tag": object()})
        self.assertIsInstance(fr.record_collected_cta(ev), bool)  # ne lève jamais


def _handler(markers, pages):
    seq_m, seq_p = list(markers), list(pages)
    return types.SimpleNamespace(
        _cta_intercept_enabled=lambda: False,
        _resolve_ctx=lambda d: types.SimpleNamespace(evaluate=lambda js: seq_p.pop(0)),
        _dom_progress_marker=lambda d: seq_m.pop(0),
        read_intercept_report=lambda d: None,
    )


class EnvelopeCollectionTests(_Base):
    def test_click_is_persisted_at_creation_and_return_unchanged(self):
        h = _handler([_marker(), _marker(url="https://s.example/p2", txt="Q2")],
                     [{"url": "https://s.example/p1", "doc": "1"}, {"url": "https://s.example/p2", "doc": "1"}])
        click = build_observed_click(lambda d, el: True, h)
        el = types.SimpleNamespace(evaluate=lambda js: {"tag": "button", "id": "n", "name": "", "className": "", "text": "Suivant"})
        self.assertIs(click(object(), el), True)
        (row,) = self.lines()
        self.assertEqual(row["kind"], "click")
        self.assertEqual(row["marker_status"], "ok")
        self.assertIn("url", row["signals_changed"])
        self.assertEqual(row["target"]["id"], "n")

    def test_exception_of_clic_is_persisted_and_reraised(self):
        h = _handler([_marker(), _marker()], [{"url": "u", "doc": "1"}, {"url": "u", "doc": "1"}])

        def boom(d, el):
            raise ValueError("x")

        click = build_observed_click(boom, h)
        with self.assertRaises(ValueError):
            click(object(), types.SimpleNamespace(evaluate=lambda js: None))
        (row,) = self.lines()
        self.assertEqual(row["raised"], "ValueError")

    def test_intercept_mode_click_is_not_persisted(self):
        h = _handler([], [{"url": "u", "doc": "1"}])
        h._cta_intercept_enabled = lambda: True
        h.read_intercept_report = lambda d: {"clickCaptured": True, "prevented": True}
        click = build_observed_click(lambda d, el: True, h)
        self.assertIs(click(object(), types.SimpleNamespace(evaluate=lambda js: None)), True)
        self.assertEqual(self.lines(), [])
        self.assertEqual(len(drain()), 1)  # toujours tracé en mémoire (v1)

    def test_collection_failure_never_changes_the_click(self):
        h = _handler([_marker(), _marker()], [{"url": "u", "doc": "1"}, {"url": "u", "doc": "1"}])
        click = build_observed_click(lambda d, el: True, h)
        with mock.patch.object(fr, "record_collected_cta", side_effect=RuntimeError("disk")):
            self.assertIs(click(object(), types.SimpleNamespace(evaluate=lambda js: None)), True)

    def test_flag_off_keeps_memory_trace_only(self):
        os.environ["SURVEY_BDL_CTA_COLLECT"] = "0"
        h = _handler([_marker(), _marker()], [{"url": "u", "doc": "1"}, {"url": "u", "doc": "1"}])
        click = build_observed_click(lambda d, el: True, h)
        click(object(), types.SimpleNamespace(evaluate=lambda js: None))
        self.assertFalse(self.root.exists())
        self.assertEqual(len(drain()), 1)


class CoverageCollectionTests(_Base):
    def _entry(self, fn_result, name="try_click_qps_skip_to_survey"):
        h = types.SimpleNamespace(_cta_intercept_enabled=lambda: False)
        return build_observed_entry(lambda d: fn_result, name, h)

    def test_only_unobserved_path_is_persisted(self):
        self._entry(True)(object())          # unobserved_path -> persisté
        self._entry(False)(object())         # no_click -> compteur mémoire seulement
        rows = self.lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "entry")
        self.assertEqual(rows[0]["outcome"], "unobserved_path")
        self.assertEqual(cta_trace.coverage()["totals"]["no_click"], 1)

    def test_observed_call_is_not_persisted_as_entry(self):
        h = types.SimpleNamespace(_cta_intercept_enabled=lambda: False)

        def fn(d):
            cta_trace.note_cta_event({"kind": "click"})  # un clic tracé pendant l'appel
            return True

        build_observed_entry(fn, "click_primary_cta", h)(object())
        self.assertEqual(self.lines(), [])
        self.assertEqual(cta_trace.coverage()["totals"]["observed"], 1)

    def test_intercept_unobserved_path_is_not_persisted(self):
        h = types.SimpleNamespace(_cta_intercept_enabled=lambda: True)
        build_observed_entry(lambda d: True, "try_click_qps_skip_to_survey", h)(object())
        self.assertEqual(self.lines(), [])
        self.assertEqual(cta_trace.coverage()["totals"]["unobserved_path"], 1)


class ThreadDefaultsTests(unittest.TestCase):
    def test_default_roots_follow_run_env(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SURVEY_BDL_CTA_COLLECT_DIR", None)
            root = str(fr._bdl_cta_collect_root())
        self.assertTrue(root.endswith("bdl_collect_cta"))


if __name__ == "__main__":
    unittest.main()
