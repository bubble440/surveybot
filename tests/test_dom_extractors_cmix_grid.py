import unicodedata

from surveybot.Survey import dom_extractors_misc as dem


class _FakeCell:
    def __init__(self, text: str):
        self.text = text

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        return ""


class _FakeRadio:
    def __init__(self, name: str, value: str):
        self._attrs = {"name": name, "value": value}

    def get_attribute(self, name):
        return self._attrs.get(name, "")


class _FakeRow:
    def __init__(self, row_label: str, radios: list[_FakeRadio]):
        self._row_hdr = _FakeCell(row_label)
        self._radios = radios

    def find_element(self, by=None, value=None):
        if value == "td.cm-grid-column-header-1, th.cm-grid-column-header-1":
            return self._row_hdr
        raise Exception("not found")

    def find_elements(self, by=None, value=None):
        if value == "input[type='radio'][name][value]":
            return self._radios
        return []


class _FakeTable:
    def __init__(self, headers: list[str], rows: list[_FakeRow]):
        self._headers = [_FakeCell("")] + [_FakeCell(h) for h in headers]
        self._rows = rows

    def find_elements(self, by=None, value=None):
        if value == "tr.cm-grid-row-header td.cm-grid-column-header, tr.cm-grid-row-header th.cm-grid-column-header":
            return self._headers
        if value == "tr.cm-grid-row":
            return self._rows
        return []


class _FakeDriver:
    def __init__(self, tables):
        self._tables = tables

    def find_elements(self, by=None, value=None):
        if value == "div.cm-element[data-type='GRID'] table.cm-grid-response-set":
            return self._tables
        return []


def _strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(c))


def test_extract_cmix_grid_question_blocks_builds_one_radio_block_per_row(monkeypatch):
    table = _FakeTable(
        headers=["Oui", "Non"],
        rows=[
            _FakeRow("Études de marché", [_FakeRadio("60973696", "225375880"), _FakeRadio("60973696", "225375881")]),
            _FakeRow("Publicité", [_FakeRadio("60973697", "225375882"), _FakeRadio("60973697", "225375883")]),
        ],
    )
    driver = _FakeDriver([table])

    monkeypatch.setattr(dem, "register_target", lambda *_, **__: None)
    monkeypatch.setattr(dem, "make_target_id", lambda *_, **__: "cmix_grid_target")

    blocks = dem._extract_cmix_grid_question_blocks(driver, frame_chain=[])

    assert len(blocks) == 2
    assert "etudes de marche" in _strip_accents(blocks[0]["question"]).lower()
    assert blocks[0]["itype"] == "radio"
    assert blocks[0]["options"] == ["Oui", "Non"]
    assert blocks[0]["context"]["cmix_grid"] is True
    assert "publicite" in _strip_accents(blocks[1]["question"]).lower()
    assert blocks[1]["options"] == ["Oui", "Non"]
