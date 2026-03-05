from surveybot.Survey.dom_extractors_misc import _extract_purespectrum_date_dropdown_blocks


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
        els = self.find_elements(by, value)
        if not els:
            raise Exception("not found")
        return els[0]


class _FakeDriver:
    def __init__(self):
        month_toggle = _FakeNode(attrs={"id": "month-toggle"})
        year_toggle = _FakeNode(attrs={"id": "year-toggle"})

        month_options = [
            _FakeNode(text="Janvier", attrs={"data-e2e": "1", "id": "m1"}),
            _FakeNode(text="Juillet", attrs={"data-e2e": "7", "id": "m7"}),
        ]
        year_options = [
            _FakeNode(text="1991", attrs={"data-e2e": "1991", "id": "y1991"}),
            _FakeNode(text="1990", attrs={"data-e2e": "1990", "id": "y1990"}),
        ]

        month_dd = _FakeNode(
            attrs={"data-e2e": "month", "id": "dd-month"},
            children={
                "button.dropdown-toggle": [month_toggle],
                "button[ngbdropdownitem][data-e2e]": month_options,
            },
        )
        year_dd = _FakeNode(
            attrs={"data-e2e": "year", "id": "dd-year"},
            children={
                "button.dropdown-toggle": [year_toggle],
                "button[ngbdropdownitem][data-e2e]": year_options,
            },
        )

        self.date_q = _FakeNode(
            attrs={"qualificationid": "212", "id": "q-date"},
            children={
                "ps-select-dropdown[data-e2e='month'], ps-select-dropdown[data-e2e='year']": [month_dd, year_dd],
                ".question-title": [_FakeNode(text="Date de naissance:")],
            },
        )

    def find_elements(self, by=None, value=None):
        if value == "ps-date-question[qualificationid]":
            return [self.date_q]
        return []

    def execute_script(self, script, node):
        node_id = node.get_attribute("id")
        return f"//*[@id='{node_id}']"



def test_extract_purespectrum_date_dropdown_blocks_month_and_year():
    driver = _FakeDriver()

    blocks = _extract_purespectrum_date_dropdown_blocks(driver, frame_chain=[])

    assert len(blocks) == 2
    questions = {b["question"] for b in blocks}
    assert "Date de naissance: (Mois)" in questions
    assert "Date de naissance: (Année)" in questions

    month_block = next(b for b in blocks if b["question"].endswith("(Mois)"))
    year_block = next(b for b in blocks if b["question"].endswith("(Année)"))

    assert month_block["itype"] == "radio"
    assert year_block["itype"] == "radio"
    assert "Juillet" in month_block["options"]
    assert "1990" in year_block["options"]
    assert month_block["context"]["purespectrum_date_dropdown"] is True
    assert year_block["context"]["purespectrum_date_dropdown"] is True
