from surveybot.Survey.dom_extractors_decipher import _extract_focusvision_cardsort_block


class _FakeNode:
    def __init__(self, text="", attrs=None, children=None, question_ancestor=None):
        self.text = text
        self._attrs = attrs or {}
        self._children = children or {}
        self._question_ancestor = question_ancestor

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return list(self._children.get(value or "", []))

    def find_element(self, by=None, value=None):
        key = value or ""
        if key == "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' question ')][1]" and self._question_ancestor is not None:
            return self._question_ancestor
        items = self.find_elements(by, value)
        if not items:
            raise Exception("not found")
        return items[0]


class _FakeDriver:
    def __init__(self, elements):
        self._elements = elements

    def find_element(self, by=None, value=None):
        items = self.find_elements(by, value)
        if not items:
            raise Exception("not found")
        return items[0]

    def find_elements(self, by=None, value=None):
        return list(self._elements.get(value or "", []))


def test_focusvision_cardsort_extracts_sq_cardsort_variant_from_decipher_dom():
    question_container = _FakeNode(
        children={
            ".question-text": [
                _FakeNode(text="Dans lesquels des magasins suivants avez-vous acheté ?")
            ]
        }
    )

    first_card = _FakeNode(
        text="Whey protéines en poudre",
        attrs={"class": "sq-cardsort-card sq-cardsort-state-selected"},
    )
    completion_item = _FakeNode(text="Vous avez terminé !", attrs={"class": "sq-cardsort-completion"})

    first_bucket = _FakeNode(
        children={
            ".sq-cardsort-bucket-legend": [_FakeNode(text="Directement sur le site Web de la marque")]
        }
    )
    second_bucket = _FakeNode(children={".sq-cardsort-bucket-legend": [_FakeNode(text="Leclerc")]})

    sq_cardsort = _FakeNode(
        children={
            ".sq-cardsort-card": [first_card, completion_item],
            ".sq-cardsort-bucket": [first_bucket, second_bucket],
        },
        question_ancestor=question_container,
    )

    driver = _FakeDriver(elements={"div.question.cardsort": [], ".sq-cardsort": [sq_cardsort]})

    block = _extract_focusvision_cardsort_block(driver, frame_chain=[])

    assert block is not None
    assert block["kind"] == "cardsort"
    assert block["question"] == "Dans lesquels des magasins suivants avez-vous acheté ?"
    assert block["cards"] == ["Whey protéines en poudre"]
    assert block["buckets"] == ["Directement sur le site Web de la marque", "Leclerc"]


def test_focusvision_cardsort_returns_none_when_sq_cardsort_has_no_buckets():
    sq_cardsort = _FakeNode(children={".sq-cardsort-card": [_FakeNode(text="Produit A")], ".sq-cardsort-bucket": []})
    driver = _FakeDriver(elements={"div.question.cardsort": [], ".sq-cardsort": [sq_cardsort]})

    block = _extract_focusvision_cardsort_block(driver, frame_chain=[])

    assert block is None
