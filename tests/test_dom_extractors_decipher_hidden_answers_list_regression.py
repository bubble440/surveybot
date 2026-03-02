from surveybot.Survey.dom_extractors_decipher import _extract_focusvision_answers_list_groups


class _FakeNode:
    def __init__(self, text="", attrs=None, children=None):
        self.text = text
        self._attrs = attrs or {}
        self._children = children or {}

    def get_attribute(self, name):
        return self._attrs.get(name, "")

    def find_elements(self, by=None, value=None):
        return list(self._children.get(value or "", []))

    def find_element(self, by=None, value=None):
        items = self.find_elements(by, value)
        if not items:
            raise Exception("not found")
        return items[0]


class _FakeInput(_FakeNode):
    def find_element(self, by=None, value=None):
        raise Exception("no clickableCell fallback in this test")


class _FakeDriver:
    def __init__(self):
        # Hidden matrix-style answers: Selenium .text would be empty, but textContent is present.
        first = _FakeInput(attrs={"id": "ans10544.0.1", "name": "ans10544.0.1", "type": "checkbox"})
        second = _FakeInput(attrs={"id": "ans10544.0.3", "name": "ans10544.0.3", "type": "checkbox"})

        answers = _FakeNode(
            children={
                "input[type='radio'], input[type='checkbox']": [first, second],
                "label[for='ans10544.0.1']": [
                    _FakeNode(attrs={"innerText": "Transféré vers  Revolut"})
                ],
                "label[for='ans10544.0.3']": [
                    _FakeNode(attrs={"textContent": "Laissé chez Société Générale"})
                ],
            }
        )

        self.question_container = _FakeNode(
            children={
                ".answers.answers-list, .answers.answers-table": [answers],
                ".question-text": [
                    _FakeNode(text="Vous avez changé de banque principale : qu’avez-vous transféré ?")
                ],
            }
        )

    def find_elements(self, by=None, value=None):
        if value == "div.question[role='radiogroup'], div.question.radio, div.question.checkbox":
            return [self.question_container]
        return []


def test_focusvision_answers_list_extracts_hidden_labels_via_dom_text_content():
    driver = _FakeDriver()

    blocks = _extract_focusvision_answers_list_groups(driver, frame_chain=[])

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "checkbox"
    assert len(block["options"]) == 2
    assert "Transféré vers  Revolut" in block["options"]
    assert "Laissé chez Société Générale" in block["options"]
