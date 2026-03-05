from surveybot.Survey import dom_extractors_misc as dem


class _FakeRadio:
    def __init__(self, name: str, value: str, rid: str):
        self._attrs = {"name": name, "value": value, "id": rid}

    def get_attribute(self, name):
        return self._attrs.get(name, "")


class _FakeCell:
    def __init__(self, text: str):
        self.text = text

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return ""


class _FakeRow:
    def __init__(self, row_label: str, radios: list[_FakeRadio]):
        self._cells = [_FakeCell(row_label), _FakeCell("")]
        self._radios = radios

    def find_elements(self, by=None, value=None):
        if value == "td, th":
            return self._cells
        if value == "input[type='radio']":
            return self._radios
        return []


class _FakeTable:
    def __init__(self, headers: list[str], rows: list[_FakeRow]):
        self._headers = [_FakeCell("")] + [_FakeCell(h) for h in headers]
        self._rows = rows

    def get_attribute(self, name):
        return ""

    def find_elements(self, by=None, value=None):
        if value == "thead tr th":
            return self._headers
        if value == "tbody tr":
            return self._rows
        return []


class _FakeDriver:
    def __init__(self, tables):
        self._tables = tables

    def find_elements(self, by=None, value=None):
        if value == "table":
            return self._tables
        return []


def test_extract_table_matrix_builds_single_matrix_block_for_sge_pattern(monkeypatch):
    rows = [
        _FakeRow("Netflix", [_FakeRadio("sge-8714385-48-61", "1", "r1"), _FakeRadio("sge-8714385-48-61", "2", "r2")]),
        _FakeRow("Disney+", [_FakeRadio("sge-8714385-48-62", "1", "r3"), _FakeRadio("sge-8714385-48-62", "2", "r4")]),
    ]
    table = _FakeTable(["Très favorable", "Plutôt favorable"], rows)
    driver = _FakeDriver([table])

    monkeypatch.setattr(dem, "_find_question_text_near_element", lambda *_: "Opinion services")
    monkeypatch.setattr(dem, "register_target", lambda *_, **__: None)
    monkeypatch.setattr(dem, "make_target_id", lambda *_, **__: "matrix_target")

    blocks = dem._extract_table_matrix_radio_rows(driver, frame_chain=[])

    assert len(blocks) == 1
    block = blocks[0]
    assert block["itype"] == "matrix"
    assert block["question"] == "Opinion services"
    assert len(block["options"]) == 2
    assert "favorable" in block["options"][0].lower()
    assert "favorable" in block["options"][1].lower()
    assert block["context"]["matrix_rows"] == ["Netflix", "Disney+"]


def test_extract_table_matrix_keeps_row_radio_blocks_for_non_sge_names(monkeypatch):
    rows = [
        _FakeRow("Netflix", [_FakeRadio("q1_row1", "1", "n1"), _FakeRadio("q1_row1", "2", "n2")]),
        _FakeRow("Disney+", [_FakeRadio("q1_row2", "1", "d1"), _FakeRadio("q1_row2", "2", "d2")]),
    ]
    table = _FakeTable(["Très favorable", "Plutôt favorable"], rows)
    driver = _FakeDriver([table])

    monkeypatch.setattr(dem, "_find_question_text_near_element", lambda *_: "Opinion services")
    monkeypatch.setattr(dem, "register_target", lambda *_, **__: None)
    monkeypatch.setattr(dem, "make_target_id", lambda *_, **__: "row_target")

    blocks = dem._extract_table_matrix_radio_rows(driver, frame_chain=[])

    assert len(blocks) == 2
    assert all(b["itype"] == "radio" for b in blocks)
