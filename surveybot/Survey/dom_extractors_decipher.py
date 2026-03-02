# Survey/dom_extractors_decipher.py
"""
DOM Extractors - Decipher/FocusVision

Ce module contient les extracteurs spécifiques aux plateformes:
- FocusVision (Decipher): radio/checkbox groups et cardsort
- Decipher answers list fallback

Ces extracteurs utilisent des patterns DOM spécifiques à ces plateformes
pour identifier et extraire les questions/options de manière fiable.
"""

from __future__ import annotations
from typing import List, Dict, Any, Set
import os
import re
from selenium.webdriver.common.by import By

# Import des utilitaires
try:
    from Survey.dom_utils import _norm_lc, _xpath_literal
    from Survey.dom_registry import register_target, make_target_id
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _norm_lc, _xpath_literal
    # dom_registry devra être disponible


# ================================================================================
# FOCUSVISION / DECIPHER - ANSWERS LIST GROUPS
# ================================================================================

def _logical_answers_list_group_name(raw_name: str, all_raw_names: Set[str]) -> str:
    """Retourne le nom de groupe logique pour les names Decipher answers-list.

    Règle DOM-first:
    - si plusieurs names siblings `ans<d>.<d>.<d>` partagent la même base
      (`ans<d>.<d>`), on regroupe sur cette base;
    - sinon on conserve le name réel tel quel.

    Cette garde évite de fabriquer un alias artificiel (ex: `ans10538.0`)
    quand le DOM n'expose qu'un seul name réel (`ans10538.0.0`).
    """
    name = (raw_name or "").strip()
    if not re.fullmatch(r"ans\d+\.\d+\.\d+", name):
        return name

    base = ".".join(name.split(".")[:2])
    sibling_count = 0
    for raw in all_raw_names:
        raw_norm = (raw or "").strip()
        if not re.fullmatch(r"ans\d+\.\d+\.\d+", raw_norm):
            continue
        if raw_norm.startswith(f"{base}."):
            sibling_count += 1
            if sibling_count >= 2:
                return base
    return name

def _extract_focusvision_answers_list_groups(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait les groupes radio/checkbox FocusVision avec structure .answers.answers-list.
    
    Pattern DOM FocusVision:
    - Conteneurs: div.question[role='radiogroup'] / div.question.radio / div.question.checkbox
    - Liste d'options: .answers.answers-list OU .answers.answers-table
    - Inputs masqués avec wrappers cliquables (.clickableCell ou .element)
    - Labels: label[for=id] ou dans .clickableCell
    
    Stratégie:
    - Chercher les conteneurs .question avec .answers.answers-list
    - Grouper les inputs par attribut name
    - Extraire question text depuis .question-text
    - Construire XPath vers wrapper cliquable (pas l'input masqué)
    
    Args:
        driver: WebDriver Selenium
        frame_chain: Chaîne de frames ou None
    
    Returns:
        Liste de dicts avec métadonnées pour dom_registry
    """
    blocks: list[dict] = []

    # Question containers FocusVision
    q_containers = driver.find_elements(By.CSS_SELECTOR, "div.question[role='radiogroup'], div.question.radio, div.question.checkbox")
    for q in q_containers:
        try:
            answers = q.find_element(By.CSS_SELECTOR, ".answers.answers-list, .answers.answers-table")
        except Exception:
            continue

        # Inputs masqués (hidden). Variante avec clickableCell
        # Inputs masqués (hidden), variante avec clickableCell
        # => on élargit un peu, mais toujours sous .answers.answers-list (scope strict).
        inputs = answers.find_elements(
            By.CSS_SELECTOR,
            "input[type='radio'], input[type='checkbox']"
        )
        if len(inputs) < 2:
            continue

        # Question texte
        question = ""
        try:
            question = (q.find_element(By.CSS_SELECTOR, ".question-text").text or "").strip()
        except Exception:
            question = (q.text or "").strip().split("\n")[0].strip()

        # Regrouper par name logique
        by_name: dict[str, list] = {}
        all_raw_names = {
            (inp.get_attribute("name") or "").strip()
            for inp in inputs
            if (inp.get_attribute("name") or "").strip()
        }
        for inp in inputs:
            name = (inp.get_attribute("name") or "").strip()
            if not name:
                continue
            name = _logical_answers_list_group_name(name, all_raw_names)
            by_name.setdefault(name, []).append(inp)

        for name, inps in by_name.items():
            # itype
            itype = "radio"
            try:
                if (inps[0].get_attribute("type") or "").strip().lower() == "checkbox":
                    itype = "checkbox"
            except Exception as e:
                if os.getenv("RUN_ENV", "local") == "local":
                    print(f"[DOM_ANALYZER][WARN] focusvision extract: {type(e).__name__}: {e}")
                continue

            options: list[str] = []
            option_xpath_map: dict[str, str] = {}
            aux_openended_input_names: set[str] = set()

            for inp in inps:
                inp_id = (inp.get_attribute("id") or "").strip()
                if not inp_id:
                    continue

                # Label visible
                label_txt = ""
                try:
                    lab = answers.find_element(By.CSS_SELECTOR, f"label[for='{inp_id}']")
                    label_txt = (lab.text or "").strip()
                    try:
                        for oe in lab.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea"):
                            oe_name = (oe.get_attribute("name") or "").strip()
                            if oe_name:
                                aux_openended_input_names.add(oe_name)
                    except Exception:
                        pass
                except Exception:
                    try:
                        lab = inp.find_element(By.XPATH, "ancestor::*[contains(@class,'clickableCell')][1]//label")
                        label_txt = (lab.text or "").strip()
                        try:
                            for oe in lab.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea"):
                                oe_name = (oe.get_attribute("name") or "").strip()
                                if oe_name:
                                    aux_openended_input_names.add(oe_name)
                        except Exception:
                            pass
                    except Exception as e:
                        if os.getenv("RUN_ENV", "local") == "local":
                            print(f"[DOM_ANALYZER][WARN] focusvision extract: {type(e).__name__}: {e}")
                        continue

                if not label_txt:
                    continue

                # Option "Autre ... préciser" avec champ open-ended dans le même label:
                # on exclut cette option du bloc group principal (non gérée en action group ici).
                if lab is not None:
                    try:
                        if lab.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea"):
                            continue
                    except Exception:
                        pass

                options.append(label_txt)

                # IMPORTANT: on clique un wrapper cliquable (pas l'input masqué).
                # Fallback: si clickableCell absent, on remonte sur .element.
                xp = (
                    f"//input[@id={_xpath_literal(inp_id)}]"
                    f"/ancestor::*["
                    f"contains(concat(' ',normalize-space(@class),' '),' clickableCell ')"
                    f" or contains(concat(' ',normalize-space(@class),' '),' element ')"
                    f"][1]"
                )
                option_xpath_map[_norm_lc(label_txt)] = xp

            if len(options) < 2:
                continue

            group_key = f"{itype}:name:{name}"
            target_id = make_target_id("group", group_key, question or name)

            register_target(target_id, {
                "kind": "group",
                "frame_chain": list(frame_chain or []),
                "itype": itype,
                "group_key": group_key,
                "question": question,
                "input_name": name,
                "max_select": 1 if itype == "radio" else len(options),
                "options": options,
                "option_xpath_map": option_xpath_map
            })

            blocks.append({
                "target_id": target_id,
                "kind": "group",
                "itype": itype,
                "question": question,
                "options": options,
                "max_select": 1 if itype == "radio" else len(options),
                "context": {
                    "kind": "group",
                    "group_key": group_key,
                    "focusvision_answers_list": True,
                    "aux_openended_names": sorted(aux_openended_input_names),
                },
            })

    return blocks


# ================================================================================
# FOCUSVISION - CARDSORT BLOCK
# ================================================================================

def _extract_focusvision_cardsort_block(driver, frame_chain: list[int] | None) -> dict | None:
    """
    Extrait un bloc cardsort FocusVision (drag & drop de cartes).
    
    Pattern DOM:
    - Container: div.question.cardsort
    - Cards à déplacer: .cardsort__card
    - Buckets cibles: .cardsort__bucket
    - Question: .question-text
    
    Stratégie:
    - Identifier le container .cardsort
    - Extraire liste des cartes disponibles
    - Extraire liste des buckets (destinations)
    - Retourner metadata pour traitement ultérieur
    
    Args:
        driver: WebDriver Selenium
        frame_chain: Chaîne de frames ou None
    
    Returns:
        Dict avec metadata ou None si pas trouvé
    """
    try:
        # Chercher conteneur cardsort
        container = driver.find_element(By.CSS_SELECTOR, "div.question.cardsort")
    except Exception:
        return None

    # Question text
    question = ""
    try:
        question = (container.find_element(By.CSS_SELECTOR, ".question-text").text or "").strip()
    except Exception:
        question = (container.text or "").strip().split("\n")[0].strip()

    # Cartes
    cards = []
    try:
        card_elements = container.find_elements(By.CSS_SELECTOR, ".cardsort__card")
        for card in card_elements:
            card_text = (card.text or "").strip()
            if card_text:
                cards.append(card_text)
    except Exception:
        pass

    # Buckets (catégories de destination)
    buckets = []
    try:
        bucket_elements = container.find_elements(By.CSS_SELECTOR, ".cardsort__bucket")
        for bucket in bucket_elements:
            # Chercher le label du bucket
            try:
                bucket_label = bucket.find_element(By.CSS_SELECTOR, ".cardsort__bucket-label")
                bucket_text = (bucket_label.text or "").strip()
            except Exception:
                bucket_text = (bucket.text or "").strip()
            
            if bucket_text:
                buckets.append(bucket_text)
    except Exception:
        pass

    if not cards or not buckets:
        return None

    # Créer un target_id pour ce cardsort
    target_id = make_target_id("cardsort", "focusvision_cardsort", question)

    # Enregistrer dans dom_registry
    register_target(target_id, {
        "kind": "cardsort",
        "frame_chain": list(frame_chain or []),
        "question": question,
        "cards": cards,
        "buckets": buckets,
        "platform": "focusvision"
    })

    return {
        "target_id": target_id,
        "kind": "cardsort",
        "question": question,
        "cards": cards,
        "buckets": buckets
    }


# ================================================================================
# DECIPHER - ANSWERS LIST FALLBACK
# ================================================================================

def _extract_decipher_answers_list_fallback(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Extracteur fallback pour Decipher answers-list (radio/checkbox groups).
    
    Utilisé quand les extracteurs standards échouent. Pattern alternatif:
    - Container: .answer-list
    - Inputs: input[type=radio] / input[type=checkbox]
    - Labels: via for= ou structure parent
    
    Stratégie:
    - Chercher tous les .answer-list containers
    - Pour chaque container, extraire inputs et labels
    - Grouper par name attribute
    - Construire option_xpath_map
    
    Args:
        driver: WebDriver Selenium
        frame_chain: Chaîne de frames
    
    Returns:
        Liste de dicts avec metadata pour dom_registry
    """
    blocks: List[Dict[str, Any]] = []

    try:
        # Chercher tous les containers .answer-list
        containers = driver.find_elements(By.CSS_SELECTOR, ".answer-list")
        
        for container in containers:
            try:
                # Trouver tous les inputs dans ce container
                inputs = container.find_elements(
                    By.CSS_SELECTOR,
                    "input[type='radio'], input[type='checkbox']"
                )
                
                if len(inputs) < 2:
                    continue

                # Déterminer le type (radio ou checkbox)
                first_type = (inputs[0].get_attribute("type") or "").strip().lower()
                itype = "checkbox" if first_type == "checkbox" else "radio"

                # Regrouper par name
                by_name: Dict[str, List] = {}
                for inp in inputs:
                    name = (inp.get_attribute("name") or "").strip()
                    if not name:
                        continue
                    by_name.setdefault(name, []).append(inp)

                # Extraire question text (chercher dans parent ou siblings)
                question = ""
                try:
                    # Essayer de trouver .question-text dans le parent
                    parent = container.find_element(By.XPATH, "..")
                    question_elem = parent.find_element(By.CSS_SELECTOR, ".question-text, .qtext")
                    question = (question_elem.text or "").strip()
                except Exception:
                    # Fallback: prendre le texte du container
                    question = (container.text or "").strip().split("\n")[0].strip()

                # Pour chaque groupe de name
                for name, group_inputs in by_name.items():
                    options: List[str] = []
                    option_xpath_map: Dict[str, str] = {}

                    for inp in group_inputs:
                        inp_id = (inp.get_attribute("id") or "").strip()
                        if not inp_id:
                            continue

                        # Chercher le label associé
                        label_txt = ""
                        try:
                            # Méthode 1: label[for=id]
                            label = driver.find_element(By.CSS_SELECTOR, f"label[for='{inp_id}']")
                            label_txt = (label.text or "").strip()
                        except Exception:
                            try:
                                # Méthode 2: label parent
                                label = inp.find_element(By.XPATH, "ancestor::label[1]")
                                label_txt = (label.text or "").strip()
                            except Exception:
                                # Méthode 3: sibling label
                                try:
                                    label = inp.find_element(By.XPATH, "following-sibling::label[1]")
                                    label_txt = (label.text or "").strip()
                                except Exception:
                                    pass

                        if not label_txt:
                            continue

                        options.append(label_txt)

                        # XPath vers le label (cliquable)
                        xp = f"//label[@for={_xpath_literal(inp_id)}]"
                        option_xpath_map[_norm_lc(label_txt)] = xp

                    if len(options) < 2:
                        continue

                    # Créer group_key et target_id
                    group_key = f"{itype}:name:{name}"
                    target_id = make_target_id("group", group_key, question or name)

                    # Enregistrer dans dom_registry
                    register_target(target_id, {
                        "kind": "group",
                        "frame_chain": list(frame_chain or []),
                        "itype": itype,
                        "group_key": group_key,
                        "question": question,
                        "input_name": name,
                        "max_select": 1 if itype == "radio" else len(options),
                        "options": options,
                        "option_xpath_map": option_xpath_map
                    })

                    blocks.append({
                        "target_id": target_id,
                        "kind": "group",
                        "itype": itype,
                        "question": question,
                        "options": options
                    })

            except Exception as e:
                if os.getenv("RUN_ENV", "local") == "local":
                    print(f"[DOM_ANALYZER][WARN] decipher fallback: {type(e).__name__}: {e}")
                continue

    except Exception as e:
        if os.getenv("RUN_ENV", "local") == "local":
            print(f"[DOM_ANALYZER][ERROR] decipher fallback outer: {type(e).__name__}: {e}")

    return blocks
