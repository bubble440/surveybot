from contextlib import contextmanager

from surveybot.Survey import dom_analyzer as da


class _FakeCard:
    def __init__(self, atmost):
        self._atmost = atmost

    def get_attribute(self, name):
        if name == "atmost":
            return str(self._atmost)
        return ""


class _FakeDriver:
    def __init__(self, atmost_values):
        self._cards = [_FakeCard(v) for v in atmost_values]

    def find_elements(self, by=None, value=None):
        if value == "li.sq-cardsort-card[atmost]":
            return list(self._cards)
        return []


@contextmanager
def _ok_chain(_driver, _chain):
    yield True


def _patch_basics(monkeypatch):
    monkeypatch.setattr(da, "clear_registry", lambda: None)
    monkeypatch.setattr(da, "_wait_for_survey_dom", lambda _driver: None)
    monkeypatch.setattr(da, "_select_best_frame_chain", lambda _driver, max_depth=2: ([], {}))
    monkeypatch.setattr(da, "switch_to_frame_chain", _ok_chain)
    monkeypatch.setattr(da, "extract_sliderpoints_question_blocks", lambda *_a, **_k: [])
    monkeypatch.setattr(da, "_extract_angular_material_radio_groups", lambda *_a, **_k: [])
    monkeypatch.setattr(da, "_extract_decipher_answers_list_fallback", lambda *_a, **_k: [])


def test_analyze_dom_skips_answers_list_for_cardsort_atmost_gt_one(monkeypatch):
    _patch_basics(monkeypatch)

    monkeypatch.setattr(
        da,
        "_analyze_dom_current_context",
        lambda *_a, **_k: [{"kind": "cardsort", "question": "Q", "target_id": "cardsort_x"}],
    )

    called = {"count": 0}

    def _answers_list(*_a, **_k):
        called["count"] += 1
        return [{"kind": "group", "itype": "checkbox", "question": "Apple", "options": ["A"]}]

    monkeypatch.setattr(da, "_extract_focusvision_answers_list_groups", _answers_list)

    blocks = da.analyze_dom(_FakeDriver(atmost_values=[13]))

    assert called["count"] == 0
    assert len(blocks) == 1
    assert blocks[0]["kind"] == "cardsort"


def test_analyze_dom_keeps_answers_list_for_cardsort_atmost_one(monkeypatch):
    _patch_basics(monkeypatch)

    monkeypatch.setattr(
        da,
        "_analyze_dom_current_context",
        lambda *_a, **_k: [{"kind": "cardsort", "question": "Q", "target_id": "cardsort_x"}],
    )

    called = {"count": 0}

    def _answers_list(*_a, **_k):
        called["count"] += 1
        return [{"kind": "group", "itype": "radio", "question": "Row", "options": ["A", "B"]}]

    monkeypatch.setattr(da, "_extract_focusvision_answers_list_groups", _answers_list)

    blocks = da.analyze_dom(_FakeDriver(atmost_values=[1]))

    assert called["count"] == 1
    assert len(blocks) == 1
    assert blocks[0]["kind"] == "group"
