from surveybot.Survey import dom_analyzer as da


class _FakeInput:
    tag_name = "input"

    def __init__(self, attrs):
        self._attrs = attrs

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return []


class _FakeDriver:
    def __init__(self, elements):
        self._elements = elements

    def find_elements(self, by=None, value=None):
        v = value or ""
        if "input[type='radio']" in v:
            return self._elements
        if "button, a[role='button']" in v:
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


def test_generic_grouping_skips_single_radio_with_aggregated_question_text(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    agg = (
        "Parmi les boissons alcoolisées suivantes, lesquelles avez-vous consommées au cours des 3 derniers mois ? "
        "Spiritueux à base d’anis Apéritifs Vermouths Liqueurs Bière Vin rouge "
        "Je n’ai pas consommé de boissons alcoolisées au cours des 3 derniers mois"
    )

    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_extract_surveywriter_ssi_question", lambda *_: "")
    monkeypatch.setattr(da, "_nearest_question_container", lambda *_: None)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_: "")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_: agg)
    monkeypatch.setattr(da, "_find_associated_label", lambda *_: agg)

    driver = _FakeDriver([_FakeInput({"type": "radio", "name": "dom:answer-options-container", "id": "r1"})])

    blocks = da._analyze_dom_current_context(driver)

    assert blocks == []


def test_aggregated_container_option_guard_keeps_short_legit_single_radio():
    assert da._looks_like_aggregated_container_option("Oui", "Oui") is False
