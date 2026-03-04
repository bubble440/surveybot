from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, text="", attrs=None, by_selector=None, by_xpath=None, displayed=True, tag_name="div"):
        self.text = text
        self._attrs = attrs or {}
        self._by_selector = by_selector or {}
        self._by_xpath = by_xpath or {}
        self._displayed = displayed
        self.tag_name = tag_name

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

    def is_displayed(self):
        return bool(self._displayed)


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
        "_extract_single_consent_checkbox_block",
        "_extract_runtime_answerrow_radio_blocks",
        "_extract_decipher_clickable_ranking_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_extracts_consent_modal_radio_block(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    radio_accept = _FakeElement(attrs={"id": "consent-radio-accept", "name": "consent", "type": "radio"}, tag_name="input")
    radio_reject = _FakeElement(attrs={"id": "consent-radio-reject", "name": "consent", "type": "radio"}, tag_name="input")

    label_accept_text = _FakeElement(text="JE CONSENS et continue l'enquête")
    label_reject_text = _FakeElement(text="JE NE CONSENS PAS et quitte l'enquête")
    cta_confirm = _FakeElement(text="Confirmez", attrs={"id": "consent-button-confirm"})
    question_hint = _FakeElement(text="Merci de répondre à cette question")

    driver = _FakeDriver(
        by_selector={
            "#modal-container": [_FakeElement()],
            ".consent-form-radiogroup": [_FakeElement()],
            ".consent-form-radiogroup input[type='radio'][name]": [radio_accept, radio_reject],
            "#consent-button-confirm": [cta_confirm],
            "label[for='consent-radio-accept'] .consent-option-text": [label_accept_text],
            "label[for='consent-radio-reject'] .consent-option-text": [label_reject_text],
            "#consent-error-message-container": [question_hint],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "radio"
    assert block["max_select"] == 1
    assert "merci de re" in block["question"].lower()
    assert any("je consens" in opt.lower() for opt in block["options"])
    assert any("je ne consens pas" in opt.lower() for opt in block["options"])
    assert (block.get("context") or {}).get("consent_modal_radio") is True



def test_extracts_consent_modal_radio_block_without_label_for_attribute(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    accept_label = _FakeElement(text="JE CONSENS et continue l'enquête")
    reject_label = _FakeElement(text="JE NE CONSENS PAS et quitte l'enquête")

    radio_accept = _FakeElement(
        attrs={"id": "consent-radio-accept", "name": "consent", "type": "radio"},
        by_xpath={"ancestor::label[contains(@class,'consent-option-label')][1]": [accept_label]},
        tag_name="input",
    )
    radio_reject = _FakeElement(
        attrs={"id": "consent-radio-reject", "name": "consent", "type": "radio"},
        by_xpath={"ancestor::label[contains(@class,'consent-option-label')][1]": [reject_label]},
        tag_name="input",
    )

    driver = _FakeDriver(
        by_selector={
            "#modal-container": [_FakeElement()],
            ".consent-form-radiogroup": [_FakeElement()],
            ".consent-form-radiogroup input[type='radio'][name]": [radio_accept, radio_reject],
            "#consent-button-confirm": [_FakeElement(text="Confirmez")],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    block = blocks[0]
    assert any("je consens" in opt.lower() for opt in block["options"])
    assert any("je ne consens pas" in opt.lower() for opt in block["options"])


def test_ignores_hidden_stale_consent_modal(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    radio_accept = _FakeElement(attrs={"id": "consent-radio-accept", "name": "consent", "type": "radio"}, displayed=False, tag_name="input")
    radio_reject = _FakeElement(attrs={"id": "consent-radio-reject", "name": "consent", "type": "radio"}, displayed=False, tag_name="input")

    driver = _FakeDriver(
        by_selector={
            "#modal-container": [_FakeElement(displayed=False)],
            ".consent-form-radiogroup": [_FakeElement(displayed=False)],
            ".consent-form-radiogroup input[type='radio'][name]": [radio_accept, radio_reject],
            "#consent-button-confirm": [_FakeElement(text="Confirmez", displayed=False)],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert blocks == []
