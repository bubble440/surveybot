from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, text="", attrs=None, by_selector=None, by_xpath=None, xpath=""):
        self.text = text
        self._attrs = attrs or {}
        self._by_selector = by_selector or {}
        self._by_xpath = by_xpath or {}
        self.xpath = xpath

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        key = value or ""
        if key in self._by_xpath:
            return list(self._by_xpath[key])
        return list(self._by_selector.get(key, []))

    def find_element(self, by=None, value=None):
        key = value or ""
        if key in self._by_xpath and self._by_xpath[key]:
            return self._by_xpath[key][0]
        if key in self._by_selector and self._by_selector[key]:
            return self._by_selector[key][0]
        raise Exception("not found")


class _FakeDriver:
    def __init__(self, by_selector=None):
        self._by_selector = by_selector or {}

    def find_elements(self, by=None, value=None):
        return list(self._by_selector.get(value or "", []))


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
        "_extract_confirmit_slider_grid_blocks",
        "_extract_areyounet_matrix_blocks",
        "_extract_areyounet_switch_radio_blocks",
        "_extract_areyounet_switch_checkbox_blocks",
        "_extract_cloudresearch_sentry_blocks",
        "_extract_custom_testid_single_select_radio_blocks",
        "_extract_runtime_answerrow_radio_blocks",
        "_extract_label_radio_list_blocks",
        "_extract_qualtrics_choice_structure_radio_blocks",
        "_extract_custom_testid_multi_select_checkbox_blocks",
        "_extract_single_consent_checkbox_block",
        "_extract_consent_modal_radio_block",
        "_extract_ps_select_dropdown_blocks",
        "_extract_purespectrum_date_dropdown_blocks",
        "_extract_purespectrum_mobile_date_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_extracts_kantar_rowpicker_when_container_suffix_is_short(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: el.xpath)

    card_clickable_a = _FakeElement(xpath="//div[@id='rowpicker']//div[@tabindex='0'][1]")
    card_clickable_b = _FakeElement(xpath="//div[@id='rowpicker']//div[@tabindex='0'][2]")

    card_a = _FakeElement(
        by_selector={
            "div[tabindex='0']": [card_clickable_a],
            "label span": [_FakeElement(text="Un homme")],
        }
    )
    card_b = _FakeElement(
        by_selector={
            "div[tabindex='0']": [card_clickable_b],
            "label span": [_FakeElement(text="Une femme")],
        }
    )

    container = _FakeElement(attrs={"id": "container_S1"})
    picker = _FakeElement(
        by_selector={"div.__flexgrid_row > div": [card_a, card_b]},
        by_xpath={"ancestor::div[starts-with(@id,'container_')][1]": [container]},
    )

    driver = _FakeDriver(
        by_selector={
            "div[id^='container_'] [data-test='main-contain']._rowpicker": [picker],
            "#qc_S1 span.mrQuestionText": [],
            ".questionContainer[questionname$='.S1'] span.mrQuestionText": [
                _FakeElement(text="Êtes-vous... ?")
            ],
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)": [],
            "button, a[role='button'], [role='button'], .sq-cardrating-button": [],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "radio"
    assert "tes-vous" in block["question"].lower()
    assert block["options"] == ["Un homme", "Une femme"]
    assert (block.get("context") or {}).get("kantar_rowpicker_radio") is True
    assert (block.get("context") or {}).get("group_key") == "kantar_rowpicker:radio:S1"
