import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "surveybot"))

import Survey
from Survey import action_dispatcher as ad


class _FakeDriver:
    current_url = "https://example.test/survey"


def test_execute_action_prioritizes_matrix_row_col_before_target(monkeypatch):
    driver = _FakeDriver()

    monkeypatch.setattr(ad, "_split_multiline_instruction", lambda instr: [instr])
    monkeypatch.setattr(
        ad,
        "_parse_action_line",
        lambda _raw: {
            "qid": "Q1",
            "target_id": "group_matrix_target",
            "value": "Transféré vers Revolut",
            "itype": "matrix",
            "context": "Crédit consommation",
        },
    )

    monkeypatch.setattr(ad, "_apply_by_target_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("target fallback should not run first")))

    fake_input_handler = types.SimpleNamespace(
        _looks_like_matrix=lambda _driver: True,
        click_matrix_cell_by_row_and_col=lambda _driver, row_label, col_label: row_label == "Crédit consommation" and col_label == "Transféré vers Revolut",
    )
    fake_context_mapper = types.SimpleNamespace(
        try_click_matrix_by_visual_mapping=lambda *_args, **_kwargs: False,
    )
    fake_dropdown = types.SimpleNamespace(
        try_resolve_dropdown_block=lambda *_args, **_kwargs: False,
    )

    monkeypatch.setitem(sys.modules, "Survey.input_handler", fake_input_handler)
    monkeypatch.setattr(Survey, "input_handler", fake_input_handler, raising=False)
    monkeypatch.setitem(sys.modules, "Survey.dom_context_mapper", fake_context_mapper)
    monkeypatch.setitem(sys.modules, "Survey.dropdown_block_resolver", fake_dropdown)

    assert ad.execute_action(driver, "ignored") is True


def test_execute_action_aborts_matrix_when_row_is_missing(monkeypatch):
    driver = _FakeDriver()

    monkeypatch.setattr(ad, "_split_multiline_instruction", lambda instr: [instr])
    monkeypatch.setattr(
        ad,
        "_parse_action_line",
        lambda _raw: {
            "qid": "Q1",
            "target_id": "group_matrix_target",
            "value": "Transféré vers Revolut",
            "itype": "matrix",
            "context": "",
        },
    )
    monkeypatch.setattr(ad, "_apply_by_target_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not fallback blindly")))

    assert ad.execute_action(driver, "ignored") is False
