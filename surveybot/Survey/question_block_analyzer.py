# Survey/question_block_analyzer.py
# ------------------------------------------------------------
# Question Block Analyzer
#
# Objectif :
# Construire une carte logique locale des inputs d'une question
# (dropdown, radio, checkbox, text, button)
#
# - AUCUN mapping global
# - AUCUNE hypothèse d'ordre DOM
# - Analyse purement DOM + texte
#
# Compatible avec OpenAI :
#   réponse //// itype //// contexte
#
# Conçu pour :
# - 100+ bots
# - DOM dynamiques (React, Bootstrap, Decipher, etc.)
# ------------------------------------------------------------

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement


# ------------------------------------------------------------
# Utils texte
# ------------------------------------------------------------

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _visible(el: WebElement) -> bool:
    try:
        if not el.is_displayed():
            return False
        r = el.rect or {}
        return r.get("width", 0) > 10 and r.get("height", 0) > 10
    except Exception:
        return False


# ------------------------------------------------------------
# Dataclass centrale
# ------------------------------------------------------------

@dataclass
class QuestionBlock:
    itype: str                  # dropdown, radio, checkbox, text, button
    label: str                  # texte humain ("Année", "Oui", etc.)
    dom_el: WebElement          # élément principal
    container: Optional[WebElement]
    options: Optional[List[str]] = None


# ------------------------------------------------------------
# Extraction du scope question
# ------------------------------------------------------------

def _find_question_container(driver) -> WebElement:
    """
    Trouve le conteneur DOM principal de la question courante.
    Heuristiques empilées, robustes.
    """
    selectors = [
        "div[id*='question']",
        "div[class*='question']",
        "form",
        "body",
    ]

    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                if _visible(el):
                    return el
        except Exception:
            continue

    # fallback ultime
    return driver.find_element(By.TAG_NAME, "body")


# ------------------------------------------------------------
# Label detection (clé du mapping)
# ------------------------------------------------------------

def _extract_label(el: WebElement) -> str:
    """
    Essaie d'associer un texte humain à un input.
    """
    try:
        # 1) label[for=id]
        el_id = el.get_attribute("id")
        if el_id:
            labels = el.find_elements(
                By.XPATH,
                f"//label[@for='{el_id}']"
            )
            if labels:
                return labels[0].text.strip()
    except Exception:
        pass

    # 2) aria-label / placeholder
    for attr in ("aria-label", "placeholder", "name"):
        try:
            v = el.get_attribute(attr)
            if v and len(v.strip()) >= 2:
                return v.strip()
        except Exception:
            pass

    # 3) texte proche (parents)
    try:
        parent = el.find_element(By.XPATH, "ancestor::*[self::div or self::td or self::li][1]")
        txt = parent.text.strip()
        if txt and len(txt) >= 2:
            return txt
    except Exception:
        pass

    return ""


# ------------------------------------------------------------
# Détecteurs par type
# ------------------------------------------------------------

def _detect_dropdowns(scope: WebElement) -> List[QuestionBlock]:
    blocks = []
    selects = scope.find_elements(By.TAG_NAME, "select")

    for sel in selects:
        if not _visible(sel):
            continue

        opts = []
        try:
            for o in sel.find_elements(By.TAG_NAME, "option"):
                if o.get_attribute("disabled"):
                    continue
                t = (o.text or "").strip()
                if t:
                    opts.append(t)
        except Exception:
            pass

        label = _extract_label(sel)

        blocks.append(
            QuestionBlock(
                itype="dropdown",
                label=label,
                dom_el=sel,
                container=None,
                options=opts or None,
            )
        )
    return blocks


def _detect_radios(scope: WebElement) -> List[QuestionBlock]:
    blocks = []
    radios = scope.find_elements(By.CSS_SELECTOR, "input[type='radio']")

    groups = {}
    for r in radios:
        if not _visible(r):
            continue
        name = r.get_attribute("name") or id(r)
        groups.setdefault(name, []).append(r)

    for group in groups.values():
        options = []
        for r in group:
            lbl = _extract_label(r)
            if lbl:
                options.append(lbl)

        if options:
            blocks.append(
                QuestionBlock(
                    itype="radio",
                    label=" / ".join(options),
                    dom_el=group[0],
                    container=None,
                    options=options,
                )
            )
    return blocks


def _detect_checkboxes(scope: WebElement) -> List[QuestionBlock]:
    blocks = []
    boxes = scope.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")

    for cb in boxes:
        if not _visible(cb):
            continue
        label = _extract_label(cb)
        if not label:
            continue

        blocks.append(
            QuestionBlock(
                itype="checkbox",
                label=label,
                dom_el=cb,
                container=None,
                options=None,
            )
        )
    return blocks


def _detect_text_inputs(scope: WebElement) -> List[QuestionBlock]:
    blocks = []
    inputs = scope.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea")

    for inp in inputs:
        if not _visible(inp):
            continue

        label = _extract_label(inp)

        blocks.append(
            QuestionBlock(
                itype="text",
                label=label,
                dom_el=inp,
                container=None,
                options=None,
            )
        )
    return blocks


def _detect_buttons(scope: WebElement) -> List[QuestionBlock]:
    blocks = []
    buttons = scope.find_elements(
        By.CSS_SELECTOR,
        "button, a[role='button'], input[type='button'], input[type='submit']"
    )

    for btn in buttons:
        if not _visible(btn):
            continue

        txt = (btn.text or "").strip()
        if not txt:
            continue

        # on ignore les boutons globaux évidents
        if _norm(txt) in {"suivant", "next", "continue", "continuer"}:
            continue

        blocks.append(
            QuestionBlock(
                itype="button",
                label=txt,
                dom_el=btn,
                container=None,
                options=None,
            )
        )
    return blocks


# ------------------------------------------------------------
# API publique
# ------------------------------------------------------------

def analyze_question_blocks(driver) -> List[QuestionBlock]:
    """
    Point d'entrée UNIQUE.
    Retourne la carte logique des inputs de la question courante.
    """
    scope = _find_question_container(driver)

    blocks: List[QuestionBlock] = []
    blocks.extend(_detect_dropdowns(scope))
    blocks.extend(_detect_radios(scope))
    blocks.extend(_detect_checkboxes(scope))
    blocks.extend(_detect_text_inputs(scope))
    blocks.extend(_detect_buttons(scope))

    return blocks
