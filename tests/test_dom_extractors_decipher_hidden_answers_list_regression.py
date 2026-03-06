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


class _FakeTable(_FakeNode):
    pass


def test_focusvision_answers_list_matrix_groups_into_single_matrix_block():
    i11 = _FakeInput(attrs={"id": "ans10544.0.1", "name": "ans10544.0.1", "type": "checkbox"})
    i21 = _FakeInput(attrs={"id": "ans10544.0.2", "name": "ans10544.0.2", "type": "checkbox"})
    i12 = _FakeInput(attrs={"id": "ans10544.0.2", "name": "ans10544.0.2", "type": "checkbox"})
    i22 = _FakeInput(attrs={"id": "ans10544.1.2", "name": "ans10544.1.2", "type": "checkbox"})

    table = _FakeTable(children={
        "th[id*='_c']": [
            _FakeNode(text="Transféré vers Revolut", attrs={"id": "Q10C_c1"}),
            _FakeNode(text="Laissé chez Société Générale", attrs={"id": "Q10C_c2"}),
        ],
        "th[id$='_left']": [
            _FakeNode(text="Épargne", attrs={"id": "Q10C_r1_left"}),
            _FakeNode(text="Crédit conso", attrs={"id": "Q10C_r2_left"}),
        ],
    })

    answers = _FakeNode(children={
        "input[type='radio'], input[type='checkbox']": [i11, i21, i12, i22],
        "table.grid": [table],
        "label[for='ans10544.0.1']": [_FakeNode(text="Transféré vers Revolut")],
        "label[for='ans10544.0.2']": [_FakeNode(text="Laissé chez Société Générale")],
        "label[for='ans10544.0.2']": [_FakeNode(text="Transféré vers Revolut")],
        "label[for='ans10544.1.2']": [_FakeNode(text="Laissé chez Société Générale")],
    })

    q = _FakeNode(children={
        ".answers.answers-list, .answers.answers-table": [answers],
        ".question-text": [_FakeNode(text="Vous avez changé de banque principale ?")],
    })

    class _D:
        def find_elements(self, by=None, value=None):
            if value == "div.question[role='radiogroup'], div.question.radio, div.question.checkbox":
                return [q]
            return []

    blocks = _extract_focusvision_answers_list_groups(_D(), frame_chain=[])

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "matrix"
    assert block["options"] == ["Transféré vers Revolut", "Laissé chez Société Générale"]
    assert block["context"]["matrix_rows"] == ["Épargne", "Crédit conso"]
    assert block["context"]["group_key"] == "matrix:name:ans10544"



def test_focusvision_gridclick_prefixes_current_segment_in_question_context():
    first = _FakeInput(attrs={"id": "ans10544.0.1", "name": "ans10544.0.1", "type": "checkbox"})
    second = _FakeInput(attrs={"id": "ans10544.0.2", "name": "ans10544.0.2", "type": "checkbox"})

    answers = _FakeNode(children={
        "input[type='radio'], input[type='checkbox']": [first, second],
        "label[for='ans10544.0.1']": [_FakeNode(text="Transféré vers Revolut")],
        "label[for='ans10544.0.2']": [_FakeNode(text="Laissé chez Société Générale")],
    })

    q = _FakeNode(children={
        ".answers.answers-list, .answers.answers-table": [answers],
        ".question-text": [_FakeNode(text="Vous avez changé de banque principale ?")],
        ".gridclick .scale-container .scale-button[data-index]": [_FakeNode()],
        ".gridclick .item.current .text-content": [_FakeNode(text="Épargne (Livret A)")],
    })

    class _D:
        def find_elements(self, by=None, value=None):
            if value == "div.question[role='radiogroup'], div.question.radio, div.question.checkbox":
                return [q]
            return []

    blocks = _extract_focusvision_answers_list_groups(_D(), frame_chain=[])

    assert len(blocks) == 1
    assert blocks[0]["question"] == "Épargne (Livret A) — Vous avez changé de banque principale ?"


def test_focusvision_atm1d_prefers_tile_xpath_over_hidden_fallback_inputs():
    from Survey.dom_registry import clear_registry, get_target

    clear_registry()

    class _FakeAtm1dButton(_FakeNode):
        def __init__(self, data_label, legend):
            super().__init__(attrs={"data-label": data_label}, children={".sq-atm1d-legend": [_FakeNode(text=legend)]})

    first = _FakeInput(attrs={"id": "ans404.0.0", "name": "ans404.0.0", "type": "checkbox"})
    second = _FakeInput(attrs={"id": "ans404.0.3", "name": "ans404.0.3", "type": "checkbox"})

    answers = _FakeNode(children={
        "input[type='radio'], input[type='checkbox']": [first, second],
        "label[for='ans404.0.0']": [_FakeNode(text="En-cas salés")],
        "label[for='ans404.0.3']": [_FakeNode(text="Aucune de ces propositions")],
    })

    q = _FakeNode(
        attrs={"id": "question_S_PastParticipation"},
        children={
            ".answers.answers-list, .answers.answers-table": [answers],
            ".question-text": [_FakeNode(text="Avez-vous participé ?")],
            ".sq-atm1d-widget .sq-atm1d-buttons .sq-atm1d-button[data-label]": [
                _FakeAtm1dButton("r1", "En-cas salés"),
                _FakeAtm1dButton("None", "Aucune de ces propositions"),
            ],
        },
    )

    class _D:
        def find_elements(self, by=None, value=None):
            if value == "div.question[role='radiogroup'], div.question.radio, div.question.checkbox":
                return [q]
            return []

    blocks = _extract_focusvision_answers_list_groups(_D(), frame_chain=[])

    assert len(blocks) == 1
    target_id = blocks[0]["target_id"]

    payload = get_target(target_id)
    assert payload is not None
    opt_map = payload["option_xpath_map"]
    assert any("li[contains(concat(' ',normalize-space(@class),' '),' sq-atm1d-button ')" in xp for xp in opt_map.values())
    assert any("@data-label=" in xp and "r1" in xp for xp in opt_map.values())
    assert any("@data-label=" in xp and "None" in xp for xp in opt_map.values())
    assert payload["meta"]["exclusive_options_norm"] == ["aucune de ces propositions"]
