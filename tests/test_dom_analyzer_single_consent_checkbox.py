from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, text="", attrs=None, by_selector=None, by_xpath=None):
        self.text = text
        self._attrs = attrs or {}
        self._by_selector = by_selector or {}
        self._by_xpath = by_xpath or {}

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

    def find_element(self, by=None, value=None):
        items = self.find_elements(by=by, value=value)
        if items:
            return items[0]
        raise Exception("not found")


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
        "_extract_custom_testid_single_select_radio_blocks",
        "_extract_custom_testid_multi_select_checkbox_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_extracts_single_consent_checkbox_block_with_accept_cta(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    label = _FakeElement(
        text="J'ai lu et j'accepte la Politique de confidentialité",
    )
    consent_container = _FakeElement(
        text="Vous pouvez accéder à cette enquête en acceptant notre Politique de confidentialité.",
    )
    checkbox = _FakeElement(
        attrs={"id": "consentCheckbox1f", "name": "consentContainer:consentCheckbox", "type": "checkbox"},
        by_xpath={
            "ancestor::*[@id='consentContainer25' or contains(@id,'consentContainer') or contains(@class,'privacy-policy')][1]": [consent_container],
            "ancestor::label[1]": [label],
        },
    )
    cta = _FakeElement(text="Accepter et commencer", attrs={"id": "acceptAndTakeSurveyLink20"})

    driver = _FakeDriver(
        by_selector={
            "#consentContainer25 input[type='checkbox'], [id*='consentContainer'] input[type='checkbox'], .river-sampling-privacy-policy input[type='checkbox'], input[type='checkbox'][id*='consentCheckbox'], input[type='checkbox'][name*='consentCheckbox'], input[type='checkbox'][name*='consentContainer']": [checkbox],
            "a[id*='acceptAndTakeSurveyLink'], button[id*='acceptAndTakeSurveyLink'], a.btn-primary, button.btn-primary": [cta],
            "label[for='consentCheckbox1f']": [label],
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)": [],
            "button, a[role='button'], [role='button'], .sq-cardrating-button": [],
            "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']": [],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "checkbox"
    assert block["max_select"] == 1
    assert len(block["options"]) == 1
    assert "j'ai lu et j'accepte" in block["options"][0].lower()
    assert "politique de confidential" in block["question"].lower()
    assert (block.get("context") or {}).get("single_consent_checkbox") is True
