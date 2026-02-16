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
from typing import List, Dict, Any
import os
from selenium.webdriver.common.by import By

# Import des utilitaires
try:
    from Survey.dom_utils import _norm_lc, _xpath_literal
    from Survey.dom_registry import make_target_id, register_target
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _norm_lc, _xpath_literal
    from Survey.dom_registry import make_target_id, register_target
    # dom_registry devra être disponible


# ================================================================================
# FOCUSVISION / DECIPHER - ANSWERS LIST GROUPS
# ================================================================================

def _extract_focusvision_answers_list_groups(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait les groupes radio/checkbox FocusVision avec structure .answers.answers-list.
    
    Pattern DOM FocusVision:
    - Conteneurs: div.question[role='radiogroup'] / div.question.radio / div.question.checkbox
    - Liste d'options: .answers.answers-list
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
            answers = q.find_element(By.CSS_SELECTOR, ".answers.answers-list")
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

        # Regrouper par name
        by_name: dict[str, list] = {}
        for inp in inputs:
            name = (inp.get_attribute("name") or "").strip()
            if not name:
                continue
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

            for inp in inps:
                inp_id = (inp.get_attribute("id") or "").strip()
                if not inp_id:
                    continue

                # Label visible
                label_txt = ""
                try:
                    lab = answers.find_element(By.CSS_SELECTOR, f"label[for='{inp_id}']")
                    label_txt = (lab.text or "").strip()
                except Exception:
                    try:
                        lab = inp.find_element(By.XPATH, "ancestor::*[contains(@class,'clickableCell')][1]//label")
                        label_txt = (lab.text or "").strip()
                    except Exception as e:
                        if os.getenv("RUN_ENV", "local") == "local":
                            print(f"[DOM_ANALYZER][WARN] focusvision extract: {type(e).__name__}: {e}")
                        continue

                if not label_txt:
                    continue

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
                "max_select": 1,
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

# ================================================================================
# DECIPHER/FOCUSVISION - SINGLE TEXT INPUT
# ================================================================================

def _extract_decipher_single_text_input(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Extracteur spécifique Decipher/FocusVision pour input text/textarea unique.
    
    Cas d'usage: questions Red Herring Math, validations numériques, etc.
    Structure DOM: div.question > .answers.answers-list > input[type=text]
    
    Stratégie:
    - Chercher div.question avec .answers.answers-list
    - Vérifier qu'il contient UN SEUL input text/textarea visible
    - Extraire question depuis .question-text (prioritaire) ou container
    - Ignorer les erreurs et instructions de validation
    
    Args:
        driver: WebDriver Selenium
        frame_chain: Chaîne de frames
    
    Returns:
        Liste de dicts avec metadata (max 1 bloc)
    """
    blocks: List[Dict[str, Any]] = []

    try:
        # Chercher les conteneurs .question avec .answers.answers-list
        q_containers = driver.find_elements(By.CSS_SELECTOR, "div.question, div[class*='question']")
        
        for q in q_containers:
            try:
                # Vérifier qu'il y a .answers.answers-list
                answers = q.find_element(By.CSS_SELECTOR, ".answers.answers-list, .answers")
            except Exception:
                continue

            # Chercher inputs text/textarea visibles
            inputs = []
            try:
                candidates = answers.find_elements(
                    By.CSS_SELECTOR,
                    "input[type='text'], input:not([type='radio']):not([type='checkbox']):not([type='hidden']):not([type='submit']):not([type='button']), textarea"
                )
                for inp in candidates:
                    try:
                        # Vérifier visibilité basique
                        if inp.is_displayed() and inp.is_enabled():
                            inputs.append(inp)
                    except Exception:
                        pass
            except Exception:
                continue

            # On ne traite que le cas d'UN SEUL input
            if len(inputs) != 1:
                continue

            el = inputs[0]

            # PRIORITÉ: extraire question depuis .question-text
            question = ""
            try:
                qtext_elem = q.find_element(By.CSS_SELECTOR, ".question-text, .qtext, .questiontext, h1[class*='question']")
                question = (qtext_elem.text or "").strip()
            except Exception:
                pass

            # Fallback: texte du container en excluant erreurs et instructions
            if not question:
                try:
                    # Exclure .question-error et .instruction-text
                    full_text = q.text or ""
                    
                    # Retirer les erreurs
                    try:
                        error_elems = q.find_elements(By.CSS_SELECTOR, ".question-error, .error, [class*='error']")
                        for err in error_elems:
                            err_txt = (err.text or "").strip()
                            if err_txt:
                                full_text = full_text.replace(err_txt, "")
                    except Exception:
                        pass
                    
                    # Retirer les instructions
                    try:
                        instr_elems = q.find_elements(By.CSS_SELECTOR, ".instruction-text, .instructions, [class*='instruction']")
                        for instr in instr_elems:
                            instr_txt = (instr.text or "").strip()
                            if instr_txt:
                                full_text = full_text.replace(instr_txt, "")
                    except Exception:
                        pass
                    
                    question = full_text.strip()
                except Exception:
                    pass

            if not question or len(question) < 3:
                continue

            # Déterminer itype
            itype = "text"
            try:
                tag = (el.tag_name or "").strip().lower()
                if tag == "textarea":
                    itype = "textarea"
            except Exception:
                pass

            # Créer target_id
            el_id = (el.get_attribute("id") or "").strip()
            el_name = (el.get_attribute("name") or "").strip()
            single_key = f"{itype}:decipher:{el_id}:{el_name}"
            
            target_id = make_target_id("single", single_key, question)

            # XPath vers l'input
            try:
                from Survey.dom_utils import _best_xpath_for_element, _xpath_literal
                xpath = _best_xpath_for_element(driver, el)
                
                # Locators alternatifs
                alt_xpaths = []
                if el_name:
                    alt_xpaths.append(f"//input[@name={_xpath_literal(el_name)}]")
                if el_id:
                    alt_xpaths.append(f"//*[@id='{el_id}']")
                
                alt_xpaths = [x for x in dict.fromkeys(alt_xpaths) if x and x != xpath][:2]

            except Exception:
                continue

            # Enregistrer dans dom_registry
            register_target(target_id, {
                "kind": "single",
                "itype": itype,
                "question": question,
                "xpath": xpath,
                "alt_xpaths": alt_xpaths,
                "tag": el.tag_name,
                "name": el_name,
                "id": el_id,
                "frame_chain": list(frame_chain or [])
            })

            blocks.append({
                "target_id": target_id,
                "kind": "single",
                "itype": itype,
                "question": question,
                "options": [],
                "max_select": 1
            })

            # Un seul bloc maximum (premier trouvé)
            break

    except Exception as e:
        if os.getenv("RUN_ENV", "local") == "local":
            print(f"[DOM_ANALYZER][ERROR] decipher single text: {type(e).__name__}: {e}")

    return blocks