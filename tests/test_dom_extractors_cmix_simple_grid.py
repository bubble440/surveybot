from surveybot.Survey import dom_extractors_misc as dem


class _FakeRadio:
    def __init__(self, name: str, value: str):
        self._attrs = {"name": name, "value": value}

    def get_attribute(self, name):
        return self._attrs.get(name, "")


class _FakeRowHeader:
    def __init__(self, text: str, has_other_specify: bool = False):
        self.text = text
        self._has_other_specify = has_other_specify

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return ""

    def find_elements(self, by=None, value=None):
        if value == "input.cm-other-specify" and self._has_other_specify:
            return [object()]
        return []


class _FakeRow:
    def __init__(self, row_header: _FakeRowHeader, radios: list[_FakeRadio]):
        self._row_header = row_header
        self._radios = radios

    def find_element(self, by=None, value=None):
        if value == "td.cm-simple-grid__row-header":
            return self._row_header
        raise Exception("not found")

    def find_elements(self, by=None, value=None):
        if value == "input[type='radio']":
            return self._radios
        return []


class _FakeTable:
    def __init__(self, headers: list[str], rows: list[_FakeRow]):
        self._headers = headers
        self._rows = rows

    def find_elements(self, by=None, value=None):
        if value == "thead th.cm-simple-grid__column-header":
            return [_FakeRowHeader(h) for h in self._headers]
        if value == "tbody tr":
            return self._rows
        return []


class _FakeDriver:
    def __init__(self, tables):
        self._tables = tables

    def find_elements(self, by=None, value=None):
        if value == "table.cm-simple-grid__table":
            return self._tables
        return []


def test_extract_cmix_simple_grid_skips_other_specify_rows(monkeypatch):
    headers = ["Toujours", "Régulièrement", "Jamais"]
    standard_row = _FakeRow(
        _FakeRowHeader("Football"),
        [_FakeRadio("group_row_1", "1"), _FakeRadio("group_row_1", "2"), _FakeRadio("group_row_1", "3")],
    )
    other_specify_row = _FakeRow(
        _FakeRowHeader("Autre, préciser", has_other_specify=True),
        [_FakeRadio("group_row_2", "1"), _FakeRadio("group_row_2", "2"), _FakeRadio("group_row_2", "3")],
    )
    table = _FakeTable(headers, [standard_row, other_specify_row])
    driver = _FakeDriver([table])

    monkeypatch.setattr(dem, "register_target", lambda *_, **__: None)
    monkeypatch.setattr(dem, "make_target_id", lambda *_: "target")

    blocks = dem._extract_cmix_simple_grid_question_blocks(driver, frame_chain=[])

    assert len(blocks) == 1
    assert blocks[0]["question"] == "Football"
