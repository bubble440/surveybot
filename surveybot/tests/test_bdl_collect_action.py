from __future__ import annotations

"""Tests synthétiques BDL-2b : mode collecte des plans d'actions jugés PASS.

Aucun navigateur : le dispatcher, le validator et les primitives de capture
(browser_capsule) sont remplacés par des doubles. On teste le hook réel
(page_snapshot._install_action_observer) et le recorder réel
(failure_recorder.record_collected_action).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import Survey.action_dispatcher as action_dispatcher
import Survey.failure_recorder as failure_recorder
import Survey.page_snapshot as page_snapshot

import inspect

# Capturée à l'import, avant que les tests ne remplacent record_validation_failure.
_FAILURE_SIGNATURE = list(inspect.signature(failure_recorder.record_validation_failure).parameters)

_PASS_REPORT = {"ok": True, "stage": "action", "issues": []}
_FAIL_REPORT = {
    "ok": False,
    "stage": "action",
    "issues": [{"failure_type": "dispatcher_false_negative", "target_id": "t1"}],
}
_BEFORE = {"elements_count": 1, "budget": {}, "facts": {"t1": {"checked": False}}}
_AFTER = {"elements_count": 1, "budget": {}, "facts": {"t1": {"checked": True}}}
_ACTIONS = [{"target_id": "t1", "itype": "radio", "value": "A", "qid": "q1"}]


class FakeDriver:
    url = "https://example.test/survey"


class BdlCollectActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "bdl_collect"

        # État de processus du recorder isolé entre tests.
        failure_recorder._collect_count_by_root.clear()
        failure_recorder._collect_cap_logged.clear()

        # Le hook réel est installé sur un faux dispatcher ; restauré en fin de test.
        self._orig_exec = action_dispatcher.execute_actions_plan
        self.addCleanup(setattr, action_dispatcher, "execute_actions_plan", self._orig_exec)
        self.dispatch_result = True

        def fake_dispatch(driver, actions, *args, **kwargs):
            return self.dispatch_result

        action_dispatcher.execute_actions_plan = fake_dispatch

        self.report = dict(_PASS_REPORT)
        self.capture = Mock(side_effect=[_BEFORE, _AFTER])
        self.install_obs = Mock(return_value=True)
        self.collect_obs = Mock(return_value={"mutations": [], "truncated": False, "observer_present": True})
        self.record_failure = Mock(return_value="/fake/snapshot")

        patches = [
            patch.dict(os.environ, {"SURVEY_BDL_COLLECT_DIR": str(self.root)}, clear=False),
            # Décorateur de l'observateur de fix externes neutralisé (hors périmètre).
            patch("Survey.action_fix_hook.observe_action_fix_successes", lambda: (lambda f: f)),
            patch("Survey.action_validator.validate_actions", side_effect=lambda *a, **k: dict(self.report)),
            patch("Survey.page_snapshot._capture_current_outer_html", return_value="<html></html>"),
            patch("Survey.browser_capsule.capture_runtime_state", self.capture),
            patch("Survey.browser_capsule.install_mutation_observer", self.install_obs),
            patch("Survey.browser_capsule.collect_mutation_observer", self.collect_obs),
            patch("Survey.failure_recorder.record_validation_failure", self.record_failure),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        page_snapshot._install_action_observer()
        self.observed = action_dispatcher.execute_actions_plan

    def _set_flag(self, value: str | None) -> None:
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        if value is None:
            os.environ.pop("SURVEY_BDL_COLLECT", None)
        else:
            os.environ["SURVEY_BDL_COLLECT"] = value

    def _collected_dirs(self) -> list[Path]:
        return sorted(p for p in self.root.iterdir() if p.is_dir()) if self.root.is_dir() else []

    # ── 1. flag désactivé : comportement strictement identique ───────────────

    def test_flag_off_writes_nothing_and_adds_no_capture(self) -> None:
        self._set_flag(None)
        result = self.observed(FakeDriver(), _ACTIONS)

        self.assertTrue(result)
        self.assertFalse(self.root.exists())
        # Seule la capture "avant" historique a lieu ; rien de plus.
        self.assertEqual(self.capture.call_count, 1)
        self.collect_obs.assert_not_called()
        self.record_failure.assert_not_called()

    def test_flag_explicitly_zero_is_off(self) -> None:
        self._set_flag("0")
        self.observed(FakeDriver(), _ACTIONS)
        self.assertFalse(self.root.exists())

    # ── 2. flag activé : faits avant/après persistés pour un PASS ────────────

    def test_flag_on_persists_before_after_facts_for_pass_plan(self) -> None:
        self._set_flag("1")
        result = self.observed(FakeDriver(), _ACTIONS)

        self.assertTrue(result)  # valeur de retour du plan inchangée
        dirs = self._collected_dirs()
        self.assertEqual(len(dirs), 1)
        names = {p.name for p in dirs[0].iterdir()}
        self.assertEqual(names, {"meta.json", "pass_report.json", "action_trace.json"})

        meta = json.loads((dirs[0] / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["kind"], failure_recorder.BDL_COLLECT_KIND)
        self.assertEqual(meta["stage"], "action")
        self.assertTrue(meta["validator_ok"])
        self.assertEqual(meta["url"], "https://example.test/survey")

        trace = json.loads((dirs[0] / "action_trace.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["before"], _BEFORE)
        self.assertEqual(trace["after"], _AFTER)
        self.assertEqual(trace["requested_actions"][0]["target_id"], "t1")
        # Captures lourdes réservées aux échecs : jamais de DOM ni de screenshot.
        self.assertFalse(any(n.endswith((".html", ".png", ".mhtml")) for n in names))
        self.record_failure.assert_not_called()

    def test_pass_plan_without_after_facts_is_skipped_and_cap_not_consumed(self) -> None:
        self._set_flag("1")
        self.capture.side_effect = [_BEFORE, None]  # "après" indisponible
        self.observed(FakeDriver(), _ACTIONS)

        self.assertEqual(self._collected_dirs(), [])
        self.assertEqual(failure_recorder._collect_count_by_root.get(str(self.root), 0), 0)

    # ── 3. plafond respecté, abandon contrôlé, un seul log ───────────────────

    def test_cap_is_respected_with_single_info_log(self) -> None:
        self._set_flag("1")
        with patch.dict(os.environ, {"SURVEY_BDL_COLLECT_MAX": "2"}), \
                patch("Survey.failure_recorder.log_info") as log_info:
            self.capture.side_effect = [_BEFORE, _AFTER] * 4
            for _ in range(4):
                self.observed(FakeDriver(), _ACTIONS)

        self.assertEqual(len(self._collected_dirs()), 2)
        self.assertEqual(log_info.call_count, 1)  # "plafond atteint" loggé une seule fois

    def test_cap_counts_folders_already_present_after_restart(self) -> None:
        self._set_flag("1")
        (self.root / "old_1").mkdir(parents=True)
        (self.root / "old_2").mkdir()
        with patch.dict(os.environ, {"SURVEY_BDL_COLLECT_MAX": "2"}):
            self.observed(FakeDriver(), _ACTIONS)
        self.assertEqual(len(self._collected_dirs()), 2)  # aucun nouveau dossier

    def test_invalid_cap_falls_back_to_default(self) -> None:
        for raw in ("abc", "0", "-3", ""):
            with patch.dict(os.environ, {"SURVEY_BDL_COLLECT_MAX": raw}):
                self.assertEqual(failure_recorder._bdl_collect_max(), 200)

    # ── 4. erreurs de collecte absorbées ─────────────────────────────────────

    def test_capture_error_is_absorbed_and_result_unchanged(self) -> None:
        self._set_flag("1")
        self.capture.side_effect = [_BEFORE, RuntimeError("boom")]
        result = self.observed(FakeDriver(), _ACTIONS)

        self.assertTrue(result)
        self.assertEqual(self._collected_dirs(), [])

    def test_recorder_write_error_is_absorbed(self) -> None:
        # La racine de collecte est un FICHIER : mkdir échoue.
        blocker = Path(self._tmp.name) / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        with patch.dict(os.environ, {"SURVEY_BDL_COLLECT_DIR": str(blocker)}):
            out = failure_recorder.record_collected_action(
                FakeDriver(),
                report=_PASS_REPORT,
                actions=_ACTIONS,
                action_trace={"after": _AFTER},
                dispatcher_success=True,
            )
        self.assertIsNone(out)

    def test_dispatcher_result_false_is_preserved(self) -> None:
        self._set_flag("1")
        self.dispatch_result = False
        self.assertFalse(self.observed(FakeDriver(), _ACTIONS))

    # ── 5. jamais visible comme failure case ─────────────────────────────────

    def test_collected_case_is_isolated_from_failure_cases(self) -> None:
        self._set_flag("1")
        self.observed(FakeDriver(), _ACTIONS)
        folder = self._collected_dirs()[0]

        # Racine distincte de la racine des snapshots d'échec.
        self.assertNotEqual(self.root.resolve(), Path("./snapshots").resolve())
        # Aucun des motifs que l'Autofix reconnaît comme failure case.
        self.assertNotIn("validation_failure", folder.name)
        self.assertFalse((folder / "validation_report.json").exists())

        from Survey.autofix.failure_case_builder import _resolve_stage
        self.assertEqual(_resolve_stage(None, folder), "unknown")

    # ── 6. non-régression du chemin d'échec existant ─────────────────────────

    def test_failure_plan_keeps_existing_recording_and_skips_collection(self) -> None:
        self._set_flag("1")
        self.report = dict(_FAIL_REPORT)
        self.capture.side_effect = [_BEFORE, _AFTER]

        result = self.observed(FakeDriver(), _ACTIONS)

        self.assertTrue(result)
        self.record_failure.assert_called_once()
        kwargs = self.record_failure.call_args.kwargs
        self.assertEqual(kwargs["stage"], "action")
        self.assertEqual(kwargs["runtime_state"], _AFTER)
        self.assertEqual(kwargs["action_trace"]["before"], _BEFORE)
        # Pas de collecte pour un échec (déjà couvert par snapshots/).
        self.assertFalse(self.root.exists())

    def test_failure_plan_flag_off_is_identical(self) -> None:
        self._set_flag(None)
        self.report = dict(_FAIL_REPORT)
        self.observed(FakeDriver(), _ACTIONS)
        self.record_failure.assert_called_once()
        self.assertFalse(self.root.exists())

    def test_record_validation_failure_signature_untouched(self) -> None:
        self.assertEqual(
            _FAILURE_SIGNATURE,
            ["driver", "stage", "report", "question_blocks", "actions",
             "pre_action_dom", "runtime_state", "action_trace"],
        )


if __name__ == "__main__":
    unittest.main()
