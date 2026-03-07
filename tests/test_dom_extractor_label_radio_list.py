from surveybot.Survey import dom_extractors_misc as de


class _FakeElement:
    def __init__(self, text="", attrs=None, by_selector=None, xpath=""):
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


def test_extract_label_radio_list_blocks(monkeypatch):
    monkeypatch.setattr(de, "_best_xpath_for_element", lambda _driver, el: el.xpath)

    labels = [
        _FakeElement(text="Employé(e) à temps plein", xpath="//label[1]"),
        _FakeElement(text="Employé(e) à temps partiel", xpath="//label[2]"),
        _FakeElement(text="Travailleur indépendant", xpath="//label[3]"),
    ]

    step = _FakeElement(
        attrs={"id": ""},
        by_selector={
            "h3.title": [_FakeElement(text="Quelle-est votre situation professionnelle actuelle ?")],
            "input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox']": [],
            "ul.option_container label.radio": labels,
        },
    )

    driver = _FakeDriver(by_selector={"div.step1": [step]})

    blocks = de._extract_label_radio_list_blocks(driver, frame_chain=None)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "radio"
    assert "situation professionnelle" in block["question"].lower()
    assert len(block["options"]) == 3
    assert (block.get("context") or {}).get("label_radio_list") is True


def test_extract_label_radio_list_blocks_skips_when_native_radios_exist(monkeypatch):
    monkeypatch.setattr(de, "_best_xpath_for_element", lambda _driver, el: el.xpath)

    step = _FakeElement(
        by_selector={
            "h3.title": [_FakeElement(text="Question")],
            "input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox']": [_FakeElement()],
            "ul.option_container label.radio": [
                _FakeElement(text="Oui", xpath="//label[1]"),
                _FakeElement(text="Non", xpath="//label[2]"),
            ],
        },
    )

    driver = _FakeDriver(by_selector={"div.step1": [step]})

    blocks = de._extract_label_radio_list_blocks(driver, frame_chain=[])
    assert blocks == []
