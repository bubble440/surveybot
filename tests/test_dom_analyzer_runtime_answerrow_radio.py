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
        "_extract_custom_testid_multi_select_checkbox_blocks",
        "_extract_single_consent_checkbox_block",
        "_extract_purespectrum_mobile_date_blocks",
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_extracts_runtime_answerrow_radio_group(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: el.xpath)

    question_text = (
        "Dans quelle catégorie socioprofessionnelle vous situez-vous? "
        "Si vous êtes à la recherche d'un emploi."
    )
    question_container = _FakeElement(
        attrs={"id": "question_1005145"},
        by_selector={
            "[data-aut='Runtime_QuestionTitleAndDescriptionWrapper'] [data-aut='Runtime-TextComponent']": [
                _FakeElement(text=question_text)
            ]
        },
    )

    def _row(option_text, idx):
        return _FakeElement(
            by_selector={
                ".radio_button[data-aut='Runtime_Wrapper']": [_FakeElement()],
                "[data-aut='Runtime_AnswerText'] [data-aut='Runtime-TextComponent']": [_FakeElement(text=option_text)],
            },
            by_xpath={
                "ancestor::*[@id][starts-with(@id, 'question_')][1]": [question_container],
            },
            xpath=f"//div[@class='answer'][{idx}]",
        )

    row_a = _row("Agriculteur exploitant", 1)
    row_b = _row("Artisan, commerçant", 2)
    row_c = _row("Employé", 3)

    driver = _FakeDriver(
        by_selector={
            "[data-aut='Runtime_QuestionTitleAndDescriptionWrapper'] [data-aut='Runtime-TextComponent']": [
                _FakeElement(text=question_text)
            ],
            ".answer[data-aut='Runtime_AnswerRow']": [row_a, row_b, row_c],
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)": [],
            "button, a[role='button'], [role='button'], .sq-cardrating-button": [],
        }
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "radio"
    assert "socioprofessionnelle" in block["question"].lower()
    assert len(block["options"]) == 3
    assert "agriculteur" in block["options"][0].lower()
    assert "artisan" in block["options"][1].lower()
    assert "employ" in block["options"][2].lower()
    assert block["max_select"] == 1
    assert (block.get("context") or {}).get("runtime_answerrow_radio") is True
    assert (block.get("context") or {}).get("group_key") == "runtime_answerrow:radio:question_1005145"
