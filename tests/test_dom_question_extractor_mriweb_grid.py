from surveybot.Survey.dom_question_extractor import _extract_mriweb_grid_question_text


class _FakeNode:
    def __init__(self, attrs=None, text="", find_map=None):
        self._attrs = attrs or {}
        self.text = text
        self._find_map = find_map or {}

    def find_elements(self, by, selector):
        return list(self._find_map.get(selector, []))

    def get_attribute(self, name):
        return self._attrs.get(name, "")


def test_extract_mriweb_grid_question_prefers_table_summary():
    grid = _FakeNode(
        attrs={
            "summary": "En pensant aux &lt;b&gt;vêtements de sport&lt;/b&gt;, quelles marques vous viennent à l’esprit&nbsp;?"
        }
    )
    el = _FakeNode(
        find_map={"ancestor::table[contains(@class,'mrGridTable')][1]": [grid]}
    )

    out = _extract_mriweb_grid_question_text(el)

    assert "pensant aux" in out.lower()
    assert "marques" in out.lower()
    assert "veillez" not in out.lower()


def test_extract_mriweb_grid_question_ignores_numeric_row_labels_on_fallback():
    q = _FakeNode(text="En pensant aux vêtements de sport, quelles marques vous viennent à l’esprit ?")
    row_number = _FakeNode(text="7")
    grid = _FakeNode(
        attrs={"summary": ""},
        find_map={
            "ancestor::div[contains(@class,'content-wrapper')][1]//span[contains(@class,'mrQuestionText') and normalize-space(.)!='' and not(ancestor::td[contains(@class,'error-block')])]": [row_number, q]
        },
    )
    el = _FakeNode(
        find_map={"ancestor::table[contains(@class,'mrGridTable')][1]": [grid]}
    )

    out = _extract_mriweb_grid_question_text(el)

    assert "pensant aux" in out.lower()
    assert "marques" in out.lower()
    assert "veillez" not in out.lower()
