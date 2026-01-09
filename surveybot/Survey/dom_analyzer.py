# Survey/dom_analyzer.py
"""
DOM Analyzer — extraction TEXT-ONLY des questions de survey.

Objectif:
- Scanner le DOM
- Identifier chaque question
- Déterminer le type d'input attendu
- Extraire les options associées
- Fournir un contexte DOM stable pour l'exécution

Aucune dépendance image.
Compatible local / prod.
Pensé pour 100+ bots.
"""

from __future__ import annotations
from typing import List, Dict, Any
import re
import unicodedata

from selenium.webdriver.common.by import By


# =========================
# Helpers texte
# =========================

def _norm(text: str) -> str:
    """Normalisation douce pour comparaison."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _is_question_text(text: str) -> bool:
    """Heuristique simple pour identifier une question."""
    if not text:
        return False
    low = text.lower()
    if "?" in text:
        return True
    keywords = [
        "what is", "which", "how", "quel", "quelle", "combien",
        "âge", "age", "gender", "education", "niveau"
    ]
    return any(k in low for k in keywords)


# =========================
# Détection du type d'input
# =========================

def _detect_itype(el) -> str:
    tag = (el.tag_name or "").lower()

    if tag == "input":
        t = (el.get_attribute("type") or "").lower()
        if t in ("radio", "checkbox", "text", "number"):
            return "radio" if t == "radio" else \
                   "checkbox" if t == "checkbox" else \
                   "text"
        return "text"

    if tag == "select":
        return "select"

    if tag == "textarea":
        return "textarea"

    if tag in ("button", "a"):
        return "button"

    role = (el.get_attribute("role") or "").lower()
    if role in ("radio", "checkbox", "button"):
        return role

    return "unknown"


# =========================
# Extraction du label / question
# =========================

def _find_associated_label(driver, el) -> str:
    """
    Cherche le texte de question associé à un input :
    - <label for=>
    - parent label
    - texte visible juste au-dessus
    """
    try:
        el_id = el.get_attribute("id")
        if el_id:
            labels = driver.find_elements(By.XPATH, f"//label[@for='{el_id}']")
            if labels:
                return _norm(labels[0].text)
    except Exception:
        pass

    try:
        parent_label = el.find_element(By.XPATH, "ancestor::label")
        if parent_label:
            return _norm(parent_label.text)
    except Exception:
        pass

    try:
        container = el.find_element(
            By.XPATH,
            "ancestor::*[self::div or self::fieldset][1]"
        )
        texts = container.text.splitlines()
        for line in texts:
            line = _norm(line)
            if _is_question_text(line):
                return line
    except Exception:
        pass

    return ""


# =========================
# Extraction des options
# =========================

def _extract_options(driver, el, itype: str) -> List[str]:
    options = []

    try:
        if itype == "select":
            opts = el.find_elements(By.TAG_NAME, "option")
            for o in opts:
                txt = _norm(o.text)
                if txt:
                    options.append(txt)

        elif itype in ("radio", "checkbox"):
            name = el.get_attribute("name")
            if name:
                group = driver.find_elements(By.XPATH, f"//input[@name='{name}']")
            else:
                group = [el]

            for g in group:
                label = _find_associated_label(driver, g)
                if label:
                    options.append(label)

    except Exception:
        pass

    # dédoublonnage conservant l'ordre
    return list(dict.fromkeys(options))


# =========================
# API principale
# =========================

def analyze_dom(driver) -> List[Dict[str, Any]]:
    """
    Analyse le DOM courant et retourne une liste de QuestionBlock.
    """
    question_blocks: List[Dict[str, Any]] = []

    inputs = driver.find_elements(
        By.CSS_SELECTOR,
        "input, select, textarea, button, [role='radio'], [role='checkbox']"
    )

    seen = set()

    for el in inputs:
        try:
            itype = _detect_itype(el)
            if itype == "unknown":
                continue

            question = _find_associated_label(driver, el)
            if not question:
                continue

            signature = (question, itype)
            if signature in seen:
                continue
            seen.add(signature)

            options = _extract_options(driver, el, itype)

            block = {
                "question": question,
                "itype": itype,
                "options": options,
                "context": {
                    "tag": el.tag_name,
                    "name": el.get_attribute("name"),
                    "id": el.get_attribute("id"),
                    "role": el.get_attribute("role"),
                },
            }

            question_blocks.append(block)

        except Exception:
            continue

    return question_blocks
