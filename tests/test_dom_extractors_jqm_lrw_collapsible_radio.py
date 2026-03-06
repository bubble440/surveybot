from selenium.webdriver.common.by import By
import unicodedata

from surveybot.Survey.dom_extractors_misc import _extract_jqm_lrw_collapsible_radio_rows


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


def _build_row(header: str, group_name: str):
    heading_span = _FakeElement(text=header)

    options = [
        "Moins d’une fois par an",
        "Une ou deux fois par an",
        "Une fois tous les 2-3 mois",
        "Une ou deux fois par mois",
        "Environ une fois par semaine",
    ]

    radios = []
    one_map = {
        (
            By.CSS_SELECTOR,
            "div.ui-collapsible-heading button.ui-collapsible-heading-toggle span.mrQuestionText",
        ): heading_span
    }

    for idx, label in enumerate(options):
        rid = f"radio_{group_name}_{idx}"
        radio = _FakeElement(attrs={"name": group_name, "id": rid, "value": f"v{idx}"})
        radios.append(radio)
        one_map[(By.CSS_SELECTOR, f"label[for='{rid}']")] = _FakeElement(text=label)

    return _FakeElement(
        list_map={
            (By.CSS_SELECTOR, "div.ui-collapsible-content input[type='radio'][name]"): radios,
        },
        one_map=one_map,
    )


def test_extract_jqm_lrw_collapsible_radio_rows_ignores_toggle_button_text():
    row1 = _build_row("Vêtements de sport", "_QPurchaseFreq_QActiveWear_QGV_C")
    row2 = _build_row("Vêtements décontractés", "_QPurchaseFreq_QCasualWear_QGV_C")

    main_question = _FakeElement(text="À quelle fréquence achetez-vous chaque type de vêtements pour vous ?")
    wrapper = _FakeElement(
        list_map={
            (
                By.XPATH,
                ".//span[contains(@class,'mrQuestionText')][not(ancestor::div[contains(@class,'collapsible-container')])]",
            ): [main_question],
        }
    )

    container = _FakeElement(
        list_map={
            (By.XPATH, "./div[contains(@class,'collapsible-button-group')]"): [row1, row2],
            (By.XPATH, "ancestor::*[contains(@class,'content-wrapper')][1]"): [wrapper],
        }
    )

    driver = _FakeDriver([container])

    blocks = _extract_jqm_lrw_collapsible_radio_rows(driver, frame_chain=[])

    assert len(blocks) == 2
    q0_ascii = unicodedata.normalize("NFKD", blocks[0]["question"]).encode("ascii", "ignore").decode("ascii").lower()
    assert "a quelle frequence achetez-vous chaque type de vetements pour vous ?" in q0_ascii
    assert q0_ascii.endswith("vetements de sport")
    q1_ascii = unicodedata.normalize("NFKD", blocks[1]["question"]).encode("ascii", "ignore").decode("ascii").lower()
    assert q1_ascii.endswith("vetements decontractes")
    assert "click to expand" not in blocks[0]["question"].lower()
    assert "click to expand" not in " ".join(blocks[0]["options"]).lower()
    assert len(blocks[0]["options"]) == 5
    assert len(blocks[1]["options"]) == 5
