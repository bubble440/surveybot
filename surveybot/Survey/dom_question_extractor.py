# Survey/dom_question_extractor.py
"""
DOM Question Extractor - Extraction du texte de question à partir du DOM.

Ce module contient les fonctions spécialisées pour :
- Recherche de question proche (_find_question_text_near_element)
- Extraction de labels associés (_find_associated_label)
- Extracteurs platform-spécifiques (SSI Confirmit, SurveyWriter SSI)
- Extraction depuis conteneurs (_nearest_question_container, _extract_question_from_container)
- Groupement et cardinalité (_group_key_for_choice, _compute_max_select)
"""

from __future__ import annotations
from typing import List
import re
from selenium.webdriver.common.by import By

# Import des utilitaires
try:
    from Survey.dom_utils import _norm, _norm_lc, _is_question_text, _is_validation_instruction
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _norm, _norm_lc, _is_question_text, _is_validation_instruction

# ================================================================================
# CONSTANTE
# ================================================================================

# Pattern pour détecter les noms indexés (ex: Q1, Q2, etc.)
_INDEXED_NAME_PATTERN = re.compile(r"^[A-Za-z]+\d+", re.IGNORECASE)

# ================================================================================
# RECHERCHE DE QUESTION PROCHE
# ================================================================================

def _find_question_text_near_element(driver, el) -> str:
    """
    Cherche un texte "question" visuellement proche (au-dessus) de l'élément input/textarea.
    
    Objectif: éviter les fallbacks vision quand la question est bien dans le DOM
    mais pas dans le même conteneur HTML (Angular/React très fragmenté).
    
    Stratégie:
    - Parcourt tous les éléments visibles
    - Cherche ceux au-dessus (verticalement) avec overlap horizontal
    - Retourne le texte du plus proche (gap minimal)
    """
    try:
        txt = driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return "";
            const r = el.getBoundingClientRect();

            const badTags = new Set(["SCRIPT","STYLE","NOSCRIPT","TEXTAREA","INPUT","BUTTON","SELECT","OPTION"]);
            const isVisible = (e) => {
              const s = window.getComputedStyle(e);
              if (!s) return false;
              if (s.display === "none" || s.visibility === "hidden") return false;
              const rr = e.getBoundingClientRect();
              return rr.width > 0 && rr.height > 0;
            };

            const candidates = [];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
            while (walker.nextNode()) {
              const e = walker.currentNode;
              if (!e || badTags.has(e.tagName)) continue;
              if (!isVisible(e)) continue;

              const t = (e.innerText || "").trim();
              if (!t || t.length < 8) continue;

              const rr = e.getBoundingClientRect();

              // On veut un bloc au-dessus (ou très légèrement overlap) et proche verticalement
              const gap = r.top - rr.bottom;
              if (gap < -10 || gap > 320) continue;

              // Overlap horizontal minimum (évite de prendre le header de la page)
              const overlap = Math.min(r.right, rr.right) - Math.max(r.left, rr.left);
              const minOverlap = Math.min(r.width, rr.width) * 0.25;
              if (overlap < minOverlap) continue;

              // Score: plus proche verticalement + bloc plus "important" (surface)
              const area = rr.width * rr.height;
              candidates.push({ t, gap, area });
            }

            candidates.sort((a,b) => (a.gap - b.gap) || (b.area - a.area));
            return candidates.length ? candidates[0].t : "";
            """,
            el
        )
        return _norm(txt) if txt else ""
    except Exception:
        return ""


# ================================================================================
# LABEL ASSOCIÉ
# ================================================================================

def _find_associated_label(driver, el) -> str:
    """
    Cherche un <label> associé à cet input/select/textarea.
    
    Stratégies:
    1. Label avec attribut for=id
    2. Label parent contenant l'input
    3. Label sibling proche

    Note: un label d'option (ex: "homme", "femme") n'est pas une question.
    On ne doit donc pas filtrer via _is_question_text ici.
    """
    def _is_valid_option_label(txt: str) -> bool:
        txt = _norm(txt)
        if not txt:
            return False
        if _is_validation_instruction(txt):
            return False
        return True

    try:
        # 1) Label avec for=id
        el_id = el.get_attribute("id")
        if el_id:
            try:
                label = driver.find_element(By.CSS_SELECTOR, f'label[for="{el_id}"]')
                txt = _norm(label.text)
                if _is_valid_option_label(txt):
                    return txt
            except Exception:
                pass
        
        # 2) Label parent
        try:
            labels = el.find_elements(By.XPATH, "ancestor::label")
            for label in labels:
                txt = _norm(label.text)
                if _is_valid_option_label(txt):
                    return txt
        except Exception:
            pass
        
        # 3) Label sibling
        try:
            labels = el.find_elements(
                By.XPATH,
                "preceding-sibling::label[1] | following-sibling::label[1]"
            )
            for label in labels:
                txt = _norm(label.text)
                if _is_valid_option_label(txt):
                    return txt
        except Exception:
            pass
        
        return ""
    
    except Exception:
        return ""


# ================================================================================
# EXTRACTEURS PLATFORM-SPÉCIFIQUES
# ================================================================================

def _extract_ssi_confirmit_question(driver, el) -> str:
    """
    SSI Confirmit: cherche div.qtext ou similaire au-dessus de l'input.
    """
    try:
        # Chercher ancêtre avec class contenant 'question'
        containers = el.find_elements(
            By.XPATH,
            "ancestor::*[contains(@class,'question') or contains(@class,'qtext')]"
        )
        if not containers:
            return ""
        
        container = containers[0]
        
        # Chercher div.qtext ou .questiontext
        try:
            qtext_div = container.find_element(
                By.CSS_SELECTOR,
                ".qtext, .questiontext, .question-text"
            )
            txt = _norm(qtext_div.text)
            if txt and _is_question_text(txt):
                return txt
        except Exception:
            pass
        
        # Fallback: prendre tout le texte du container
        txt = _norm(container.text)
        if txt and _is_question_text(txt):
            return txt
        
        return ""
    
    except Exception:
        return ""


def _extract_surveywriter_ssi_question(driver, el) -> str:
    """
    SurveyWriter/SSI: cherche .label-text ou .survey-label proche.
    """
    try:
        # Chercher ancêtre avec class 'survey-question' ou similaire
        containers = el.find_elements(
            By.XPATH,
            "ancestor::*[contains(@class,'survey-question') or contains(@class,'question-container')]"
        )
        if not containers:
            return ""
        
        container = containers[0]
        
        # Chercher label-text
        try:
            label_div = container.find_element(
                By.CSS_SELECTOR,
                ".label-text, .survey-label, .question-label"
            )
            txt = _norm(label_div.text)
            if txt and _is_question_text(txt):
                return txt
        except Exception:
            pass
        
        return ""
    
    except Exception:
        return ""


# ================================================================================
# CONTENEUR DE QUESTION
# ================================================================================

def _nearest_question_container(el):
    """
    Remonte dans la hiérarchie pour trouver le conteneur de question le plus proche.
    Critères: div/section/fieldset avec class contenant 'question' ou similaire.
    """
    try:
        containers = el.find_elements(
            By.XPATH,
            "ancestor::*["
            "contains(@class,'question') or "
            "contains(@class,'survey-item') or "
            "contains(@class,'form-group') or "
            "contains(@class,'field-wrap') or "
            "self::fieldset or "
            "self::section"
            "]"
        )
        if containers:
            return containers[0]
        return None
    except Exception:
        return None


def _extract_question_from_container(container, options: List[str]) -> str:
    """
    Extrait le texte de question depuis un conteneur, en excluant les options.
    
    Stratégie:
    - Prendre tout le texte du conteneur
    - Retirer les options (qui sont souvent répétées dans le texte)
    - Garder ce qui reste comme question
    """
    try:
        if not container:
            return ""
        
        full_text = _norm(container.text)
        if not full_text:
            return ""
        
        # Retirer les options du texte
        question_text = full_text
        for opt in options:
            if not opt:
                continue
            # Retirer toutes les occurrences de l'option
            opt_pattern = re.escape(opt)
            question_text = re.sub(opt_pattern, "", question_text, flags=re.IGNORECASE)
        
        # Nettoyer
        question_text = _norm(question_text)
        
        # Vérifier que ce qui reste ressemble à une question
        if question_text and len(question_text) > 5 and _is_question_text(question_text):
            return question_text
        
        return ""
    
    except Exception:
        return ""


# ================================================================================
# GROUPEMENT ET CARDINALITÉ
# ================================================================================

def _group_key_for_choice(el, itype: str) -> str:
    """
    Retourne une clé de groupement pour regrouper les choices (radio/checkbox).
    Basé sur le name attribute pour radio/checkbox.
    """
    try:
        if itype in ("radio", "checkbox"):
            name = el.get_attribute("name") or ""
            if name:
                # Nettoyer le name (enlever les indices si présents)
                # Ex: "Q1[0]" -> "Q1", "question_1" -> "question_1"
                clean_name = re.sub(r"\[\d+\]$", "", name)
                clean_name = _norm_lc(clean_name)

                # Cas observé sur certains DOMs: les checkboxes d'une même question
                # ont des noms suffixés par option (ex: ...A1, ...A2, ...SQ001).
                # Sans normalisation, chaque option devient un groupe distinct.
                # On compacte donc la clé sur la racine commune du name.
                if itype == "checkbox":
                    base_name = re.sub(r"(?:sq\d+|a\d+)$", "", clean_name, flags=re.IGNORECASE)
                    if base_name and base_name != clean_name:
                        clean_name = base_name

                return _norm_lc(clean_name)

            # Fallback DOM-first: certains providers (ex: Quantilope) n'exposent
            # aucun `name` sur les radios mais regroupent visuellement les options
            # dans un conteneur commun (aria-labelledby / question-items / options).
            # On dérive alors une clé stable à partir de cet ancêtre.
            try:
                container = None
                container_xpaths = [
                    "ancestor::*[@role='radiogroup' or @role='group' or @aria-labelledby][1]",
                    "ancestor::*[contains(@class,'question-items') or contains(@class,'answers') or contains(@class,'options') or contains(@class,'choices')][1]",
                ]
                for xp in container_xpaths:
                    nodes = el.find_elements(By.XPATH, xp)
                    if nodes:
                        container = nodes[0]
                        break

                if container:
                    key_parts = [
                        container.get_attribute("aria-labelledby") or "",
                        container.get_attribute("name") or "",
                        container.get_attribute("id") or "",
                        container.get_attribute("data-testid") or "",
                        container.get_attribute("data-cy") or "",
                        container.get_attribute("class") or "",
                    ]
                    key = _norm_lc("|".join([p for p in key_parts if p]))
                    if key:
                        return f"dom:{key}"
            except Exception:
                pass
        
        return ""
    
    except Exception:
        return ""


def _compute_max_select(itype: str, options: List[str]) -> int:
    """
    Calcule max_select (cardinalité maximale de sélection).
    
    Règles:
    - radio: 1 (exclusif)
    - checkbox: len(options) (tout peut être sélectionné)
    - autres: 1 (par défaut)
    """
    if itype == "radio":
        return 1
    elif itype == "checkbox":
        return max(len(options), 1)
    else:
        return 1
