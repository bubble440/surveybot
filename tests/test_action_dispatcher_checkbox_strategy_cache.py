import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "surveybot"))

import Survey
from Survey import action_dispatcher as ad


class _FakeDriver:
    current_url = "https://example.test/survey"


def _install_common_stubs(monkeypatch, input_handler):
    fake_context_mapper = types.SimpleNamespace(
        try_click_matrix_by_visual_mapping=lambda *_args, **_kwargs: False,
    )
    fake_dropdown = types.SimpleNamespace(
        try_resolve_dropdown_block=lambda *_args, **_kwargs: False,
    )
    monkeypatch.setitem(sys.modules, "Survey.input_handler", input_handler)
    monkeypatch.setattr(Survey, "input_handler", input_handler, raising=False)
    monkeypatch.setitem(sys.modules, "Survey.dom_context_mapper", fake_context_mapper)
    monkeypatch.setitem(sys.modules, "Survey.dropdown_block_resolver", fake_dropdown)


def test_checkbox_strategy_is_reused_for_same_target(monkeypatch):
    driver = _FakeDriver()
    calls = []

    fake_input_handler = types.SimpleNamespace(
        click_checkbox_by_label=lambda *_args, **_kwargs: calls.append("checkbox_main") or True,
        click_checkbox_buttonish_by_label=lambda *_args, **_kwargs: calls.append("checkbox_buttonish") or False,
        click_radio_by_label=lambda *_args, **_kwargs: calls.append("checkbox_fallback_radio") or False,
    )
    _install_common_stubs(monkeypatch, fake_input_handler)

    first = ad.execute_action(driver, "Q1 //// tid_block_1 //// Option A //// checkbox //// Question bloc")
    second = ad.execute_action(driver, "Q1 //// tid_block_1 //// Option B //// checkbox //// Question bloc")

    assert first is True
    assert second is True
    assert calls == ["checkbox_main", "checkbox_main"]


def test_checkbox_cached_strategy_falls_back_when_it_fails(monkeypatch):
    driver = _FakeDriver()
    calls = []
    main_results = iter([True, False])

    def _main(*_args, **_kwargs):
        calls.append("checkbox_main")
        return next(main_results)

    fake_input_handler = types.SimpleNamespace(
        click_checkbox_by_label=_main,
        click_checkbox_buttonish_by_label=lambda *_args, **_kwargs: calls.append("checkbox_buttonish") or True,
        click_radio_by_label=lambda *_args, **_kwargs: calls.append("checkbox_fallback_radio") or False,
    )
    _install_common_stubs(monkeypatch, fake_input_handler)

    first = ad.execute_action(driver, "Q1 //// tid_block_2 //// Option A //// checkbox //// Question bloc")
    second = ad.execute_action(driver, "Q1 //// tid_block_2 //// Option B //// checkbox //// Question bloc")

    assert first is True
    assert second is True
    assert calls == ["checkbox_main", "checkbox_main", "checkbox_buttonish"]
