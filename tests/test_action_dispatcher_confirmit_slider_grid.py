from surveybot.Survey import action_dispatcher as ad


class _FakeSwitchTo:
    def default_content(self):
        return None


class _FakeDriver:
    def __init__(self):
        self.switch_to = _FakeSwitchTo()
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append((script, args))
        if "confirmit_slider_grid_apply_v1" in script:
            row_id, selected_index = args
            if row_id == "Q9_10" and int(selected_index) == 2:
                return {"ok": True, "desired": "1", "now": "1"}
            return {"ok": False, "reason": "bad_args"}
        return None


def test_apply_by_target_id_confirmit_slider_grid_uses_slider_handle(monkeypatch, capsys):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    payload = {
        "kind": "group",
        "itype": "radio",
        "confirmit_slider_grid": True,
        "slider_grid_row_id": "Q9_10",
        "slider_grid_scale_labels": [
            "1 - Pas du tout d’accord",
            "2",
            "3",
            "4",
            "5 - Tout à fait d’accord",
            "Ne s’applique pas à ma situation",
        ],
        "slider_grid_code_to_index": {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "99": 6},
    }

    monkeypatch.setattr(ad, "get_target", lambda _tid: payload)

    driver = _FakeDriver()
    assert ad._apply_by_target_id(driver, "tid-1", "radio", "2") is True

    out = capsys.readouterr().out
    assert "slider-grid row applied" in out
    assert any("confirmit_slider_grid_apply_v1" in call[0] for call in driver.calls)
