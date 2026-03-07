from surveybot.Survey.dom_extractors_decipher import _extract_focusvision_cardsort_block


class _FakeNode:
    def __init__(self, text="", children=None):
        self.text = text
        self._children = children or {}

    def find_elements(self, by=None, value=None):
        return list(self._children.get(value or "", []))

    def find_element(self, by=None, value=None):
        items = self.find_elements(by, value)
        if not items:
            raise Exception("not found")
        return items[0]


class _FakeDriver:
    def __init__(self, question):
        self.question = question

    def find_element(self, by=None, value=None):
        if value == "div.question.cardsort":
            raise Exception("legacy cardsort not present")
        if value == ".sq-cardsort":
            return self.question.find_element(by, value)
        raise Exception("not found")


def test_focusvision_cardsort_detects_sq_cardsort_widget():
    card_legend = _FakeNode(text="Whey protéines en poudre")
    bucket_1 = _FakeNode(text="Directement sur le site Web de la marque")
    bucket_2 = _FakeNode(text="Carrefour (y compris Carrefour Market)")

    widget = _FakeNode()
    question = _FakeNode(
        children={
            ".sq-cardsort": [widget],
            ".question-text": [_FakeNode(text="Dans lesquels des magasins suivants... ?")],
            ".sq-cardsort-cards .sq-cardsort-card .sq-cardsort-card-legend": [card_legend],
            ".sq-cardsort-buckets .sq-cardsort-bucket .sq-cardsort-bucket-legend": [bucket_1, bucket_2],
        }
    )
    widget._children = {
        "ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' question ')][1]": [question]
    }

    driver = _FakeDriver(question)
    block = _extract_focusvision_cardsort_block(driver, frame_chain=[])

    assert block is not None
    assert block["kind"] == "cardsort"
    assert block["question"] == "Dans lesquels des magasins suivants... ?"
    assert block["cards"] == ["Whey protéines en poudre"]
    assert block["buckets"] == [
        "Directement sur le site Web de la marque",
        "Carrefour (y compris Carrefour Market)",
    ]
