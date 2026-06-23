# Survey/dom_utils.py
"""
DOM Utilities - Fonctions utilitaires génériques pour l'analyse DOM.

Ce module contient les helpers de bas niveau pour :
- Normalisation de texte (_norm, _norm_lc, _norm_key)
- Détection d'éléments système (_looks_like_system_field)
- Visibilité d'éléments (_is_actionable_visible)
- Génération XPath (_best_xpath_for_element, _xpath_literal)
- Classification basique (_is_question_text, _is_validation_instruction, _detect_itype)
- Hints dropdown (_dropdown_field_hint)
- Variables d'environnement (_env_truthy)
"""

from __future__ import annotations
from typing import List
import re
import os
import unicodedata


def _pw_page(d):
    """Extrait la Page Playwright native depuis un PlaywrightDriverShim ou retourne d tel quel."""
    if hasattr(d, "_page"):
        return d._page
    return d


# ================================================================================
# CONSTANTES
# ================================================================================

_SYS_FIELD_TOKENS = {
    "token", "csrf", "hidden", "antiforgery", "__viewstate", "__eventvalidation",
    "confirm-clearall", "confirm", "clear", "reset", "submit", "__requestverificationtoken"
}

_VALIDATION_INSTRUCTION_PATTERNS = [
    re.compile(r"please\s+(select|choose|enter|provide)", re.IGNORECASE),
    re.compile(r"required\s+field", re.IGNORECASE),
    re.compile(r"must\s+(be|select|choose|enter)", re.IGNORECASE),
    re.compile(r"can\s+not\s+be\s+blank", re.IGNORECASE),
    re.compile(r"error\s*:", re.IGNORECASE),
    re.compile(r"invalid\s+(selection|input|entry)", re.IGNORECASE),
]

# ================================================================================
# NORMALISATION TEXTE
# ================================================================================

def _norm(text: str) -> str:
    """Normalise du texte : collapse whitespace, strip."""
    if not text:
        return ""
    # Normalisation unicode
    text = unicodedata.normalize("NFKD", text)
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


def _norm_lc(text: str) -> str:
    """Normalise en lowercase."""
    return _norm(text).lower()


def _norm_key(text: str) -> str:
    """Normalise un texte pour servir de clé (lowercase, collapse whitespace)."""
    return _norm_lc(text)


# ================================================================================
# DÉTECTION ÉLÉMENTS SYSTÈME
# ================================================================================

def _looks_like_system_field(el) -> bool:
    """
    Retourne True si l'élément ressemble à un champ système (hidden token, CSRF, etc.).
    Critères : name/id contient un token système OU type=hidden.
    """
    try:
        tag_name = el.evaluate("e => e.tagName.toLowerCase()")
        if tag_name not in ("input", "select", "textarea"):
            return False

        input_type = el.get_attribute("type") or ""
        if input_type.lower() == "hidden":
            return True

        name_val = (el.get_attribute("name") or "").lower()
        id_val = (el.get_attribute("id") or "").lower()

        # Qualtrics language selector UI (non-question field)
        if tag_name == "select" and name_val == "q_lang":
            return True

        for token in _SYS_FIELD_TOKENS:
            if token in name_val or token in id_val:
                return True

        return False
    except Exception:
        return False


# ================================================================================
# VISIBILITÉ
# ================================================================================

def _is_actionable_visible(el) -> bool:
    """
    Retourne True si l'élément est actionnable/visible par l'utilisateur.

    Gère les cas spéciaux :
    - Inputs masqués mais avec wrapper visible (Decipher/FocusVision: clickableCell, sq-cardrating-button)
    - Inputs masqués mais label visible (custom UI)
    - Exclusion des blocs LimeSurvey masqués (ls-js-hidden)

    Stratégie :
    1. Vérifier que l'élément n'est pas dans un bloc ls-js-hidden (LimeSurvey)
    2. Si input type=hidden → chercher wrapper cliquable parent
    3. Sinon, vérifier is_visible() standard
    """
    try:
        # 0) LimeSurvey: ignorer tout ce qui est dans un bloc masqué "ls-js-hidden"
        try:
            if el.query_selector_all(
                "xpath=ancestor-or-self::*[contains(concat(' ',normalize-space(@class),' '),' ls-js-hidden ')][1]",
            ):
                return False
        except Exception:
            pass

        # 1) Cas spécial: input type=hidden mais wrapper cliquable visible
        #    (Decipher/FocusVision: clickableCell, sq-cardrating-button, etc.)
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        if tag == "input":
            input_type = (el.get_attribute("type") or "").lower()
            if input_type == "hidden":
                # Chercher un parent cliquable
                try:
                    cliquable_wrappers = el.query_selector_all(
                        "xpath=ancestor::*[contains(@class,'clickableCell') or "
                        "contains(@class,'sq-cardrating-button') or "
                        "contains(@class,'clickable')]"
                    )
                    if cliquable_wrappers:
                        # Vérifier que le wrapper est visible
                        for wrapper in cliquable_wrappers:
                            if wrapper.is_visible():
                                return True
                except Exception:
                    pass
                # Pas de wrapper cliquable visible → pas actionnable
                return False

        # 2) Cas spécial: <select> masqué mais widget visible (bootstrap-select / custom select)
        if tag == "select":
            try:
                if el.is_visible():
                    return True
            except Exception:
                pass

            try:
                wrappers = el.query_selector_all(
                    "xpath="
                    "ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' bootstrap-select ') "
                    "or contains(concat(' ', normalize-space(@class), ' '), ' bs-select-hidden ') "
                    "or contains(concat(' ', normalize-space(@class), ' '), ' selectpicker ')][1]"
                    "|following-sibling::*[contains(concat(' ', normalize-space(@class), ' '), ' bootstrap-select ')][1]",
                )
                for wrapper in wrappers:
                    if wrapper.is_visible():
                        return True
            except Exception:
                pass
            # Fieldset progressif (ex: surveys.insights-today.com prescreener) :
            # l'élément est masqué côté CSS mais présent dans le DOM sous un
            # <fieldset> portant un <legend class="qualification-text">.
            try:
                if el.query_selector_all(
                    "xpath=ancestor::fieldset[1]//*[contains(@class,'qualification-text')]",
                ):
                    return True
            except Exception:
                pass
            # GfK mrIWeb: select.mrDropdown est dans un .platform_clone masqué ;
            # le widget custom .combo_master visible est dans le même .acc_ct parent.
            try:
                el_classes = (el.get_attribute("class") or "").lower()
                if "mrdropdown" in el_classes:
                    platform_clones = el.query_selector_all(
                        "xpath=ancestor::div[contains(concat(' ',normalize-space(@class),' '),' platform_clone ')][1]",
                    )
                    if platform_clones:
                        combo_widgets = platform_clones[0].query_selector_all(
                            "xpath=preceding-sibling::*["
                            "contains(concat(' ',normalize-space(@class),' '),' combo_master ') or "
                            "contains(concat(' ',normalize-space(@class),' '),' combo_ct ')"
                            "][1]",
                        )
                        for widget in combo_widgets:
                            if widget.is_visible():
                                return True
            except Exception:
                pass
            return False

        # 3) Cas standard: vérifier is_visible()
        if el.is_visible():
            return True
        # Fieldset progressif : même logique que pour <select> ci-dessus.
        try:
            if el.query_selector_all(
                "xpath=ancestor::fieldset[1]//*[contains(@class,'qualification-text')]",
            ):
                return True
        except Exception:
            pass
        return False

    except Exception:
        # En cas d'erreur (stale element, etc.), considérer invisible
        return False


# ================================================================================
# XPATH
# ================================================================================

def _best_xpath_for_element(driver, el) -> str:
    """
    Retourne un XPath absolu unique pour identifier l'élément.
    Stratégie: construit un chemin absolu en remontant dans la hiérarchie.
    """
    try:
        xpath = _pw_page(driver).evaluate(
            """(element) => {
            function getAbsoluteXPath(element) {
                if (!element || element.nodeType !== 1) return '';

                const parts = [];
                let current = element;

                while (current && current.nodeType === 1) {
                    let index = 0;
                    let sibling = current.previousSibling;

                    while (sibling) {
                        if (sibling.nodeType === 1 && sibling.tagName === current.tagName) {
                            index++;
                        }
                        sibling = sibling.previousSibling;
                    }

                    const tagName = current.tagName.toLowerCase();
                    const part = index > 0 ? `${tagName}[${index + 1}]` : tagName;
                    parts.unshift(part);

                    current = current.parentElement;
                }

                return '/' + parts.join('/');
            }

            return getAbsoluteXPath(element);
        }""",
            el,
        )
        return xpath if xpath else "//*"
    except Exception:
        return "//*"


def _xpath_literal(s: str) -> str:
    """
    Retourne une expression XPath literal-safe pour une chaîne.
    Gère les quotes mixtes en utilisant concat().
    """
    if '"' not in s:
        return f'"{s}"'
    if "'" not in s:
        return f"'{s}'"
    # Mixte: utiliser concat()
    parts = []
    current = ""
    for ch in s:
        if ch == '"':
            if current:
                parts.append(f'"{current}"')
                current = ""
            parts.append("'\"'")
        else:
            current += ch
    if current:
        parts.append(f'"{current}"')
    return f"concat({','.join(parts)})"


# ================================================================================
# CLASSIFICATION TEXTE
# ================================================================================

def _is_question_text(text: str) -> bool:
    """
    Retourne True si le texte ressemble à une question (heuristique).
    """
    if not text or len(text) < 3:
        return False

    text_lower = text.lower().strip()

    # Patterns d'exclusion (instructions de validation, messages d'erreur)
    if _is_validation_instruction(text):
        return False

    # Patterns positifs
    question_markers = [
        "what", "how", "why", "when", "where", "who", "which",
        "do you", "did you", "have you", "will you", "would you",
        "are you", "were you", "is it", "was it",
        "select", "choose", "indicate", "rate", "rank", "please"
    ]

    for marker in question_markers:
        if marker in text_lower:
            return True

    # Texte se terminant par '?' ou ':'
    if text.rstrip().endswith(("?", ":")):
        return True

    # Texte long (> 20 caractères) pourrait être une question
    if len(text) > 20:
        return True

    return False


def _is_validation_instruction(text: str) -> bool:
    """
    Retourne True si le texte est une instruction de validation / message d'erreur.
    """
    if not text:
        return False

    for pattern in _VALIDATION_INSTRUCTION_PATTERNS:
        if pattern.search(text):
            return True

    return False


# ================================================================================
# DÉTECTION TYPE INPUT
# ================================================================================

def _detect_itype(el) -> str:
    """
    Détecte le type d'input basé sur le tag et les attributs de l'élément.
    Retourne: 'radio', 'checkbox', 'dropdown', 'text', 'textarea', 'unknown'
    """
    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        role = (el.get_attribute("role") or "").lower().strip()

        if tag == "select":
            return "dropdown"

        if tag == "textarea":
            return "textarea"

        if tag == "input":
            input_type = (el.get_attribute("type") or "text").lower()
            if input_type in ("radio",):
                return "radio"
            if input_type in ("checkbox",):
                # MetrixLab/Toluna single-select pattern: le DOM expose des
                # <input type="checkbox" class="radioQT"> mais le comportement
                # est radio (exclusive) via wrappers .radio_question/.option_radio.
                # Scope DOM strict pour éviter d'impacter les checkboxes classiques.
                el_class = (el.get_attribute("class") or "").lower()
                if "radioqt" in el_class:
                    try:
                        has_radio_question = bool(
                            el.query_selector_all(
                                "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' radio_question ')][1]",
                            )
                        )
                    except Exception:
                        has_radio_question = False

                    try:
                        has_option_radio = bool(
                            el.query_selector_all(
                                "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' answer_options ')][1]//*[contains(concat(' ', normalize-space(@class), ' '), ' option_radio ')]",
                            )
                        )
                    except Exception:
                        has_option_radio = False

                    if has_radio_question or has_option_radio:
                        return "radio"
                return "checkbox"
            if input_type in ("text", "email", "tel", "number", "date"):
                return "text"

        # Widgets ARIA sans input natif (ex: Forsta/Confirmit answer buttons)
        if role == "radio":
            return "radio"
        if role == "checkbox":
            return "checkbox"

        return "unknown"

    except Exception:
        return "unknown"


# ================================================================================
# DROPDOWN HINT
# ================================================================================

def _dropdown_field_hint(driver, el) -> str:
    """
    Retourne un hint pour un dropdown (ex: placeholder, première option).
    """
    try:
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        if tag != "select":
            return ""

        # Chercher l'option sélectionnée ou la première option
        options = el.query_selector_all("option")
        if not options:
            return ""

        # Première option (souvent placeholder)
        first_option_text = _norm(options[0].inner_text())
        if first_option_text and len(first_option_text) < 50:
            return first_option_text

        return ""

    except Exception:
        return ""


# ================================================================================
# VARIABLE D'ENVIRONNEMENT
# ================================================================================

def _env_truthy(name: str, default: str = "0") -> bool:
    """
    Retourne True si la variable d'environnement est truthy (1, true, yes).
    """
    val = os.environ.get(name, default).lower()
    return val in ("1", "true", "yes")
