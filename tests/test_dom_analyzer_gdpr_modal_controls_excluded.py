from surveybot.Survey import dom_analyzer as da


class _FakeElement:
    def __init__(self, tag_name="div", text="", attrs=None, in_modal=False):
        self.tag_name = tag_name
        self.text = text
        self._attrs = attrs or {}
        self._in_modal = in_modal

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return []

    def find_element(self, by=None, value=None):
        raise Exception("not found")


class _FakeDriver:
    def __init__(self, choices, buttons, other_inputs):
        self._choices = choices
        self._buttons = buttons
        self._other_inputs = other_inputs

    def find_elements(self, by=None, value=None):
        query = value or ""
        if "input[type='radio'], input[type='checkbox']" in query:
            return self._choices
        if query == "button, a[role='button'], [role='button'], .sq-cardrating-button":
            return self._buttons
        if query == "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']":
            return self._other_inputs
        return []

    def execute_script(self, script, *args):
        if "el.closest" in (script or ""):
            el = args[0] if args else None
            return bool(getattr(el, "_in_modal", False))
        return None


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
    ]:
        monkeypatch.setattr(da, name, lambda *a, **k: [])


def test_analyze_dom_ignores_modal_related_controls_in_button_groups(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    consent = _FakeElement(
        tag_name="input",
        attrs={"type": "checkbox", "name": "gdpr_consent", "id": "gdpr_consent"},
    )
    modal_info = _FakeElement(
        tag_name="a",
        text="ici",
        attrs={"role": "button", "id": "purpose_detail_button", "href": "#gdpr_detail_modal"},
    )
    refuse = _FakeElement(
        tag_name="a",
        text="Je ne veux pas participer",
        attrs={"role": "button", "id": "refuse_button", "href": "#gdpr_refuse_modal"},
    )

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: "Cher(s) participant(s)")
    monkeypatch.setattr(da, "_extract_question_from_container", lambda _container, options: "Cher(s) participant(s)")
    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: _FakeElement(tag_name="div", attrs={"id": "q", "class": "question"}))
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda *_: "//fake")
    monkeypatch.setattr(
        da,
        "_find_associated_label",
        lambda _driver, el: "J'accepte le traitement de mes données personnelles en conformité avec les informations fournies ici."
        if (el.get_attribute("id") == "gdpr_consent") else "",
    )

    driver = _FakeDriver(
        choices=[consent],
        buttons=[modal_info, refuse],
        other_inputs=[modal_info, refuse],
    )

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    assert blocks[0]["itype"] == "checkbox"
    assert blocks[0]["options"] == [
        "J'accepte le traitement de mes données personnelles en conformité avec les informations fournies ici."
    ]
