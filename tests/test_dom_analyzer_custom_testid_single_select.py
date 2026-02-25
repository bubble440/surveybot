from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, text="", attrs=None, by_selector=None, xpath=""):
        self.text = text
        self._attrs = attrs or {}
        self._by_selector = by_selector or {}
        self.xpath = xpath

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return list(self._by_selector.get(value or "", []))


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
        "_extract_areyounet_matrix_blocks",
        "_extract_areyounet_switch_radio_blocks",
        "_extract_areyounet_switch_checkbox_blocks",
        "_extract_cloudresearch_sentry_blocks",
        "_extract_purespectrum_mobile_date_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_analyze_dom_extracts_custom_testid_single_select(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: el.xpath)

    option_yes = _FakeElement(
        by_selector={
            "label[data-testid='answer-radio-label-radiotext']": [_FakeElement(text="J’accepte")],
            "label": [_FakeElement(text="J’accepte")],
        },
        xpath="//div[@data-testid='answer-radio-div-container'][1]",
    )
    option_no = _FakeElement(
        by_selector={
            "label[data-testid='answer-radio-label-radiotext']": [_FakeElement(text="Je n’accepte pas")],
            "label": [_FakeElement(text="Je n’accepte pas")],
        },
        xpath="//div[@data-testid='answer-radio-div-container'][2]",
    )

    container = _FakeElement(
        by_selector={
            "label[data-testid='common-question-label-text']": [
                _FakeElement(text="Acceptez-vous de ne pas partager ce contenu ?")
            ],
            "div[data-testid='answer-radio-div-container']": [option_yes, option_no],
            "[id][data-testid*='question-singleselect']": [_FakeElement(attrs={"id": "20526249"})],
        }
    )

    driver = _FakeDriver(
        by_selector={
            "div[data-testid='common-question-div-container']": [container],
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)": [],
            "button, a[role='button'], [role='button'], .sq-cardrating-button": [],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "radio"
    assert blocks[0]["options"] == ["J’accepte", "Je n’accepte pas"]
    assert (blocks[0].get("context") or {}).get("custom_testid_single_select") is True
    assert (blocks[0].get("context") or {}).get("group_key") == "custom_testid_single_select:radio:20526249"
