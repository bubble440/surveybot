from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, text="", attrs=None, by_selector=None):
        self.text = text
        self._attrs = attrs or {}
        self._by_selector = by_selector or {}

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
        "_extract_confirmit_slider_grid_blocks",
        "_extract_areyounet_matrix_blocks",
        "_extract_areyounet_switch_radio_blocks",
        "_extract_areyounet_switch_checkbox_blocks",
        "_extract_cloudresearch_sentry_blocks",
        "_extract_custom_testid_single_select_radio_blocks",
        "_extract_runtime_answerrow_radio_blocks",
        "_extract_custom_testid_multi_select_checkbox_blocks",
        "_extract_single_consent_checkbox_block",
        "_extract_purespectrum_mobile_date_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_extracts_qualtrics_choice_structure_radios(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    q_text = "Sélectionnez votre niveau d'étude le plus élevé."

    radio_1 = _FakeElement(attrs={"name": "QR~QID261", "id": "QR~QID261~1"})
    radio_2 = _FakeElement(attrs={"name": "QR~QID261", "id": "QR~QID261~2"})
    radio_3 = _FakeElement(attrs={"name": "QR~QID261", "id": "QR~QID261~3"})

    container = _FakeElement(
        by_selector={
            "ul.ChoiceStructure li.Selection input[type='radio'][name^='QR~'], table.ChoiceStructure input[type='radio'][name^='QR~']": [radio_1, radio_2, radio_3],
            "div.Inner fieldset legend div.QuestionText": [_FakeElement(text=q_text)],
            "label.SingleAnswer[for='QR~QID261~1'] span": [_FakeElement(text="Enseignement primaire")],
            "label.SingleAnswer[for='QR~QID261~2'] span": [_FakeElement(text="Premier cycle")],
            "label.SingleAnswer[for='QR~QID261~3'] span": [_FakeElement(text="Deuxième cycle")],
        }
    )

    driver = _FakeDriver(
        by_selector={
            "div.QuestionOuter": [container],
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)": [],
            "button, a[role='button'], [role='button'], .sq-cardrating-button": [],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "radio"
    assert "niveau" in block["question"].lower()
    assert len(block["options"]) == 3
    assert "primaire" in block["options"][0].lower()
    assert "premier" in block["options"][1].lower()
    assert "cycle" in block["options"][2].lower()
    assert block["max_select"] == 1
    assert (block.get("context") or {}).get("qualtrics_choice_structure_radio") is True
    assert (block.get("context") or {}).get("group_key") == "qualtrics_choice_structure:radio:QR~QID261"


def test_extracts_qualtrics_choice_structure_table_radios(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    q_text = "Où vivez-vous?"

    radio_1 = _FakeElement(attrs={"name": "QR~QID8", "id": "QR~QID8~56"})
    radio_2 = _FakeElement(attrs={"name": "QR~QID8", "id": "QR~QID8~64"})

    container = _FakeElement(
        by_selector={
            "ul.ChoiceStructure li.Selection input[type='radio'][name^='QR~'], table.ChoiceStructure input[type='radio'][name^='QR~']": [radio_1, radio_2],
            "div.Inner fieldset legend div.QuestionText": [_FakeElement(text=q_text)],
            "label.SingleAnswer[for='QR~QID8~56'] span": [_FakeElement(text="Bourgogne-Franche-Comté")],
            "label.SingleAnswer[for='QR~QID8~64'] span": [_FakeElement(text="Nouvelle Aquitaine")],
        }
    )

    driver = _FakeDriver(
        by_selector={
            "div.QuestionOuter": [container],
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)": [],
            "button, a[role='button'], [role='button'], .sq-cardrating-button": [],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "radio"
    assert "vivez" in block["question"].lower()
    assert len(block["options"]) == 2
    assert "bourgogne" in block["options"][0].lower()
    assert "nouvelle" in block["options"][1].lower()
