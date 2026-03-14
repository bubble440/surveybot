from surveybot.Survey.dom_utils import _looks_like_system_field


class _FakeElement:
    def __init__(self, tag_name: str, attrs: dict[str, str] | None = None):
        self.tag_name = tag_name
        self._attrs = attrs or {}

    def get_attribute(self, name: str):
        return self._attrs.get(name, "")


def test_looks_like_system_field_filters_qualtrics_language_selector():
    el = _FakeElement("select", {"name": "Q_lang", "id": "Q_lang", "class": "Q_lang"})

    assert _looks_like_system_field(el) is True


def test_looks_like_system_field_does_not_filter_regular_select():
    el = _FakeElement("select", {"name": "region", "id": "region"})

    assert _looks_like_system_field(el) is False
