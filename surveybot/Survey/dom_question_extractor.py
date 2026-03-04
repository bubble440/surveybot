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
from typing import List, Optional
import re
from selenium.webdriver.common.by import By

# Import des utilitaires
try:
    from Survey.dom_utils import _norm, _norm_lc, _is_question_text, _is_validation_instruction
except ImportError:
    # Fallback pour tests locaux
    from Survey.dom_utils import _norm, _norm_lc, _is_question_text, _is_validation_instruction

try:
    from Survey.log_utils import is_debug, log_debug
except ImportError:
    def is_debug() -> bool:
        return False

    def log_debug(tag: str, msg: str) -> None:
        return None

try:
    from Survey.dom_selection_rules import (
        explicit_exact_count_from_question,
        has_explicit_multi_indicator,
    )
except ImportError:
    from surveybot.Survey.dom_selection_rules import (
        explicit_exact_count_from_question,
        has_explicit_multi_indicator,
    )

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
        # 0) Widgets ARIA custom: label via aria-labelledby (Forsta/Confirmit, etc.)
        aria_labelledby = (el.get_attribute("aria-labelledby") or "").strip()
        if aria_labelledby:
            try:
                parts = [p for p in aria_labelledby.split() if p]
                texts = []
                for ref_id in parts:
                    node = driver.find_element(By.ID, ref_id)
                    txt = _norm(node.text or node.get_attribute("innerText") or "")
                    if txt and txt not in texts:
                        texts.append(txt)
                joined = _norm(" ".join(texts))
                if _is_valid_option_label(joined):
                    return joined
            except Exception:
                pass

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

        # 4) Variants custom: libellé dans un sibling non-<label>
        # ex: <div class="answer_options"><div class="option_label"><span>...</span></div><input ...></div>
        try:
            custom_label_nodes = el.find_elements(
                By.XPATH,
                "ancestor::*[contains(@class,'answer_options')][1]//*[contains(@class,'option_label')]"
            )
            for node in custom_label_nodes:
                txt = _norm(node.text or node.get_attribute("innerText") or "")
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
        
        raw_text = container.text or ""
        if not _norm(raw_text):
            return ""

        # Retirer les lignes qui correspondent exactement à des options
        # sans supprimer les mots-clés d'une consigne (ex: "sélectionnez violet").
        option_keys = {_norm_lc(opt) for opt in options if _norm(opt)}
        kept_lines = []
        for line in raw_text.splitlines():
            line_norm = _norm(line)
            if not line_norm:
                continue
            if _norm_lc(line_norm) in option_keys:
                continue
            kept_lines.append(line_norm)

        # Nettoyer
        question_text = _norm(" ".join(kept_lines))
        
        # Vérifier que ce qui reste ressemble à une question
        if question_text and len(question_text) > 5 and _is_question_text(question_text):
            return question_text
        
        return ""
    
    except Exception:
        return ""


def _find_group_heading_text_near_element(driver, el, options: List[str]) -> str:
    """
    Récupère un texte d'intitulé quand les radios/checkbox sont visibles mais que
    la question n'est pas dans le même conteneur direct que les inputs.

    Critères DOM observables:
    - texte candidat porté par un heading/label/legend/question-like node
    - candidat situé dans l'ancêtre visuel du groupe de choix
    - exclusion stricte des labels d'options du groupe
    """
    try:
        option_keys = [_norm_lc(opt) for opt in (options or []) if _norm(opt)]
        txt = driver.execute_script(
            """
            const el = arguments[0];
            const optionKeys = Array.isArray(arguments[1]) ? arguments[1] : [];
            if (!el) return "";

            const norm = (v) => (v || "").replace(/\s+/g, " ").trim();
            const normLc = (v) => norm(v).toLowerCase();

            const isVisible = (node) => {
              if (!node || !(node instanceof Element)) return false;
              const st = window.getComputedStyle(node);
              if (!st) return false;
              if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
              const r = node.getBoundingClientRect();
              return r.width > 0 && r.height > 0;
            };

            const groupType = ((el.getAttribute('type') || '').toLowerCase() || (el.getAttribute('role') || '').toLowerCase());
            const groupName = el.getAttribute('name') || '';
            const root = el.closest('#app, form, main, [role="main"], body') || document.body;

            const sharedGroupSelectors = 'fieldset, .choice-list-full, .question, .question-container, #profiler-choice';

            const sameChoices = Array.from(root.querySelectorAll('input[type="radio"],input[type="checkbox"],[role="radio"],[role="checkbox"]'))
              .filter((n) => {
                const t = ((n.getAttribute('type') || '').toLowerCase() || (n.getAttribute('role') || '').toLowerCase());
                if (t !== groupType) return false;
                const nName = n.getAttribute('name') || '';
                if (groupName && nName) return nName === groupName;
                return n.closest(sharedGroupSelectors) === el.closest(sharedGroupSelectors);
              });

            const groupRoot = (sameChoices[0] && sameChoices[0].closest(sharedGroupSelectors))
              || el.closest(sharedGroupSelectors)
              || el.parentElement;
            if (!groupRoot) return '';

            const qSelectors = [
              '.question-label',
              '.question-description',
              'legend',
              'h1,h2,h3,h4,h5',
              '[role="heading"]',
              'label',
              'p'
            ];

            const scanRoots = [
              groupRoot,
              groupRoot.parentElement,
              groupRoot.parentElement ? groupRoot.parentElement.parentElement : null,
            ].filter(Boolean);
            const candidates = [];
            for (const scanRoot of scanRoots) {
              for (const sel of qSelectors) {
                for (const node of Array.from(scanRoot.querySelectorAll(sel))) {
                  if (!isVisible(node)) continue;
                  if (groupRoot.contains(node) && node.matches('label[for], .single-choice-container label')) continue;
                  const t = norm(node.textContent || node.innerText || '');
                  const tLc = normLc(t);
                  if (!t || t.length < 8) continue;
                  if (optionKeys.includes(tLc)) continue;
                  if (Array.from((groupRoot || document).querySelectorAll('label[for]')).some((ln) => normLc(ln.textContent || '') === tLc)) continue;
                  candidates.push({
                    t,
                    priority: node.matches('.question-label,.question-description,legend,h1,h2,h3,h4,h5,[role="heading"]') ? 1 : 0,
                    len: t.length,
                  });
                }
              }
              if (candidates.length) break;
            }

            candidates.sort((a,b) => (b.priority - a.priority) || (a.len - b.len));
            return candidates.length ? candidates[0].t : '';
            """,
            el,
            option_keys,
        )
        return _norm(txt) if txt else ""
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
                    # SPSSMR/HTMLPlayer pattern (ex: Escalent): les options d'une
                    # même question checkbox peuvent avoir des names tous distincts
                    # (`_QQ1_Cr1`, `_QQ1_Cr2`, ...). Dans ce cas, grouper par `name`
                    # casse la question en N blocs mono-option.
                    # Scope DOM strict: uniquement si on trouve un conteneur
                    # question stable (fieldset/.mrQuestionTable), >=2 checkboxes
                    # avec names non vides tous distincts et des marqueurs DOM
                    # SPSSMR/HTMLPlayer observables (mrForm / classes mr*).
                    try:
                        fieldsets = el.find_elements(By.XPATH, "ancestor::fieldset[1]")
                    except Exception:
                        fieldsets = []

                    try:
                        mr_tables = el.find_elements(
                            By.XPATH,
                            "ancestor::*[contains(@class,'mrQuestionTable')][1]",
                        )
                    except Exception:
                        mr_tables = []

                    group_container = fieldsets[0] if fieldsets else (mr_tables[0] if mr_tables else None)

                    if group_container is not None:
                        try:
                            in_mr_form = bool(
                                el.find_elements(By.XPATH, "ancestor::form[@id='mrForm' or @name='mrForm'][1]")
                            )
                        except Exception:
                            in_mr_form = False

                        c_class = _norm_lc(group_container.get_attribute("class") or "")
                        has_mr_class = ("mrquestiontable" in c_class) or ("mrmultiple" in c_class)

                        if not has_mr_class:
                            try:
                                has_mr_class = bool(
                                    group_container.find_elements(
                                        By.XPATH,
                                        ".//*[contains(@class,'mrQuestionTable') or contains(@class,'mrMultiple')]",
                                    )
                                )
                            except Exception:
                                has_mr_class = False

                        is_spssmr_like = in_mr_form or has_mr_class

                        if is_spssmr_like:
                            try:
                                scoped_boxes = group_container.find_elements(By.XPATH, ".//input[@type='checkbox'][@name]")
                            except Exception:
                                scoped_boxes = []

                            scoped_names = []
                            for b in scoped_boxes:
                                try:
                                    nm = _norm_lc(b.get_attribute("name") or "")
                                except Exception:
                                    nm = ""
                                if nm:
                                    scoped_names.append(nm)

                            names_distinct = (
                                len(scoped_names) == len(scoped_boxes)
                                and len(set(scoped_names)) == len(scoped_names)
                            )

                            if len(scoped_boxes) >= 2 and names_distinct:
                                c_tag = _norm_lc(group_container.tag_name or "")
                                c_id = _norm_lc(group_container.get_attribute("id") or "")
                                c_legend = ""
                                try:
                                    legends = group_container.find_elements(By.XPATH, ".//legend[1]")
                                    if legends:
                                        c_legend = _norm_lc((legends[0].text or "").strip())
                                except Exception:
                                    c_legend = ""

                                container_bits = [x for x in [c_tag, c_id, c_class, c_legend[:120]] if x]
                                if container_bits:
                                    dom_container_key = f"dom_container:{'|'.join(container_bits)}"
                                    if is_debug():
                                        log_debug(
                                            "[DOM_GROUPING]",
                                            f"spssmr_container_group key={dom_container_key} boxes={len(scoped_boxes)} names_distinct={names_distinct}",
                                        )
                                    return dom_container_key

                    base_name = re.sub(r"(?:sq\d+|a\d+)$", "", clean_name, flags=re.IGNORECASE)
                    if base_name and base_name != clean_name:
                        clean_name = base_name

                    # Decipher/FocusVision pattern: checkboxes d'une même question
                    # nommés `ans10518.0.0`, `ans10518.0.1`, etc. Le suffixe final
                    # identifie l'option et ne doit pas créer un groupement distinct.
                    # Scope minimal: uniquement si le name se termine par `.<digits>`.
                    dotted_base_name = re.sub(r"\.\d+$", "", clean_name)
                    if dotted_base_name and dotted_base_name != clean_name:
                        clean_name = dotted_base_name

                    # YouGov-like pattern: une question checkbox rend chaque option
                    # avec un name distinct suffixé par index (`w38-response-1`, `...-2`, ...)
                    # à l'intérieur d'un `fieldset.question-multiple` unique.
                    # Scope DOM strict: on normalise uniquement si ce fieldset contient
                    # >=2 checkboxes partageant la même racine `prefix-<digits>`.
                    suffix_num_base = re.sub(r"-\d+$", "", clean_name)
                    if suffix_num_base and suffix_num_base != clean_name:
                        try:
                            fieldsets = el.find_elements(
                                By.XPATH,
                                "ancestor::fieldset[contains(@class,'question-multiple')][1]",
                            )
                        except Exception:
                            fieldsets = []

                        if fieldsets:
                            try:
                                sibling_boxes = fieldsets[0].find_elements(By.XPATH, ".//input[@type='checkbox'][@name]")
                            except Exception:
                                sibling_boxes = []

                            prefixed = 0
                            for sib in sibling_boxes:
                                try:
                                    sib_name = _norm_lc(sib.get_attribute("name") or "")
                                except Exception:
                                    sib_name = ""
                                if sib_name and re.match(rf"^{re.escape(suffix_num_base)}-\d+$", sib_name):
                                    prefixed += 1

                            if prefixed >= 2:
                                clean_name = suffix_num_base

                    # Tivian/CustomerVoice pattern: chaque option checkbox d'une même
                    # question porte un name distinct "v_115", "v_116", etc.
                    # On regroupe alors par identifiant de conteneur question-* quand
                    # ce pattern DOM précis est observé (scope minimal).
                    if re.match(r"^v_\d+$", clean_name):
                        try:
                            containers = el.find_elements(
                                By.XPATH,
                                "ancestor::*[contains(@class,'type-multi') and contains(@class,'question-')][1]",
                            )
                        except Exception:
                            containers = []

                        if containers:
                            try:
                                cls = _norm_lc(containers[0].get_attribute("class") or "")
                            except Exception:
                                cls = ""
                            m = re.search(r"\bquestion-(\d+)\b", cls)
                            if m:
                                return f"question_{m.group(1)}"

                return _norm_lc(clean_name)

            # Fallback DOM-first: certains providers (ex: Quantilope) n'exposent
            # aucun `name` sur les radios mais regroupent visuellement les options
            # dans un conteneur commun (aria-labelledby / question-items / options).
            # On dérive alors une clé stable à partir de cet ancêtre.
            try:
                container = None
                container_xpaths = [
                    "ancestor::*[@role='radiogroup' or @role='group' or @aria-labelledby][1]",
                    "ancestor::*[@role='listbox' or contains(@class,'multi-select-container') or contains(@class,'single-choice-container')][1]",
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


def _compute_max_select(itype: str, options: List[str], question_text: str | None = None) -> int:
    """
    Calcule max_select (cardinalité maximale de sélection).
    
    Règles:
    - radio: 1 (exclusif)
    - checkbox: len(options) (tout peut être sélectionné)
    - autres: 1 (par défaut)
    """
    if itype in {"checkbox", "radio", "button"}:
        exact_count = explicit_exact_count_from_question(question_text)
        if exact_count is not None:
            if options:
                return max(1, min(exact_count, len(options)))
            return max(1, exact_count)
        if has_explicit_multi_indicator(question_text):
            if options:
                max_select = min(3, len(options))
            else:
                max_select = 3
            if is_debug() and max_select == 3:
                log_debug(
                    "[max_select][debug]",
                    f"rule=multi_explicit_force_3 itype={itype} "
                    f"max_select=3 question=\"{_norm(question_text or '')}\"",
                )
            return max_select
        if itype in {"radio", "button"}:
            return 1
        return max(len(options), 1)
    return 1
