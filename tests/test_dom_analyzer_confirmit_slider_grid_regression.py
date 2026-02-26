from surveybot.Survey import dom_analyzer as da


class _FakeNode:
    def __init__(self, text="", attrs=None, selector_map=None, tag_name="div"):
        self.text = text
        self._attrs = attrs or {}
        self._selector_map = selector_map or {}
        self.tag_name = tag_name

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return list(self._selector_map.get(value or "", []))

    def find_element(self, by=None, value=None):
        items = self.find_elements(by, value)
        if not items:
            raise Exception("not found")
        return items[0]


class _FakeDriver:
    def __init__(self, slider_questions):
        self.slider_questions = slider_questions

    def find_elements(self, by=None, value=None):
        if value == ".cf-question.cf-question--slider-grid":
            return self.slider_questions
        if "button" in (value or "") and "a[role='button']" in (value or ""):
            return []
        return []


def _patch_non_generic_extractors(monkeypatch):
    for name in [
        "_extract_focusvision_cardsort_block",
        "_extract_walr_cardsort_block",
        "_extract_askandanswer_mobile_matrix_rows",
        "_extract_askandanswer_selection_list_questions",
        "_extract_rnw_ionicon_multi_choice_blocks",
        "_extract_cmix_simple_grid_question_blocks",
        "_extract_table_matrix_radio_rows",
        "_extract_cmix_radio_question_blocks",
        "_extract_ipsos_slider_question_blocks",
        "_extract_areyounet_matrix_blocks",
        "_extract_areyounet_switch_radio_blocks",
        "_extract_areyounet_switch_checkbox_blocks",
        "_extract_cloudresearch_sentry_blocks",
        "_extract_custom_testid_single_select_radio_blocks",
        "_extract_custom_testid_multi_select_checkbox_blocks",
        "_extract_single_consent_checkbox_block",
        "_extract_purespectrum_mobile_date_blocks",
        "_extract_focusvision_answers_list_groups",
        "_extract_decipher_answers_list_fallback",
        "extract_sliderpoints_question_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_confirmit_slider_grid_extracts_rows_as_blocks(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    monkeypatch.setattr(da, "_extract_angular_material_radio_groups", lambda *a, **k: [])

    scale_nodes = [
        _FakeNode(text="1 - Pas du tout d’accord"),
        _FakeNode(text="2"),
        _FakeNode(text="3"),
        _FakeNode(text="4"),
        _FakeNode(text="5 - Tout à fait d’accord"),
        _FakeNode(text="Ne s’applique pas à ma situation"),
    ]

    row1 = _FakeNode(
        attrs={"id": "Q9_10"},
        selector_map={
            ".cf-slider__handle[role='slider']": [_FakeNode()],
            ".cf-slider-grid-answer__text": [_FakeNode(text="Je ne me sens pas à l’aise...")],
        },
    )
    row2 = _FakeNode(
        attrs={"id": "Q9_9"},
        selector_map={
            ".cf-slider__handle[role='slider']": [_FakeNode()],
            ".cf-slider-grid-answer__text": [_FakeNode(text="Je n’ai jamais discuté...")],
        },
    )

    slider_question = _FakeNode(
        selector_map={
            ".cf-slider__handle[role='slider']": [_FakeNode()],
            ".cf-question__text": [_FakeNode(text="Dans quelle mesure êtes-vous d’accord ?")],
            ".cf-slider-grid-answer--fake-for-panel .cf-slider-grid-answer__scale-label": scale_nodes,
            ".cf-grid-layout__row.cf-slider-grid-answer[id]:not(.cf-slider-grid-answer--fake-for-panel)": [row1, row2],
        }
    )

    blocks = da._analyze_dom_current_context(_FakeDriver([slider_question]))

    assert len(blocks) == 2
    assert all(b["itype"] == "radio" for b in blocks)
    assert all(len(b["options"]) == 6 for b in blocks)
    assert "Dans quelle mesure" in blocks[0]["question"]
    assert "Je ne me sens pas" in blocks[0]["question"]
    assert "confirmit_slider_grid" in (blocks[0].get("context") or {})
