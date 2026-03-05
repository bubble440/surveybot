import unicodedata

from surveybot.Survey import dom_extractors_misc as dem


class _FakeCell:
    def __init__(self, text: str, class_name: str = ""):
        self.text = text
        self.class_name = class_name

    def get_attribute(self, name):
        if name == "innerText":
            return self.text
        if name == "class":
            return self.class_name
        return ""


class _FakeRadio:
    def __init__(self, name: str, value: str):
        self._attrs = {"name": name, "value": value}

    def get_attribute(self, name):
        return self._attrs.get(name, "")


class _FakeRow:
    def __init__(self, row_label: str, radios: list[_FakeRadio], generic_row_header_only: bool = False):
        self._row_hdr = _FakeCell(row_label)
        self._radios = radios
        self._generic_row_header_only = generic_row_header_only

    def find_element(self, by=None, value=None):
        if self._generic_row_header_only and value == (
            "td.cm-grid-column-header-1, th.cm-grid-column-header-1, "
            "td.cm-grid-column-header, th.cm-grid-column-header"
        ):
            return self._row_hdr
        if value == (
            "td.cm-grid-column-header-1, th.cm-grid-column-header-1, "
            "td.cm-grid-column-header, th.cm-grid-column-header"
        ):
            return self._row_hdr
        raise Exception("not found")

    def find_elements(self, by=None, value=None):
        if value == "input[type='radio'][name][value]":
            return self._radios
        return []


class _FakeTable:
    def __init__(self, headers: list[str], rows: list[_FakeRow], header_row_classed: bool = True):
        self._headers = [_FakeCell("", "cm-grid-column-header cm-grid-column-header-1")] + [
            _FakeCell(h, f"cm-grid-column-{idx}") for idx, h in enumerate(headers, start=1)
        ]
        self._rows = rows
        self._header_row_classed = header_row_classed

    def find_elements(self, by=None, value=None):
        if value == "tr.cm-grid-row-header td, tr.cm-grid-row-header th":
            return self._headers if self._header_row_classed else []
        if value == "tr:first-child td, tr:first-child th":
            return self._headers
        if value == "tr[data-response-batch]":
            return self._rows
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


def test_extract_cmix_grid_question_blocks_accepts_generic_row_header_class(monkeypatch):
    table = _FakeTable(
        headers=["Oui", "Non"],
        rows=[
            _FakeRow(
                "Studios / Films / Promotion cinématographique",
                [_FakeRadio("60973699", "225375886"), _FakeRadio("60973699", "225375887")],
                generic_row_header_only=True,
            ),
        ],
    )
    driver = _FakeDriver([table])

    monkeypatch.setattr(dem, "register_target", lambda *_, **__: None)
    monkeypatch.setattr(dem, "make_target_id", lambda *_, **__: "cmix_grid_target")

    blocks = dem._extract_cmix_grid_question_blocks(driver, frame_chain=[])

    assert len(blocks) == 1
    assert "studios / films" in _strip_accents(blocks[0]["question"]).lower()
    assert blocks[0]["options"] == ["Oui", "Non"]


def test_extract_cmix_grid_question_blocks_uses_first_row_headers_when_row_header_class_missing(monkeypatch):
    table = _FakeTable(
        headers=["Oui", "Non"],
        rows=[
            _FakeRow("Banque", [_FakeRadio("60973698", "225375884"), _FakeRadio("60973698", "225375885")]),
        ],
        header_row_classed=False,
    )
    driver = _FakeDriver([table])

    monkeypatch.setattr(dem, "register_target", lambda *_, **__: None)
    monkeypatch.setattr(dem, "make_target_id", lambda *_, **__: "cmix_grid_target")

    blocks = dem._extract_cmix_grid_question_blocks(driver, frame_chain=[])

    assert len(blocks) == 1
    assert blocks[0]["question"] == "Banque"
    assert blocks[0]["options"] == ["Oui", "Non"]
