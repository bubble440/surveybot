import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "surveybot"))

import Survey
from Survey import action_dispatcher as ad
from Survey import dom_registry


class _FakeDriver:
    current_url = "https://example.test/survey"


def _stub_parsing(monkeypatch, *, target_id="tid-1", value="Option A", itype="checkbox", context="Question"):
    monkeypatch.setattr(ad, "_split_multiline_instruction", lambda instr: [instr])
    monkeypatch.setattr(
        ad,
        "_parse_action_line",
        lambda _raw: {
            "qid": "Q1",
            "target_id": target_id,
            "value": value,
            "itype": itype,
            "context": context,
        },
    )


def _stub_dependencies(monkeypatch, input_handler):
    monkeypatch.setitem(sys.modules, "Survey.input_handler", input_handler)
    monkeypatch.setattr(Survey, "input_handler", input_handler, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "Survey.dom_context_mapper",
        types.SimpleNamespace(try_click_matrix_by_visual_mapping=lambda *_a, **_k: False),
    )
    monkeypatch.setitem(
        sys.modules,
        "Survey.dropdown_block_resolver",
        types.SimpleNamespace(try_resolve_dropdown_block=lambda *_a, **_k: False),
    )


def test_execute_action_reuses_cached_handler_for_same_target_id(monkeypatch):
    dom_registry.clear_registry()
    driver = _FakeDriver()

    _stub_parsing(monkeypatch)
    monkeypatch.setattr(ad, "_apply_by_target_id", lambda *_a, **_k: False)

    calls = {"main": 0, "buttonish": 0}

    def _main(*_a, **_k):
        calls["main"] += 1
        return False

    def _buttonish(*_a, **_k):
        calls["buttonish"] += 1
        return True

    fake_input_handler = types.SimpleNamespace(
        click_checkbox_by_label=_main,
        click_checkbox_buttonish_by_label=_buttonish,
        click_radio_by_label=lambda *_a, **_k: False,
    )
    _stub_dependencies(monkeypatch, fake_input_handler)

    assert ad.execute_action(driver, "ignored") is True
    assert ad.execute_action(driver, "ignored") is True

    # 1er passage: main + buttonish, puis cache sur buttonish
    # 2e passage: buttonish direct (pas de main)
    assert calls["main"] == 1
    assert calls["buttonish"] == 2
    assert dom_registry.get_cached_handler("tid-1") == "checkbox_buttonish"


def test_execute_action_invalidates_cached_handler_and_fallbacks(monkeypatch):
    dom_registry.clear_registry()
    driver = _FakeDriver()

    _stub_parsing(monkeypatch)
    monkeypatch.setattr(ad, "_apply_by_target_id", lambda *_a, **_k: False)

    phase = {"step": 0}
    calls = {"main": 0, "buttonish": 0}

    def _main(*_a, **_k):
        calls["main"] += 1
        # 1er passage: échec pour laisser buttonish réussir et se cacher.
        # 2e passage (après invalidation): succès.
        return phase["step"] >= 2

    def _buttonish(*_a, **_k):
        calls["buttonish"] += 1
        # 1er appel: succès pour alimenter le cache
        # 2e appel: échec -> invalidation -> fallback normal vers main
        phase["step"] += 1
        return phase["step"] == 1

    fake_input_handler = types.SimpleNamespace(
        click_checkbox_by_label=_main,
        click_checkbox_buttonish_by_label=_buttonish,
        click_radio_by_label=lambda *_a, **_k: False,
    )
    _stub_dependencies(monkeypatch, fake_input_handler)

    assert ad.execute_action(driver, "ignored") is True
    assert dom_registry.get_cached_handler("tid-1") == "checkbox_buttonish"

    assert ad.execute_action(driver, "ignored") is True

    # 2e passage: buttonish cache (échec) puis fallback main (succès)
    assert calls["buttonish"] == 2
    assert calls["main"] == 2
    assert dom_registry.get_cached_handler("tid-1") == "checkbox_main"


def test_clear_registry_also_clears_handler_cache():
    dom_registry.set_cached_handler("tid-42", "checkbox_main")
    assert dom_registry.get_cached_handler("tid-42") == "checkbox_main"

    dom_registry.clear_registry()

    assert dom_registry.get_cached_handler("tid-42") is None
