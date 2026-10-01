from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch

import Survey.dom_analyzer as analyzer
from Survey.external_fix_registry import ExternalFixRegistry
from Survey.external_fix_hidden_consent_radio_question import (
    FIXES,
    _extract_visible_radio_question_blocks,
)
from Survey.external_fix_loader import MODULE_IMPORTS


def _parse_selector(selector: str) -> tuple[str | None, list[str]]:
    match = re.match(r"^([a-zA-Z0-9_-]*)((?:\.[a-zA-Z0-9_-]+)*)$", selector)
    assert match is not None, selector
    tag = match.group(1) or None
    classes = [c for c in match.group(2).split(".") if c]
    return tag, classes


class FakeElement:
    def __init__(self, tag: str, *, classes: tuple[str, ...] = (), attrs: dict | None = None,
                 text: str = "", children: tuple["FakeElement", ...] = ()) -> None:
        self.tag = tag
        self.classes = set(classes)
        self.attrs = dict(attrs or {})
        self.text = text
        self.children = list(children)

    def get_attribute(self, name: str) -> str | None:
        if name == "class":
            return " ".join(sorted(self.classes)) or None
        return self.attrs.get(name)

    def inner_text(self) -> str:
        return self.text

    def _matches(self, selector: str) -> bool:
        tag, classes = _parse_selector(selector)
        if tag and self.tag != tag:
            return False
        return all(c in self.classes for c in classes)

    def _walk(self):
        for child in self.children:
            yield child
            yield from child._walk()

    def query_selector(self, selector: str):
        for node in self._walk():
            if node._matches(selector):
                return node
        return None

    def query_selector_all(self, selector: str) -> list["FakeElement"]:
        return [node for node in self._walk() if node._matches(selector)]


def _answer_row(rid: str, name: str, label: str) -> FakeElement:
    return FakeElement(
        "div",
        classes=("answer_options",),
        children=(
            FakeElement("input", classes=("radioQT",), attrs={"id": rid, "name": name}),
            FakeElement("div", classes=("option_label",), text=label),
        ),
    )


def _visible_widget() -> FakeElement:
    return FakeElement(
        "div",
        classes=("question", "radio_question"),
        children=(
            FakeElement("div", classes=("radio_q_text",), text="Etes-vous...?"),
            _answer_row("q1001_a1", "q1001", "Un homme"),
            _answer_row("q1001_a2", "q1001", "Une femme"),
        ),
    )


class HiddenConsentRadioQuestionFixTests(unittest.TestCase):
    def test_extracts_visible_widget_and_registers_target(self) -> None:
        root = FakeElement("div", children=(_visible_widget(),))
        with patch("Survey.external_fix_hidden_consent_radio_question.register_target") as reg:
            blocks = _extract_visible_radio_question_blocks(root, None)
        self.assertIsNotNone(blocks)
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block["itype"], "radio")
        self.assertEqual(block["question"], "Etes-vous...?")
        self.assertEqual(block["options"], ["Un homme", "Une femme"])
        self.assertEqual(block["context"]["group_key"], "radio:name:q1001")
        reg.assert_called_once()
        target_id, payload = reg.call_args[0]
        self.assertEqual(target_id, block["target_id"])
        self.assertEqual(len(payload["option_xpath_map"]), 2)
        for xpath in payload["option_xpath_map"].values():
            self.assertIn("@id=", xpath)
            self.assertNotIn("input_field_radio", xpath)

    def test_hidden_div_widget_is_excluded(self) -> None:
        hidden_widget = FakeElement(
            "div",
            classes=("question", "radio_question", "hidden_div"),
            children=(
                FakeElement("div", classes=("radio_q_text",), text="Masqué"),
                _answer_row("h_a1", "hq", "A"),
                _answer_row("h_a2", "hq", "B"),
            ),
        )
        root = FakeElement("div", children=(hidden_widget,))
        with patch("Survey.external_fix_hidden_consent_radio_question.register_target"):
            self.assertIsNone(_extract_visible_radio_question_blocks(root, None))

    def test_no_widget_returns_none(self) -> None:
        root = FakeElement("div", children=())
        self.assertIsNone(_extract_visible_radio_question_blocks(root, None))

    def test_fix_declaration_registers_against_real_baseline(self) -> None:
        real_root = Path(analyzer.__file__).resolve().parent
        registry = ExternalFixRegistry(root=real_root)
        self.assertEqual(len(FIXES), 1)
        for fix in FIXES:
            registry.register(fix)
        fix = FIXES[0]
        self.assertEqual(fix.stage, "extraction")
        self.assertEqual(fix.position, "before")
        self.assertEqual(
            fix.anchor_function_id,
            "dom_extractors_misc.py::_extract_consent_modal_radio_block",
        )
        self.assertIn("20260929_033834_extraction_validation_failure", fix.case_ids)

    def test_loader_activates_this_module(self) -> None:
        found = False
        for load in MODULE_IMPORTS:
            fixes = load()
            if any(fix.fix_id == "hidden_consent_modal_visible_radio_question" for fix in fixes):
                found = True
        self.assertTrue(found, "module non branché dans Survey/external_fix_loader.py")


if __name__ == "__main__":
    unittest.main()
