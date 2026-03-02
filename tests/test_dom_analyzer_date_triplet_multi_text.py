from surveybot.Survey import dom_analyzer as da
from Survey.dom_registry import get_target


class _FakeElement:
    def __init__(self, tag="input", text="", attrs=None, by_selector=None, xpath=""):
        self.tag_name = tag
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

    def execute_script(self, script, *args):
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


def test_detects_month_day_year_triplet_as_multi_text(monkeypatch):
    _patch_non_generic_extractors(monkeypatch)

    month = _FakeElement(
        attrs={"type": "tel", "name": "date_m", "id": "dobMonth", "placeholder": "MM"},
        xpath="//*[@id='dobMonth']",
    )
    day = _FakeElement(
        attrs={"type": "tel", "name": "date_d", "id": "dobDay", "placeholder": "DD"},
        xpath="//*[@id='dobDay']",
    )
    year = _FakeElement(
        attrs={"type": "text", "name": "date_y", "id": "dobYear", "placeholder": "YYYY"},
        xpath="//*[@id='dobYear']",
    )
    fields = [month, day, year]

    container = _FakeElement(
        tag="div",
        by_selector={
            "input:not([type='radio']):not([type='checkbox']):not([type='hidden']):not([type='button']):not([type='submit']):not([type='reset']):not([type='file']):not([type='image']), textarea": fields
        },
    )

    driver = _FakeDriver(
        by_selector={
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)": [],
            "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']": fields,
            "button, a[role='button'], [role='button'], .sq-cardrating-button": [],
        }
    )

    monkeypatch.setattr(da, "_nearest_question_container", lambda _el: container)
    monkeypatch.setattr(da, "_extract_question_from_container", lambda *_a, **_k: "When were you born?")
    monkeypatch.setattr(da, "_find_question_text_near_element", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_find_associated_label", lambda *_a, **_k: "")
    monkeypatch.setattr(da, "_is_actionable_visible", lambda _el: True)
    monkeypatch.setattr(da, "_looks_like_system_field", lambda _el: False)
    monkeypatch.setattr(da, "_best_xpath_for_element", lambda _driver, el: el.xpath)

    blocks = da._analyze_dom_current_context(driver)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "text"
    assert block["question"] == "When were you born?"
    assert block["max_select"] == 3
    assert (block.get("context") or {}).get("kind") == "multi_text"
    assert (block.get("context") or {}).get("fields_count") == 3

    payload = get_target(block["target_id"]) or {}
    assert payload.get("kind") == "multi_text"
    assert [f.get("name") for f in payload.get("fields") or []] == ["date_m", "date_d", "date_y"]
