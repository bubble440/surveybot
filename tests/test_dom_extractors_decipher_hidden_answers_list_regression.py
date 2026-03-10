from surveybot.Survey.dom_extractors_decipher import (
    _clean_decipher_template_markers,
    _extract_decipher_answers_list_fallback,
    _extract_focusvision_answers_list_groups,
)


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


def test_clean_decipher_template_markers_strips_template_suffixes():
    assert _clean_decipher_template_markers(
        "Des desserts lactés pour bébé/enfant jusqu'à 3 ans{@imageURL::12.jpg@}"
    ) == "Des desserts lactés pour bébé/enfant jusqu'à 3 ans"
    assert _clean_decipher_template_markers(
        "Aucune de ces catégories de produits{@globalExclusive::true@}"
    ) == "Aucune de ces catégories de produits"


def test_decipher_fallback_cleans_template_markers_from_option_labels():
    class _FallbackDriver:
        def __init__(self):
            self._container = _FakeNode(
                text="Question exemple",
                children={
                    "input[type='radio'], input[type='checkbox']": [
                        _FakeInput(attrs={"id": "ans1", "name": "ans100.0", "type": "checkbox"}),
                        _FakeInput(attrs={"id": "ans2", "name": "ans100.0", "type": "checkbox"}),
                    ]
                },
            )

        def find_elements(self, by=None, value=None):
            if value == ".answer-list":
                return [self._container]
            return []

        def find_element(self, by=None, value=None):
            if value == "label[for='ans1']":
                return _FakeNode(text="Option A{@imageURL::12.jpg@}")
            if value == "label[for='ans2']":
                return _FakeNode(text="Option B{@globalExclusive::true@}")
            raise Exception("not found")

    blocks = _extract_decipher_answers_list_fallback(_FallbackDriver(), frame_chain=[])

    assert len(blocks) == 1
    assert blocks[0]["options"] == ["Option A", "Option B"]


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


def test_focusvision_group_by_row_radio_table_emits_one_block_per_row():
    col_labels = [
        "Une fois par semaine ou plus",
        "Toutes les 2 semaines",
        "Toutes les 3 semaines",
        "Une fois par mois",
        "Une fois tous les 2 à 3 mois",
        "Une fois tous les 4 à 6 mois",
        "Moins souvent",
        "Je n’ai acheté ce produit qu’une seule fois.",
    ]
    row_defs = [
        ("ans899.0.0", "Whey protéines en poudre"),
        ("ans899.0.1", "Collagène"),
        ("ans899.0.7", "Pre-workouts"),
        ("ans899.0.17", "Créatine en poudre"),
    ]

    col_headers = [_FakeNode(text=lbl) for lbl in col_labels]
    row_nodes = []
    label_map = {}

    for row_name, row_label in row_defs:
        row_inputs = []
        for idx, col_label in enumerate(col_labels):
            inp_id = f"{row_name.replace('.', '_')}_{idx}"
            row_inputs.append(_FakeInput(attrs={"id": inp_id, "name": row_name, "type": "radio"}))
            label_map[f"label[for='{inp_id}']"] = [_FakeNode(text=col_label)]

        row_nodes.append(
            _FakeNode(
                children={
                    "th[scope='row']": [_FakeNode(text=row_label)],
                    "input[type='radio'], input[type='checkbox']": row_inputs,
                }
            )
        )

    table = _FakeTable(
        children={
            "th[scope='col']": col_headers,
            "tr.row-elements": row_nodes,
        }
    )

    answers_children = {
        "table.grid[data-settings*='group-by-row'][data-settings*='table-mode']": [table],
        "input[type='radio'], input[type='checkbox']": [
            _FakeInput(attrs={"id": "seed1", "name": "ans899.0.0", "type": "radio"}),
            _FakeInput(attrs={"id": "seed2", "name": "ans899.0.1", "type": "radio"}),
        ],
    }
    answers_children.update(label_map)

    answers = _FakeNode(children=answers_children)

    q = _FakeNode(children={
        ".answers.answers-list, .answers.answers-table": [answers],
        ".question-text": [_FakeNode(text="À quelle fréquence en moyenne achetez-vous chacun des produits suivants ?")],
    })

    class _D:
        def find_elements(self, by=None, value=None):
            if value == "div.question[role='radiogroup'], div.question.radio, div.question.checkbox":
                return [q]
            return []

    blocks = _extract_focusvision_answers_list_groups(_D(), frame_chain=[])

    assert len(blocks) == 4
    assert all(b["itype"] == "radio" for b in blocks)
    assert [b["question"] for b in blocks] == [row_label for _, row_label in row_defs]
    assert all(len(b["options"]) == 8 for b in blocks)
    assert all(b["max_select"] == 1 for b in blocks)


def test_focusvision_group_by_row_checkbox_table_accepts_per_cell_names():
    col_headers = [
        _FakeNode(text="Bulk", attrs={"id": "S12_c4"}),
        _FakeNode(text="ESN", attrs={"id": "S12_c10"}),
        _FakeNode(text="Nutrimuscle", attrs={"id": "S12_c21"}),
    ]

    row_inputs = [
        _FakeInput(attrs={"id": "ans1352.3.0", "name": "ans1352.3.0", "type": "checkbox"}, children={"ancestor::td[1]": [_FakeNode(attrs={"headers": "S12_c4"})]}),
        _FakeInput(attrs={"id": "ans1352.9.0", "name": "ans1352.9.0", "type": "checkbox"}, children={"ancestor::td[1]": [_FakeNode(attrs={"headers": "S12_c10"})]}),
        _FakeInput(attrs={"id": "ans1352.20.0", "name": "ans1352.20.0", "type": "checkbox"}, children={"ancestor::td[1]": [_FakeNode(attrs={"headers": "S12_c21"})]}),
    ]

    row = _FakeNode(
        children={
            "th[scope='row']": [_FakeNode(text="Whey protéines en poudre", attrs={"id": "S12_r1_left"})],
            "input[type='radio'], input[type='checkbox']": row_inputs,
        }
    )

    table = _FakeTable(
        children={
            "th[scope='col']": col_headers,
            "tr.row-elements": [row],
        }
    )

    answers = _FakeNode(
        children={
            "table.grid[data-settings*='group-by-row'][data-settings*='table-mode']": [table],
            "input[type='radio'], input[type='checkbox']": row_inputs,
        }
    )

    q = _FakeNode(children={
        ".answers.answers-list, .answers.answers-table": [answers],
        ".question-text": [_FakeNode(text="Enfin, lesquelles des marques suivantes avez-vous achetées ?")],
    })

    class _D:
        def find_elements(self, by=None, value=None):
            if value == "div.question[role='radiogroup'], div.question.radio, div.question.checkbox":
                return [q]
            return []

    blocks = _extract_focusvision_answers_list_groups(_D(), frame_chain=[])

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "checkbox"
    assert block["question"] == "Whey protéines en poudre"
    assert block["options"] == ["Bulk", "ESN", "Nutrimuscle"]


def test_focusvision_group_by_row_mx_carousel_prefers_visible_scale_cards():
    from Survey.dom_registry import clear_registry, get_target

    clear_registry()

    col_headers = [
        _FakeNode(text="Oui", attrs={"id": "QR8_c1"}),
        _FakeNode(text="Non", attrs={"id": "QR8_c2"}),
    ]
    row = _FakeNode(
        children={
            "th[scope='row']": [_FakeNode(text="Chocolat en poudre", attrs={"id": "QR8_r1_left"})],
            "input[type='radio'], input[type='checkbox']": [
                _FakeInput(attrs={"id": "ans10210.0.0", "name": "ans10210.0.0", "type": "radio", "value": "0"}),
                _FakeInput(attrs={"id": "ans10210.1.0", "name": "ans10210.0.0", "type": "radio", "value": "1"}),
            ],
        }
    )
    table = _FakeTable(
        children={
            "th[scope='col']": col_headers,
            "tr.row-elements": [row],
        }
    )

    answers = _FakeNode(
        children={
            "table.grid[data-settings*='group-by-row'][data-settings*='table-mode']": [table],
            "input[type='radio'], input[type='checkbox']": [
                _FakeInput(attrs={"id": "seed1", "name": "ans10210.0.0", "type": "radio"}),
                _FakeInput(attrs={"id": "seed2", "name": "ans10210.0.1", "type": "radio"}),
            ],
        }
    )

    q = _FakeNode(
        children={
            ".answers.answers-list, .answers.answers-table": [answers],
            ".question-text": [_FakeNode(text="Avez-vous déjà répondu ?")],
            ".mx-stage[id^='mx-stage-']": [_FakeNode(attrs={"id": "mx-stage-QR8"})],
        }
    )

    class _D:
        def find_elements(self, by=None, value=None):
            if value == "div.question[role='radiogroup'], div.question.radio, div.question.checkbox":
                return [q]
            return []

    blocks = _extract_focusvision_answers_list_groups(_D(), frame_chain=[])

    assert len(blocks) == 1
    payload = get_target(blocks[0]["target_id"])
    assert payload is not None
    opt_map = payload["option_xpath_map"]
    assert "mx-carouselapp-scale" in opt_map["oui"]
    assert '@data-code="c1"' in opt_map["oui"]
    assert '@data-code="c2"' in opt_map["non"]
    assert payload.get("pre_click_xpaths")
    assert "mx-carouselapp-item" in payload["pre_click_xpaths"][0]
    assert '@data-code="r1"' in payload["pre_click_xpaths"][0]

def test_focusvision_answers_list_mx_collapsible_stage_found_from_driver_scope():
    from Survey.dom_registry import clear_registry, get_target

    clear_registry()

    first = _FakeInput(attrs={"id": "ans10247.0.3", "name": "ans10247.0.3", "type": "checkbox"})
    second = _FakeInput(attrs={"id": "ans10247.0.4", "name": "ans10247.0.4", "type": "checkbox"})
    answers = _FakeNode(
        children={
            "input[type='radio'], input[type='checkbox']": [first, second],
            "label[for='ans10247.0.3']": [_FakeNode(text="Du lait UHT aromatisé")],
            "label[for='ans10247.0.4']": [_FakeNode(text="Du lait UHT classique")],
        }
    )

    q = _FakeNode(
        attrs={"id": "question_QR10"},
        children={
            ".answers.answers-list, .answers.answers-table": [answers],
            ".question-text": [_FakeNode(text="Parmi les catégories...")],
        },
    )

    mx_row_1 = _FakeNode(
        attrs={"precode": "r4"},
        children={".bottom .label": [_FakeNode(text="Du lait UHT aromatisé")]},
    )
    mx_row_2 = _FakeNode(
        attrs={"precode": "r5"},
        children={".bottom .label": [_FakeNode(text="Du lait UHT classique")]},
    )
    mx_stage = _FakeNode(
        children={
            ".mx-collapsible-groupholder .mx-collapsible-row-item[precode]": [mx_row_1, mx_row_2],
            ".mx-collapsible-exclusive-holder .mx-collapsible-exclusive[class*='mx-button-r']": [],
        }
    )

    class _D:
        def find_elements(self, by=None, value=None):
            if value == "div.question[role='radiogroup'], div.question.radio, div.question.checkbox":
                return [q]
            return []

        def find_element(self, by=None, value=None):
            if value == "#mx-stage-QR10":
                return mx_stage
            raise Exception("not found")

    blocks = _extract_focusvision_answers_list_groups(_D(), frame_chain=[])

    assert len(blocks) == 1
    payload = get_target(blocks[0]["target_id"])
    assert payload is not None
    xpaths = list(payload["option_xpath_map"].values())
    assert any("mx-stage-QR10" in xp for xp in xpaths)
    assert any("mx-collapsible-row-item" in xp for xp in xpaths)
    assert any("@precode" in xp for xp in xpaths)
    assert all("clickableCell" not in xp for xp in xpaths)
