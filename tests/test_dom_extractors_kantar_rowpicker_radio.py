from surveybot.Survey.dom_extractors_misc import _extract_kantar_rowpicker_radio_blocks


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


def test_extract_kantar_rowpicker_radio_blocks(monkeypatch):
    monkeypatch.setattr(
        "surveybot.Survey.dom_extractors_misc._best_xpath_for_element",
        lambda _driver, el: el.xpath,
    )

    question_text = (
        "Merci beaucoup d’avoir participé à cette enquête. "
        "Accepteriez-vous d’être recontacté(e) ultérieurement ?"
    )

    clickable_a = _FakeElement(xpath="//*[@id='opt_1_click']")
    clickable_b = _FakeElement(xpath="//*[@id='opt_2_click']")

    card_a = _FakeElement(
        by_selector={
            "div[tabindex='0']": [clickable_a],
            "label span": [_FakeElement(text="Oui, j’accepte.")],
        }
    )
    card_b = _FakeElement(
        by_selector={
            "div[tabindex='0']": [clickable_b],
            "label span": [_FakeElement(text="Non, je refuse.")],
        }
    )

    container = _FakeElement(attrs={"id": "container_REcontact_agreement"})
    picker = _FakeElement(
        by_xpath={"ancestor::div[starts-with(@id,'container_')][1]": [container]},
        by_selector={"div.__flexgrid_row > div": [card_a, card_b]},
    )

    driver = _FakeDriver(
        by_selector={
            "div[id^='container_'] [data-test='main-contain']._rowpicker": [picker],
            "#qc_REcontact_agreement span.mrQuestionText": [_FakeElement(text=question_text)],
        }
    )

    blocks = _extract_kantar_rowpicker_radio_blocks(driver, frame_chain=[])

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "radio"
    assert "recontact" in block["question"].lower()
    assert block["options"] == ["Oui, j’accepte.", "Non, je refuse."]
    assert block["max_select"] == 1
    assert (block.get("context") or {}).get("kantar_rowpicker_radio") is True
    assert (block.get("context") or {}).get("group_key") == "kantar_rowpicker:radio:REcontact_agreement"
