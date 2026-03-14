from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, text="", attrs=None, by_selector=None, tag_name="div", xpath=""):
        self.text = text
        self._attrs = attrs or {}
        self._by_selector = by_selector or {}
        self.tag_name = tag_name
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


def test_qualtrics_matrix_dropdown_rows_skip_generic_duplicates(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: el.xpath or "//fake")
    monkeypatch.setattr(da, "_detect_itype", lambda el: "dropdown" if (getattr(el, "tag_name", "") or "").lower() == "select" else "unknown")
    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: None)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_a, **_k: "")

    s1 = _FakeElement(attrs={"id": "QR~QID22~4", "name": "QR~QID22~4", "role": ""}, tag_name="select", xpath="//select[@id='QR~QID22~4']")
    s2 = _FakeElement(attrs={"id": "QR~QID22~5", "name": "QR~QID22~5", "role": ""}, tag_name="select", xpath="//select[@id='QR~QID22~5']")
    s3 = _FakeElement(attrs={"id": "QR~QID22~6", "name": "QR~QID22~6", "role": ""}, tag_name="select", xpath="//select[@id='QR~QID22~6']")

    seeded_blocks = [
        {"question": "Ligne 1", "itype": "dropdown", "options": ["A", "B"], "max_select": 1, "target_id": "single_a", "context": {"kind": "single", "id": "QR~QID22~4", "name": "QR~QID22~4"}},
        {"question": "Ligne 2", "itype": "dropdown", "options": ["A", "B"], "max_select": 1, "target_id": "single_b", "context": {"kind": "single", "id": "QR~QID22~5", "name": "QR~QID22~5"}},
        {"question": "Ligne 3", "itype": "dropdown", "options": ["A", "B"], "max_select": 1, "target_id": "single_c", "context": {"kind": "single", "id": "QR~QID22~6", "name": "QR~QID22~6"}},
    ]
    monkeypatch.setattr(
        da,
        "_extract_qualtrics_matrix_dropdown_row_blocks",
        lambda *_a, **_k: (seeded_blocks, {"QR~QID22~4", "QR~QID22~5", "QR~QID22~6"}, {"QR~QID22~4", "QR~QID22~5", "QR~QID22~6"}),
    )

    driver = _FakeDriver(
        by_selector={
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)": [],
            "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']": [s1, s2, s3],
            "button, a[role='button'], [role='button'], .sq-cardrating-button": [],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 3
    assert [b["question"] for b in blocks] == ["Ligne 1", "Ligne 2", "Ligne 3"]


def test_extracts_qualtrics_choice_structure_table_checkboxes(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    q_text = "Vous rendez-vous régulièrement dans l'une de ces villes?"

    chk_1 = _FakeElement(attrs={"name": "QR~QID13~21", "id": "QR~QID13~21"})
    chk_2 = _FakeElement(attrs={"name": "QR~QID13~27", "id": "QR~QID13~27"})
    chk_3 = _FakeElement(attrs={"name": "QR~QID13~23", "id": "QR~QID13~23"})

    container = _FakeElement(
        by_selector={
            "table.ChoiceStructure input[type='checkbox'][name^='QR~']": [chk_1, chk_2, chk_3],
            "div.Inner fieldset legend div.QuestionText": [_FakeElement(text=q_text)],
            "label.MultipleAnswer[for='QR~QID13~21'] span": [_FakeElement(text="Bourges")],
            "label.MultipleAnswer[for='QR~QID13~27'] span": [_FakeElement(text="Marseille")],
            "label.MultipleAnswer[for='QR~QID13~23'] span": [_FakeElement(text="Dijon")],
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
    assert block["itype"] == "checkbox"
    assert "villes" in block["question"].lower()
    assert len(block["options"]) == 3
    assert block["options"] == ["Bourges", "Marseille", "Dijon"]
    assert block["max_select"] == 3
    assert (block.get("context") or {}).get("qualtrics_choice_structure_checkbox") is True
    assert (block.get("context") or {}).get("group_key") == "qualtrics_choice_structure:checkbox:QR~QID13"
