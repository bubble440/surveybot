import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "surveybot"))

import Survey
from Survey import action_dispatcher as ad


class _FakeDriver:
    current_url = "https://example.test/survey"


def test_execute_action_uses_table_matrix_sge_strategy_before_generic_matrix(monkeypatch):
    driver = _FakeDriver()

    monkeypatch.setattr(ad, "_split_multiline_instruction", lambda instr: [instr])
    monkeypatch.setattr(
        ad,
        "_parse_action_line",
        lambda _raw: {
            "qid": "Q1",
            "target_id": "group_046fe0b2d616",
            "value": "Plutôt favorable",
            "itype": "matrix",
            "context": "Amazon Prime Video",
        },
    )

    monkeypatch.setattr(ad, "_try_gridclick_matrix_set", lambda *_args, **_kwargs: False)

    called = {"sge": 0}

    def _fake_sge(_driver, payload, row, col):
        called["sge"] += 1
        assert payload.get("table_matrix_sge") is True
        assert row == "Amazon Prime Video"
        assert col == "Plutôt favorable"
        return True

    monkeypatch.setattr(ad, "_try_table_matrix_sge_set", _fake_sge)

    fake_input_handler = types.SimpleNamespace(
        _looks_like_matrix=lambda _driver: (_ for _ in ()).throw(AssertionError("generic matrix handler must not run")),
        click_matrix_cell_by_row_and_col=lambda *_args, **_kwargs: False,
    )
    fake_context_mapper = types.SimpleNamespace(
        try_click_matrix_by_visual_mapping=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("visual mapping must not run")),
    )
    fake_dropdown = types.SimpleNamespace(
        try_resolve_dropdown_block=lambda *_args, **_kwargs: False,
    )

    monkeypatch.setitem(sys.modules, "Survey.input_handler", fake_input_handler)
    monkeypatch.setattr(Survey, "input_handler", fake_input_handler, raising=False)
    monkeypatch.setitem(sys.modules, "Survey.dom_context_mapper", fake_context_mapper)
    monkeypatch.setitem(sys.modules, "Survey.dropdown_block_resolver", fake_dropdown)
    monkeypatch.setattr(ad, "get_target", lambda _tid: {"itype": "matrix", "table_matrix_sge": True})

    assert ad.execute_action(driver, "ignored") is True
    assert called["sge"] == 1
