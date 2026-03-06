import inspect

from surveybot.Survey import dom_analyzer


def test_mriweb_grid_text_signature_uses_name_id_to_avoid_dedupe():
    src = inspect.getsource(dom_analyzer)

    assert "in_mriweb_grid" in src
    assert "ancestor::table[contains(@class,'mrGridTable')][1]" in src
    assert "(el.get_attribute(\"name\") or \"\").strip()" in src
    assert "_extract_mriweb_grid_question_text(el)" in src
