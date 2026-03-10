from selenium.webdriver.common.by import By

from Survey import input_radio as ir


class _Head:
    def __init__(self, text, hid=None):
        self.text = text
        self._id = hid

    def get_attribute(self, name):
        return self._id if name == "id" else None


class _Input:
    def __init__(self, iid, labelled, checked=False):
        self._id = iid
        self._labelled = labelled
        self._checked = checked

    def get_attribute(self, name):
        if name == "id":
            return self._id
        if name == "aria-labelledby":
            return self._labelled
        if name == "checked":
            return "checked" if self._checked else ""
        return ""

    def is_selected(self):
        return self._checked

    def click(self):
        return None


class _Label:
    def __init__(self, fid):
        self._for = fid

    def get_attribute(self, name):
        return self._for if name == "for" else ""

    def click(self):
        return None


class _Cell:
    def __init__(self, inp):
        self._inp = inp

    def find_elements(self, by, value):
        if by == By.XPATH and value == ".//input[@type='radio']":
            return [self._inp]
        return []

    def find_element(self, by, value):
        raise Exception("not found")

    def get_attribute(self, _name):
        return ""


class _Row:
    def __init__(self, row_text, row_id, inp):
        self._row_text = row_text
        self._row_id = row_id
        self._inp = inp
        self._cell = _Cell(inp)

    def find_element(self, by, value):
        if by == By.XPATH and value in (".//th", "./td[1]", "./td[2]"):
            return _Head(self._row_text, self._row_id)
        if by == By.XPATH and value == ".//input[@type='radio']":
            return self._inp
        raise Exception(f"not found: {value}")

    def find_elements(self, by, value):
        if by == By.XPATH and value == "./td":
            return [self._cell]
        if by == By.XPATH and value == ".//input[@type='radio']":
            return [self._inp]
        return []


class _Table:
    def __init__(self, rows):
        self._rows = rows
        self._heads = [_Head("Non", "QR9_c2")]

    def find_elements(self, by, value):
        if by == By.XPATH and value == ".//tr[1]//th[normalize-space(.)!='']":
            return self._heads
        if by == By.XPATH and value == ".//tr[contains(@class,'row-elements')]":
            return self._rows
        return []

    def find_element(self, by, value):
        if by == By.XPATH and value.startswith(".//label[@for="):
            return _Label("ans10225.1.1")
        raise Exception("not found")


class _Scope:
    def __init__(self, table):
        self._table = table

    def find_element(self, by, value):
        if by == By.XPATH and "table[contains(@class,'grid')]" in value:
            return self._table
        raise Exception("not found")


class _Driver:
    def execute_script(self, _script, *_args):
        return None


def test_decipher_grid_requires_row_context_on_multirow(monkeypatch):
    rows = [_Row("row1", "QR9_r1_left", _Input("i1", "QR9_r1_left QR9_c2", False)), _Row("row2", "QR9_r2_left", _Input("i2", "QR9_r2_left QR9_c2", False))]
    monkeypatch.setattr(ir, "find_questions_container", lambda *_: _Scope(_Table(rows)))

    assert ir.click_decipher_grid_radio(_Driver(), "Non", "") is False


def test_decipher_grid_fails_when_exact_native_input_not_checked(monkeypatch):
    rows = [_Row("Le bâtiment", "QR9_r2_left", _Input("ans10225.1.1", "QR9_r2_left QR9_c2", False))]
    monkeypatch.setattr(ir, "find_questions_container", lambda *_: _Scope(_Table(rows)))

    assert ir.click_decipher_grid_radio(_Driver(), "Non", "Le bâtiment") is False
