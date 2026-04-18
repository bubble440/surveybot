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

_ASPNET_SYSTEM_FIELDS = {
    "__viewstate",
    "__viewstategenerator",
    "__eventvalidation",
    "__eventtarget",
    "__eventargument",
    "__lastfocus",
    "__scrollpositionx",
    "__scrollpositiony",
}

def _norm_lc(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def _looks_like_system_field(name_or_id: str | None) -> bool:
    v = _norm_lc(name_or_id)
    if not v:
        return False
    if v in _ASPNET_SYSTEM_FIELDS:
        return True
    # Certains sites encapsulent avec prefixes/suffixes
    if any(tok in v for tok in ["__viewstate", "__eventvalidation", "__viewstategenerator", "__eventtarget", "__eventargument"]):
        return True
    return False

def _is_hidden_like(attrs: dict) -> bool:
    """
    Heuristique cheap & robuste (pas besoin de Selenium ici).
    attrs: dict d'attributs HTML (type/name/id/style/class/hidden/aria-hidden...)
    """
    t = _norm_lc(attrs.get("type"))
    if t == "hidden":
        return True

    # attribut HTML hidden / aria-hidden
    if "hidden" in attrs and attrs.get("hidden") is not None:
        return True
    if _norm_lc(attrs.get("aria-hidden")) in {"true", "1"}:
        return True

    style = _norm_lc(attrs.get("style"))
    if "display:none" in style or "visibility:hidden" in style:
        return True

    # champs système ASP.NET
    if _looks_like_system_field(attrs.get("id")) or _looks_like_system_field(attrs.get("name")):
        return True

    return False

def _infer_itype(tag_name: str, attrs: dict) -> str:
    """
    Corrige notamment le cas <input type="submit" ...> (CTA Continue)
    """
    tag = _norm_lc(tag_name)
    t = _norm_lc(attrs.get("type"))

    if tag == "button":
        return "button"

    if tag == "input":
        if t in {"submit", "button", "reset", "image"}:
            return "button"
        if t == "radio":
            return "radio"
        if t == "checkbox":
            return "checkbox"
        # text-like
        if t in {"", "text", "email", "tel", "number", "search", "password", "url"}:
            return "text"
        # default safe
        return "text"

    if tag == "select":
        return "dropdown"
    if tag == "textarea":
        return "text"

    return "unknown"

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


def _extract_matrix_grid_block(group: List[WebElement]) -> Optional[QuestionBlock]:
    """
    Nfield mrQuestionTable: radios have rowid/colid attrs; labels are in header
    td.mrGridQuestionText cells, not in label[for] elements (which are empty spans).
    Only activates when rowid+colid present AND ancestor table.mrQuestionTable exists.
    """
    first = group[0]
    if first.get_attribute("rowid") is None or first.get_attribute("colid") is None:
        return None

    # Row label from td.mrGridCategoryText in the same <tr>
    row_label = ""
    try:
        tr = first.find_element(By.XPATH, "ancestor::tr[1]")
        cat_td = tr.find_element(By.CSS_SELECTOR, "td.mrGridCategoryText")
        row_label = re.sub(r"\s+", " ", (cat_td.get_attribute("innerText") or cat_td.text or "")).strip()
    except Exception:
        pass

    # Column headers from the header row of the enclosing mrQuestionTable
    col_headers: dict = {}
    try:
        table = first.find_element(
            By.XPATH, "ancestor::table[contains(@class,'mrQuestionTable')][1]"
        )
        header_cells = table.find_elements(By.CSS_SELECTOR, "td.mrGridQuestionText")
        for idx, cell in enumerate(header_cells):
            txt = re.sub(r"\s+", " ", (cell.get_attribute("innerText") or cell.text or "")).strip()
            col_headers[idx] = txt
    except Exception:
        pass

    if not row_label and not col_headers:
        return None

    options = []
    for r in group:
        try:
            colid = int(r.get_attribute("colid") or 0)
        except (ValueError, TypeError):
            colid = len(options)
        options.append(col_headers.get(colid, f"col{colid}"))

    return QuestionBlock(
        itype="radio",
        label=row_label or f"row{first.get_attribute('rowid')}",
        dom_el=group[0],
        container=None,
        options=options or None,
    )


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
        # Matrix grid (Nfield mrQuestionTable): rowid/colid attrs present
        matrix_block = _extract_matrix_grid_block(group)
        if matrix_block is not None:
            blocks.append(matrix_block)
            continue

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

def _detect_aria_radios(scope: WebElement) -> List[QuestionBlock]:
    """
    Détecte les boutons radio implémentés via role="button" (CloudResearch, Vue.js, React, etc.).
    
    Ces frameworks modernes n'utilisent pas <input type="radio"> mais des divs avec:
    - role="button" (ARIA)
    - Classes spécifiques: .choice-option, .random-choice, etc.
    - Tabindex pour la navigation clavier
    
    Exemples:
    - CloudResearch/Sentry: div[role="button"].choice-option.random-choice
    - Autres Vue.js/React: div[role="button"][tabindex]
    """
    blocks = []
    
    # Sélecteurs pour différents patterns de boutons radio ARIA
    selectors = [
        '[role="button"].choice-option',
        '[role="button"].random-choice',
        # Fallback: div avec role="button" et tabindex dans un contexte de choix multiples
        'div[tabindex][role="button"]',
    ]
    
    all_buttons = []
    for selector in selectors:
        try:
            buttons = scope.find_elements(By.CSS_SELECTOR, selector)
            all_buttons.extend(buttons)
        except Exception:
            continue
    
    # Dédupliquer (un bouton peut matcher plusieurs sélecteurs)
    seen = set()
    unique_buttons = []
    for btn in all_buttons:
        try:
            btn_id = id(btn)
            if btn_id not in seen:
                seen.add(btn_id)
                unique_buttons.append(btn)
        except Exception:
            continue
    
    # Filtrer les boutons visibles avec texte
    visible_buttons = []
    for btn in unique_buttons:
        try:
            if not _visible(btn):
                continue
            
            # CORRECTION: Extraction robuste du texte
            # Les frameworks modernes ont souvent le texte dans des divs imbriqués,
            # et btn.text peut retourner une chaîne vide. On essaie plusieurs méthodes:
            text = None
            
            # Méthode 1: .text (propriété Selenium standard)
            try:
                text = btn.text
                if text:
                    text = text.strip()
            except Exception:
                pass
            
            # Méthode 2: innerText (recommandé pour les éléments avec du texte visible)
            if not text or len(text) < 1:
                try:
                    text = btn.get_attribute('innerText')
                    if text:
                        text = text.strip()
                except Exception:
                    pass
            
            # Méthode 3: textContent (fallback, inclut aussi le texte masqué)
            if not text or len(text) < 1:
                try:
                    text = btn.get_attribute('textContent')
                    if text:
                        text = text.strip()
                except Exception:
                    pass
            
            # Si toujours pas de texte, ignorer ce bouton
            if not text or len(text) < 1:
                continue
            
            visible_buttons.append((btn, text))
            
        except Exception:
            continue
    
    # Si on trouve au moins 2 boutons visibles, c'est probablement un groupe de radios
    if len(visible_buttons) >= 2:
        options = [text for btn, text in visible_buttons]
        
        blocks.append(
            QuestionBlock(
                itype="radio",
                label=" / ".join(options),
                dom_el=visible_buttons[0][0],  # Premier élément du tuple (btn, text)
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
    blocks.extend(_detect_aria_radios(scope))
    blocks.extend(_detect_checkboxes(scope))
    blocks.extend(_detect_text_inputs(scope))
    blocks.extend(_detect_buttons(scope))

    return blocks