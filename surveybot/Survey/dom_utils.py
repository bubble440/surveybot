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
from selenium.webdriver.common.by import By

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
        tag_name = el.tag_name.lower()
        if tag_name not in ("input", "select", "textarea"):
            return False
        
        input_type = el.get_attribute("type") or ""
        if input_type.lower() == "hidden":
            return True
        
        name_val = (el.get_attribute("name") or "").lower()
        id_val = (el.get_attribute("id") or "").lower()
        
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
    3. Sinon, vérifier is_displayed() standard
    """
    try:
        # 0) LimeSurvey: ignorer tout ce qui est dans un bloc masqué "ls-js-hidden"
        try:
            if el.find_elements(
                By.XPATH,
                "ancestor-or-self::*[contains(concat(' ',normalize-space(@class),' '),' ls-js-hidden ')][1]",
            ):
                return False
        except Exception:
            pass
        
        # 1) Cas spécial: input type=hidden mais wrapper cliquable visible
        #    (Decipher/FocusVision: clickableCell, sq-cardrating-button, etc.)
        tag = el.tag_name.lower()
        if tag == "input":
            input_type = (el.get_attribute("type") or "").lower()
            if input_type == "hidden":
                # Chercher un parent cliquable
                try:
                    cliquable_wrappers = el.find_elements(
                        By.XPATH,
                        "ancestor::*[contains(@class,'clickableCell') or "
                        "contains(@class,'sq-cardrating-button') or "
                        "contains(@class,'clickable')]"
                    )
                    if cliquable_wrappers:
                        # Vérifier que le wrapper est visible
                        for wrapper in cliquable_wrappers:
                            if wrapper.is_displayed():
                                return True
                except Exception:
                    pass
                # Pas de wrapper cliquable visible → pas actionnable
                return False
        
        # 2) Cas spécial: <select> natif masqué mais proxy Bootstrap-Select visible
        #    (ex: class "bs-select-hidden" + sibling .bootstrap-select)
        if tag == "select":
            try:
                if el.is_displayed():
                    return True
            except Exception:
                pass

            try:
                cls = (el.get_attribute("class") or "").lower()
            except Exception:
                cls = ""

            if "bs-select-hidden" in cls:
                try:
                    proxies = el.find_elements(
                        By.XPATH,
                        "following-sibling::*[contains(concat(' ', normalize-space(@class), ' '), ' bootstrap-select ')][1]",
                    )
                    if proxies:
                        try:
                            if proxies[0].is_displayed():
                                return True
                        except Exception:
                            pass
                except Exception:
                    pass

        # 3) Cas standard: vérifier is_displayed()
        return el.is_displayed()
    
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
        script = """
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
        
        return getAbsoluteXPath(arguments[0]);
        """
        xpath = driver.execute_script(script, el)
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
        tag = el.tag_name.lower()
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
        tag = el.tag_name.lower()
        if tag != "select":
            return ""
        
        # Chercher l'option sélectionnée ou la première option
        options = el.find_elements(By.TAG_NAME, "option")
        if not options:
            return ""
        
        # Première option (souvent placeholder)
        first_option_text = _norm(options[0].text)
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
