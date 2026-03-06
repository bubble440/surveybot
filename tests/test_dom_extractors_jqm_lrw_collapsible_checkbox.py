from selenium.webdriver.common.by import By
import unicodedata

from surveybot.Survey.dom_extractors_misc import _extract_jqm_lrw_collapsible_checkbox_rows


class _FakeElement:
    def __init__(self, text="", attrs=None, list_map=None, one_map=None):
        self.text = text
        self._attrs = attrs or {}
        self._list_map = list_map or {}
        self._one_map = one_map or {}

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return list(self._list_map.get((by, value), []))

    def find_element(self, by=None, value=None):
        key = (by, value)
        if key in self._one_map:
            return self._one_map[key]
        lst = self._list_map.get(key, [])
        if lst:
            return lst[0]
        raise Exception("not found")


class _FakeDriver:
    def __init__(self, containers):
        self._containers = containers

    def find_elements(self, by=None, value=None):
        if (by, value) == (By.CSS_SELECTOR, "div.collapsible-container.ui-collapsible-set"):
            return self._containers
        return []


def _build_checkbox_row(header: str, name_prefix: str):
    heading_span = _FakeElement(text=f"{header} click to expand contents")

    options = [
        "Salle de sport ou studio d’entraînement",
        "Magasin d’articles de sport",
        "Amazon",
    ]

    boxes = []
    one_map = {
        (
            By.CSS_SELECTOR,
            "div.ui-collapsible-heading button.ui-collapsible-heading-toggle span.mrQuestionText",
        ): heading_span
    }

    for idx, label in enumerate(options):
        cid = f"cb_{name_prefix}_{idx}"
        box = _FakeElement(attrs={"name": f"{name_prefix}_{idx}", "id": cid, "value": f"v{idx}"})
        boxes.append(box)
        one_map[(By.CSS_SELECTOR, f"label[for='{cid}']")] = _FakeElement(text=label)

    return _FakeElement(
        list_map={(By.CSS_SELECTOR, "div.ui-collapsible-content input[type='checkbox'][name]"): boxes},
        one_map=one_map,
    )


def test_extract_jqm_lrw_collapsible_checkbox_rows_strips_heading_status_and_splits_rows():
    row1 = _build_checkbox_row("Vêtements de sport", "_QPurchaseChannel_QActiveWear_QGV_C")
    row2 = _build_checkbox_row("Vêtements décontractés", "_QPurchaseChannel_QCasualWear_QGV_C")

    container = _FakeElement(
        list_map={(By.XPATH, "./div[contains(@class,'collapsible-button-group')]"): [row1, row2]}
    )

    driver = _FakeDriver([container])

    blocks = _extract_jqm_lrw_collapsible_checkbox_rows(driver, frame_chain=[])

    assert len(blocks) == 2
    assert blocks[0]["itype"] == "checkbox"
    q0_ascii = unicodedata.normalize("NFKD", blocks[0]["question"]).encode("ascii", "ignore").decode("ascii").lower()
    q1_ascii = unicodedata.normalize("NFKD", blocks[1]["question"]).encode("ascii", "ignore").decode("ascii").lower()
    assert q0_ascii == "vetements de sport"
    assert q1_ascii == "vetements decontractes"
    assert "click to expand" not in blocks[0]["question"].lower()
    assert len(blocks[0]["options"]) == 3
    assert blocks[0]["max_select"] == 3
    assert "radio:button_group" not in (blocks[0]["context"] or {}).get("group_key", "")
