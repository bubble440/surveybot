from surveybot.Survey import dom_analyzer as da


class _FakeNode:
    def __init__(self, text="", attrs=None, children=None):
        self.text = text
        self._attrs = attrs or {}
        self._children = children or {}
        self.tag_name = self._attrs.get("tag", "div")

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return list(self._children.get(value or "", []))

    def find_element(self, by=None, value=None):
        items = self.find_elements(by, value)
        if not items:
            raise Exception("not found")
        return items[0]


class _FakeDriver:
    def __init__(self):
        statement_7 = _FakeNode(text="Tarifs et prix", attrs={"id": "statement_7"})
        statement_2 = _FakeNode(text="Qualité du digital", attrs={"id": "statement_2"})
        statement_26 = _FakeNode(text="Autre - préciser", attrs={"id": "statement_26"})

        def _item(statement):
            return _FakeNode(
                attrs={"class": "customItem"},
                children={
                    ".customStatement": [statement],
                },
            )

        self.item_area = _FakeNode(
            attrs={"id": "itemArea"},
            children={
                ".customItem .customRank": [_FakeNode(attrs={"class": "customRank"})],
                ".customItem": [_item(statement_7), _item(statement_2), _item(statement_26)],
            },
        )

        self.question_h1 = _FakeNode(
            text="Pouvez vous préciser pourquoi vous n'avez pas donné une meilleure note à Revolut ?"
        )

        self.custom_other_input = _FakeNode(
            attrs={"id": "customOther_26", "type": "text", "name": "", "tag": "input"}
        )

    def find_elements(self, by=None, value=None):
        v = value or ""
        if v == "#customToolArea #itemArea":
            return [self.item_area]
        if v in {"#question_text_Q4", "h1.question-text", "h1"}:
            return [self.question_h1]
        if "input:not([type='radio'])" in v:
            return [self.custom_other_input]
        if "input[type='radio']" in v and "input[type='checkbox']" in v:
            return []
        if "button" in v and "a[role='button']" in v:
            return []
        return []

    def execute_script(self, script, *_args):
        if "maxNrAnswer" in script:
            return "var config = { maxNrAnswer : 3, minNrAnswer: 1 };"
        return False


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
        "_extract_runtime_answerrow_radio_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])



def test_decipher_clickable_ranking_extracted_as_choice_group(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: f"//*[@id='{el.get_attribute('id') or 'item'}']")

    driver = _FakeDriver()
    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "checkbox"
    assert block["max_select"] == 3
    assert len(block["options"]) == 3
    assert any("Tarifs" in opt for opt in block["options"])
