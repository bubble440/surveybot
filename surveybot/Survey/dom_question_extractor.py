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
import html

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
        compute_checkbox_max_select,
        compute_min_select,
    )
except ImportError:
    from surveybot.Survey.dom_selection_rules import (
        compute_checkbox_max_select,
        compute_min_select,
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
        txt = driver.evaluate(
            """(el) => {
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
              if (e.id === "error-summary") continue;
              if (e.className && typeof e.className === "string" && e.className.indexOf("error") !== -1) continue;

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
            }""",
            el,
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
        page = driver

        # 0) Widgets ARIA custom: label via aria-labelledby (Forsta/Confirmit, etc.)
        aria_labelledby = (el.get_attribute("aria-labelledby") or "").strip()
        if aria_labelledby:
            parts = [p for p in aria_labelledby.split() if p]
            texts = []
            for ref_id in parts:
                node = page.query_selector(f"#{ref_id}")
                if node is None:
                    continue
                txt = _norm(node.inner_text() or node.text_content() or "")
                if txt and txt not in texts:
                    texts.append(txt)
            if texts:
                joined = _norm(" ".join(texts))
                if _is_valid_option_label(joined):
                    return joined

        # 1) Label avec for=id
        el_id = el.get_attribute("id")
        if el_id:
            label = page.query_selector(f'label[for="{el_id}"]')
            if label is not None:
                txt = _norm(label.inner_text() or label.text_content() or "")
                if _is_valid_option_label(txt):
                    return txt

        # 2) Label parent
        try:
            labels = el.query_selector_all("xpath=ancestor::label")
            for label in labels:
                txt = _norm(label.inner_text() or label.text_content() or "")
                if _is_valid_option_label(txt):
                    return txt
        except Exception:
            pass

        # 3) Label sibling
        try:
            labels = el.query_selector_all(
                "xpath=preceding-sibling::label[1] | following-sibling::label[1]"
            )
            for label in labels:
                txt = _norm(label.inner_text())
                if _is_valid_option_label(txt):
                    return txt
        except Exception:
            pass

        # 4) Variants custom: libellé dans un sibling non-<label>
        # ex: <div class="answer_options"><div class="option_label"><span>...</span></div><input ...></div>
        try:
            custom_label_nodes = el.query_selector_all(
                "xpath=ancestor::*[contains(@class,'answer_options')][1]//*[contains(@class,'option_label')]"
            )
            for node in custom_label_nodes:
                txt = _norm(node.inner_text() or node.text_content() or "")
                if _is_valid_option_label(txt):
                    return txt
        except Exception:
            pass

        # 4b) Span directement adjacent à l'input dans le même conteneur
        #     (ex: Askia — <td><input class="askia-live"/><span>Label</span></td>).
        #     Déclenché uniquement quand les stratégies label/aria/parent ont échoué.
        try:
            sibling_spans = el.query_selector_all("xpath=following-sibling::span[1]")
            for span in sibling_spans:
                txt = _norm(span.inner_text() or "")
                if txt and len(txt) <= 300 and _is_valid_option_label(txt):
                    return txt
        except Exception:
            pass

        # 5) Fallback DOM ciblé: options custom sans <label for="..."> explicite.
        # Scope strict DOM: on cherche le plus proche wrapper d'option
        # (et non le conteneur global de question) pour éviter de capturer
        # le texte agrégé de toute la question.
        try:
            dom_option_txt = page.evaluate(
                r"""(input) => {
                if (!input) return '';

                const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
                const isVisible = (node) => {
                  if (!node || !(node instanceof Element)) return false;
                  const st = window.getComputedStyle(node);
                  if (!st) return false;
                  if (st.display === 'none' || st.visibility === 'hidden') return false;
                  const r = node.getBoundingClientRect();
                  return r.width > 0 && r.height > 0;
                };

                const optionHost = input.closest(
                  'label, .radio-checkbox-wrapper, .checkbox-wrapper, .radio-wrapper, .category-option, .answer-option, .answer_options, .option, li, [role="option"], [class*="option-item"], [class*="choice-item"]'
                );
                if (!optionHost || !isVisible(optionHost)) return '';

                // Guard: si l'optionHost contient plusieurs inputs avec le même name,
                // c'est un conteneur de section, pas un wrapper d'option — ignorer.
                const iName = (input.getAttribute('name') || '').trim();
                if (iName) {
                  const esc = iName.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
                  if (optionHost.querySelectorAll('input[name="' + esc + '"]').length > 1) return '';
                }

                const candidates = [];
                const nodes = optionHost.querySelectorAll('span, div, p, strong, em, label');
                for (const node of Array.from(nodes)) {
                  if (!isVisible(node)) continue;
                  if (node === input || node.contains(input)) continue;
                  if (node.querySelector('input,select,textarea')) continue;
                  const t = norm(node.innerText || node.textContent || '');
                  if (!t) continue;
                  candidates.push({ t, len: t.length });
                }

                if (!candidates.length) {
                  const fallback = norm(optionHost.innerText || optionHost.textContent || '');
                  return fallback;
                }

                candidates.sort((a, b) => a.len - b.len);
                return candidates[0].t;
                }""",
                el,
            )
            dom_option_txt = _norm(dom_option_txt)
            if dom_option_txt and len(dom_option_txt) <= 300 and _is_valid_option_label(dom_option_txt):
                return dom_option_txt
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
        containers = el.query_selector_all(
            "xpath=ancestor::*[contains(@class,'question') or contains(@class,'qtext')]"
        )
        if not containers:
            return ""

        container = containers[0]

        # Chercher div.qtext ou .questiontext
        qtext_div = container.query_selector(".qtext, .questiontext, .question-text")
        if qtext_div is not None:
            txt = _norm(qtext_div.inner_text())
            if txt and _is_question_text(txt):
                return txt

        # Fallback: prendre tout le texte du container
        txt = _norm(container.inner_text())
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
        containers = el.query_selector_all(
            "xpath=ancestor::*[contains(@class,'survey-question') or contains(@class,'question-container')]"
        )
        if not containers:
            return ""

        container = containers[0]

        # Chercher label-text
        label_div = container.query_selector(".label-text, .survey-label, .question-label")
        if label_div is not None:
            txt = _norm(label_div.inner_text())
            if txt and _is_question_text(txt):
                return txt

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
        containers = el.query_selector_all(
            "xpath=ancestor::*["
            "contains(@class,'question') or "
            "contains(@class,'survey-item') or "
            "contains(@class,'form-group') or "
            "contains(@class,'field-wrap') or "
            "self::fieldset or "
            "self::section"
            "]"
        )
        if containers:
            return containers[-1]
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

        raw_text = container.inner_text() or ""
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


def _extract_mriweb_grid_question_text(el) -> str:
    """
    Material/mrIWeb: récupère le texte de question principal d'une grille
    `table.mrGridTable` (inputs texte par ligne), sans inclure les messages d'erreur.

    Critères DOM stricts:
    - input dans `table.mrGridTable`
    - priorité au `summary` de la table (texte question canonical)
    - fallback sur `span.mrQuestionText` visible dans le `content-wrapper`
      en excluant explicitement le bloc `.error-block`
    """
    try:
        grids = el.query_selector_all("xpath=ancestor::table[contains(@class,'mrGridTable')][1]")
    except Exception:
        grids = []

    if not grids:
        return ""

    grid = grids[0]

    try:
        summary_raw = grid.get_attribute("summary") or ""
        summary_txt = _norm(re.sub(r"<[^>]+>", " ", html.unescape(summary_raw)))
        if summary_txt and _is_question_text(summary_txt) and not _is_validation_instruction(summary_txt):
            return summary_txt
    except Exception:
        pass

    try:
        candidates = grid.query_selector_all(
            "xpath=ancestor::div[contains(@class,'content-wrapper')][1]"
            "//span[contains(@class,'mrQuestionText') and normalize-space(.)!='' and "
            "not(ancestor::td[contains(@class,'error-block')])]",
        )
    except Exception:
        candidates = []

    for node in candidates:
        try:
            txt = _norm(node.inner_text() or "")
        except Exception:
            txt = ""
        if not txt:
            continue
        if re.fullmatch(r"\d+", txt):
            continue
        if _is_validation_instruction(txt):
            continue
        if _is_question_text(txt):
            return txt

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
        # Priorité DOM stricte: si un fieldset parent expose un legend non-option,
        # c'est l'intitulé de question le plus fiable (ex: YouGov question-multiple).
        try:
            # Utilise //legend[1] (descendant) et non /legend[1] (enfant direct)
            # pour couvrir le pattern fieldset > article > legend (ex: prescreener
            # surveys.insights-today.com). L'accès via textContent contourne
            # les légendes CSS-invisibles (width/height=0 mais texte présent dans le DOM).
            legends = el.query_selector_all("xpath=ancestor::fieldset[1]//legend[1]")
        except Exception:
            legends = []

        if legends:
            legend_text = _norm(
                legends[0].inner_text()
                or legends[0].text_content()
                or ""
            )
            legend_lc = _norm_lc(legend_text)
            option_lc = {_norm_lc(opt) for opt in (options or []) if _norm(opt)}
            if legend_text and legend_lc not in option_lc and _is_question_text(legend_text):
                return legend_text

        option_keys = [_norm_lc(opt) for opt in (options or []) if _norm(opt)]
        txt = driver.evaluate(
            r"""([el, optionKeys]) => {
            optionKeys = Array.isArray(optionKeys) ? optionKeys : [];
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
            }""",
            [el, option_keys],
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
            # Savanta JQM pattern: fieldset.question-wrapper contenant >=2 inputs
            # (checkbox ou radio) partageant le même attribut dat= (discriminant
            # côté serveur). On retourne une clé stable basée sur dat= pour que
            # tous ces inputs soient fusionnés en un seul question_block.
            # Guard DOM strict: dat= non vide + fieldset.question-wrapper ancêtre.
            try:
                dat_val = _norm_lc(el.get_attribute("dat") or "")
                if dat_val:
                    fs_nodes = el.query_selector_all(
                        "xpath=ancestor::fieldset[contains(@class,'question-wrapper')][1]",
                    )
                    if fs_nodes:
                        all_inp = fs_nodes[0].query_selector_all(
                            "xpath=.//input[@type='checkbox' or @type='radio']"
                        )
                        matching = sum(
                            1 for inp in all_inp
                            if _norm_lc(inp.get_attribute("dat") or "") == dat_val
                        )
                        if matching >= 2:
                            return f"fieldset:{dat_val}"
            except Exception:
                pass
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
                    # CloudResearch/Sentry-like pattern: les options d'une même
                    # question partagent `data-checkbox-group`, tandis que `name`
                    # est suffixé par option (`...selection.opt_xxx`).
                    # Scope DOM strict: uniquement si l'attribut groupe existe ET
                    # le `name` suit explicitement le pattern `.opt_`.
                    data_checkbox_group = _norm_lc(el.get_attribute("data-checkbox-group") or "")
                    if data_checkbox_group and re.search(r"\.opt_[a-z0-9_-]+$", clean_name, flags=re.IGNORECASE):
                        clean_name = data_checkbox_group

                    # SPSSMR/HTMLPlayer pattern (ex: Escalent): les options d'une
                    # même question checkbox peuvent avoir des names tous distincts
                    # (`_QQ1_Cr1`, `_QQ1_Cr2`, ...). Dans ce cas, grouper par `name`
                    # casse la question en N blocs mono-option.
                    # Scope DOM strict: uniquement si on trouve un conteneur
                    # question stable (fieldset/.mrQuestionTable), >=2 checkboxes
                    # avec names non vides tous distincts et des marqueurs DOM
                    # SPSSMR/HTMLPlayer observables (mrForm / classes mr*).
                    try:
                        fieldsets = el.query_selector_all("xpath=ancestor::fieldset[1]")
                    except Exception:
                        fieldsets = []

                    try:
                        mr_tables = el.query_selector_all(
                            "xpath=ancestor::*[contains(@class,'mrQuestionTable')][1]",
                        )
                    except Exception:
                        mr_tables = []

                    group_container = fieldsets[0] if fieldsets else (mr_tables[0] if mr_tables else None)

                    # GfK/mrIWeb (SPSSMR/HTMLPlayer): les checkboxes d'une question
                    # partagent un ancêtre div.muAll sans fieldset ni mrQuestionTable.
                    # Guard DOM strict : contains(@class,'muAll') + in_mr_form vérifié ci-dessous.
                    if group_container is None:
                        try:
                            muall_nodes = el.query_selector_all(
                                "xpath=ancestor::*[contains(@class,'muAll')][1]",
                            )
                            if muall_nodes:
                                group_container = muall_nodes[0]
                        except Exception:
                            pass

                    if group_container is not None:
                        try:
                            in_mr_form = bool(
                                el.query_selector_all("xpath=ancestor::form[@id='mrForm' or @name='mrForm'][1]")
                            )
                        except Exception:
                            in_mr_form = False

                        c_class = _norm_lc(group_container.get_attribute("class") or "")
                        has_mr_class = ("mrquestiontable" in c_class) or ("mrmultiple" in c_class)

                        if not has_mr_class:
                            try:
                                has_mr_class = bool(
                                    group_container.query_selector_all(
                                        "xpath=.//*[contains(@class,'mrQuestionTable') or contains(@class,'mrMultiple')]",
                                    )
                                )
                            except Exception:
                                has_mr_class = False

                        is_spssmr_like = in_mr_form or has_mr_class

                        if is_spssmr_like:
                            try:
                                scoped_boxes = group_container.query_selector_all("xpath=.//input[@type='checkbox'][@name]")
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
                                c_tag = _norm_lc(group_container.evaluate("e => e.tagName.toLowerCase()") or "")
                                c_id = _norm_lc(group_container.get_attribute("id") or "")
                                c_legend = ""
                                try:
                                    legends = group_container.query_selector_all("xpath=.//legend[1]")
                                    if legends:
                                        c_legend = _norm_lc((legends[0].inner_text() or "").strip())
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

                    # LimeSurvey multiple-opt pattern: options in the same question
                    # carry names like <sid>X<gid>X<qid>NA or <sid>X<gid>X<qid>othercbox.
                    # The sq\d+|a\d+ rule above already handles SQ001…SQ009 and A1–A9;
                    # this rule covers remaining alphabetic suffixes (NA, othercbox, etc.).
                    # Guard (scope minimal): ancestor div with id="question{digits}" —
                    # a LimeSurvey-specific DOM marker, more reliable than class matching.
                    # Activation: >=2 checkboxes in that container sharing the same prefix.
                    ls_m = re.match(r"^(\d+x\d+x\d+)([a-z][a-z0-9]*)$", clean_name)
                    if ls_m:
                        ls_prefix = ls_m.group(1)
                        try:
                            ls_q_divs = el.query_selector_all(
                                "xpath=ancestor::div[starts-with(@id,'question')][1]",
                            )
                            if ls_q_divs:
                                ls_q_id = ls_q_divs[0].get_attribute("id") or ""
                                if re.match(r"^question\d+$", ls_q_id):
                                    ls_qc = ls_q_divs[0]
                                    ls_sibs = ls_qc.query_selector_all(
                                        "xpath=.//input[@type='checkbox'][@name]"
                                    )
                                    ls_prefix_pat = re.compile(
                                        rf"^{re.escape(ls_prefix)}[a-z]", re.IGNORECASE
                                    )
                                    ls_matching = 0
                                    for _s in ls_sibs:
                                        try:
                                            _sn = _norm_lc(_s.get_attribute("name") or "")
                                        except Exception:
                                            continue
                                        if ls_prefix_pat.match(_sn):
                                            ls_matching += 1
                                    if ls_matching >= 2:
                                        log_debug(
                                            "[DOM_GROUPING]",
                                            f"limesurvey_multi_group prefix={ls_prefix} suffix={ls_m.group(2)} q_id={ls_q_id} matching={ls_matching}",
                                        )
                                        clean_name = ls_prefix
                        except Exception:
                            pass

                    # Decipher/FocusVision pattern: checkboxes d'une même question
                    # nommés `ans10518.0.0`, `ans10518.0.1`, etc. Le suffixe final
                    # identifie l'option et ne doit pas créer un groupement distinct.
                    # Scope minimal: uniquement si le name se termine par `.<digits>`.
                    dotted_base_name = re.sub(r"\.\d+$", "", clean_name)
                    if dotted_base_name and dotted_base_name != clean_name:
                        clean_name = dotted_base_name

                    # Qualtrics JFE pattern: chaque option d'une question checkbox
                    # possède un `name` unique `QR~QID...~<choiceId>`.
                    # Scope DOM strict: uniquement dans un fieldset parent qui expose
                    # un <legend>, et seulement si >=2 checkboxes y partagent le même
                    # préfixe `qr~qid...`.
                    qualtrics_match = re.match(r"^(qr~qid[0-9a-z_-]+)~\d+$", clean_name)
                    if qualtrics_match:
                        try:
                            q_fieldsets = el.query_selector_all("xpath=ancestor::fieldset[legend][1]")
                        except Exception:
                            q_fieldsets = []

                        if q_fieldsets:
                            try:
                                sibling_boxes = q_fieldsets[0].query_selector_all("xpath=.//input[@type='checkbox'][@name]")
                            except Exception:
                                sibling_boxes = []

                            prefixes: list[str] = []
                            for sib in sibling_boxes:
                                try:
                                    sib_name = _norm_lc(sib.get_attribute("name") or "")
                                except Exception:
                                    sib_name = ""
                                m = re.match(r"^(qr~qid[0-9a-z_-]+)~\d+$", sib_name)
                                if m:
                                    prefixes.append(m.group(1))

                            if len(prefixes) >= 2 and len(set(prefixes)) == 1:
                                clean_name = prefixes[0]

                    # Alchemer/Forsta-like pattern: les options checkbox d'une même
                    # question portent un `name` distinct de forme `sgE-...-<optionId>`.
                    # Scope DOM strict: uniquement dans un fieldset de question sg-question
                    # quand >=2 checkboxes partagent la même racine `sgE-...`.
                    if re.match(r"^sge-\d+-\d+-\d+-\d+$", clean_name):
                        try:
                            sg_fieldsets = el.query_selector_all(
                                "xpath=ancestor::fieldset[contains(@class,'sg-question') and starts-with(@id,'sgE-')][1]",
                            )
                        except Exception:
                            sg_fieldsets = []

                        if sg_fieldsets:
                            try:
                                sibling_boxes = sg_fieldsets[0].query_selector_all("xpath=.//input[@type='checkbox'][@name]")
                            except Exception:
                                sibling_boxes = []

                            prefixes: list[str] = []
                            for sib in sibling_boxes:
                                try:
                                    sib_name = _norm_lc(sib.get_attribute("name") or "")
                                except Exception:
                                    sib_name = ""
                                if not re.match(r"^sge-\d+-\d+-\d+-\d+$", sib_name):
                                    continue
                                sib_prefix = re.sub(r"-\d+$", "", sib_name)
                                if sib_prefix:
                                    prefixes.append(sib_prefix)

                            if len(prefixes) >= 2 and len(set(prefixes)) == 1:
                                clean_name = prefixes[0]

                    # YouGov-like pattern: une question checkbox rend chaque option
                    # avec un name distinct suffixé par index (`w38-response-1`, `...-2`, ...)
                    # à l'intérieur d'un `fieldset.question-multiple` unique.
                    # Scope DOM strict: on normalise uniquement si ce fieldset contient
                    # >=2 checkboxes partageant la même racine `prefix-<digits>`.
                    suffix_num_base = re.sub(r"-\d+$", "", clean_name)
                    if suffix_num_base and suffix_num_base != clean_name:
                        try:
                            fieldsets = el.query_selector_all(
                                "xpath=ancestor::fieldset[contains(@class,'question-multiple')][1]",
                            )
                        except Exception:
                            fieldsets = []

                        if fieldsets:
                            try:
                                sibling_boxes = fieldsets[0].query_selector_all("xpath=.//input[@type='checkbox'][@name]")
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
                            containers = el.query_selector_all(
                                "xpath=ancestor::*[contains(@class,'type-multi') and contains(@class,'question-')][1]",
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

                    # IpsosInteractive/card-based pattern: les checkboxes d'une même
                    # question portent des names de la forme "<qid>_<optionid>"
                    # (ex: "50_713", "50_714", ...). Sans normalisation, chaque option
                    # reçoit une group_key distincte et forme son propre bloc.
                    # Scope DOM strict: déclenché uniquement si >=2 checkboxes dans le
                    # même conteneur partagent le même préfixe entier "<qid>_".
                    qid_opt_m = re.match(r"^(\d+)_(\d+)$", clean_name)
                    if qid_opt_m:
                        qid_prefix = qid_opt_m.group(1)
                        scope = None
                        try:
                            for xp in [
                                "xpath=ancestor::div[contains(@class,'card-body')][1]",
                                "xpath=ancestor::div[contains(@class,'card')][1]",
                                "xpath=ancestor::form[1]",
                            ]:
                                nodes = el.query_selector_all(xp)
                                if nodes:
                                    scope = nodes[0]
                                    break
                        except Exception:
                            scope = None

                        if scope is not None:
                            try:
                                sibling_boxes = scope.query_selector_all(
                                    "xpath=.//input[@type='checkbox'][@name]"
                                )
                            except Exception:
                                sibling_boxes = []

                            prefix_pat = re.compile(rf"^{re.escape(qid_prefix)}_\d+$")
                            matching = 0
                            for sib in sibling_boxes:
                                try:
                                    nm = _norm_lc(sib.get_attribute("name") or "")
                                except Exception:
                                    nm = ""
                                if prefix_pat.match(nm):
                                    matching += 1

                            if matching >= 2:
                                log_debug(
                                    "[DOM_GROUPING]",
                                    f"qid_prefix_group key={qid_prefix} matching={matching}",
                                )
                                return qid_prefix

                # Confirmit/Wix pattern: checkboxes of the same question carry distinct
                # names like `SCR1_1`, `SCR1_6`, `SCR1_3` (pattern: <alphanum_QID>_<digits>).
                # They share an ancestor fieldset whose id is `fieldset_<QID>`.
                # Guard DOM strict: both conditions must hold simultaneously —
                # (1) name matches ^[alpha][alphanum]*_<digits>$, AND
                # (2) ancestor fieldset[id=fieldset_<QID>] (case-insensitive) exists
                # with >=2 checkboxes sharing that <QID>_ prefix.
                confirmit_m = re.match(r"^([a-z][a-z0-9]*)_(\d+)$", clean_name)
                if confirmit_m:
                    qid_prefix = confirmit_m.group(1)
                    fieldset_id_lc = f"fieldset_{qid_prefix}"
                    _upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    _lower = "abcdefghijklmnopqrstuvwxyz"
                    _xp = (
                        f"xpath=ancestor::fieldset["
                        f"translate(@id,'{_upper}','{_lower}')='{fieldset_id_lc}'"
                        f"][1]"
                    )
                    try:
                        fs_nodes = el.query_selector_all(_xp)
                    except Exception:
                        fs_nodes = []
                    if fs_nodes:
                        try:
                            sibling_boxes = fs_nodes[0].query_selector_all(
                                "xpath=.//input[@type='checkbox'][@name]"
                            )
                        except Exception:
                            sibling_boxes = []
                        prefix_pat = re.compile(
                            rf"^{re.escape(qid_prefix)}_\d+$", re.IGNORECASE
                        )
                        matching = sum(
                            1 for sib in sibling_boxes
                            if prefix_pat.match(_norm_lc(sib.get_attribute("name") or ""))
                        )
                        if matching >= 2:
                            log_debug(
                                "[DOM_GROUPING]",
                                f"confirmit_wix_group prefix={qid_prefix} fieldset={fieldset_id_lc} matching={matching}",
                            )
                            return qid_prefix

                # ARIA-group pattern (SurveyJS / sd-selectbase and similar): options of
                # the same checkbox question carry distinct names but all share a single
                # fieldset[role="group"][aria-labelledby] container.
                # Guard DOM strict: ancestor fieldset[@role="group"][@aria-labelledby]
                # + >=2 checkboxes with ALL-distinct names → group by aria-labelledby.
                try:
                    grp_fs = el.query_selector_all(
                        "xpath=ancestor::fieldset[@role='group'][@aria-labelledby][1]",
                    )
                except Exception:
                    grp_fs = []

                if grp_fs:
                    grp_labelledby = _norm_lc(
                        grp_fs[0].get_attribute("aria-labelledby") or ""
                    )
                    if grp_labelledby:
                        try:
                            sib_boxes = grp_fs[0].query_selector_all(
                                "xpath=.//input[@type='checkbox'][@name]"
                            )
                        except Exception:
                            sib_boxes = []
                        sib_names = []
                        for _s in sib_boxes:
                            try:
                                _nm = _norm_lc(_s.get_attribute("name") or "")
                            except Exception:
                                _nm = ""
                            if _nm:
                                sib_names.append(_nm)
                        if len(sib_names) >= 2 and len(set(sib_names)) == len(sib_names):
                            log_debug(
                                "[DOM_GROUPING]",
                                f"aria_group_fieldset key={grp_labelledby} boxes={len(sib_names)}",
                            )
                            return grp_labelledby

                return _norm_lc(clean_name)

            # SSI Confirmit / "graphical radiobox" pattern: un widget div[role="radio"]
            # est un frère direct de l'input natif caché (HideElement) dans le même
            # conteneur row. Le widget n'a pas d'attribut `name` ; l'input natif a
            # name="SEXE". Sans ce guard, les deux éléments produisent des clés
            # différentes → deux question_blocks identiques.
            # Guard strict: tag != input + sibling input[type="radio"][name] non vide.
            try:
                tag_name = (el.evaluate("e => e.tagName.toLowerCase()") or "").lower()
                if tag_name != "input":
                    sibling_inputs = el.query_selector_all(
                        "xpath=../input[@type='radio' and @name and normalize-space(@name)!=''][1]"
                    )
                    if sibling_inputs:
                        native_name = _norm_lc(sibling_inputs[0].get_attribute("name") or "")
                        if native_name:
                            log_debug(
                                "[DOM_GROUPING]",
                                f"graphical_radio_native_name: widget={tag_name} -> name={native_name}",
                            )
                            return f"radio:name:{native_name}"
            except Exception:
                pass

            # Fallback DOM-first: certains providers (ex: Quantilope) n'exposent
            # aucun `name` sur les radios mais regroupent visuellement les options
            # dans un conteneur commun (aria-labelledby / question-items / options).
            # On dérive alors une clé stable à partir de cet ancêtre.
            try:
                container = None
                container_xpaths = [
                    "xpath=ancestor::*[@role='radiogroup' or @role='group' or @aria-labelledby][1]",
                    "xpath=ancestor::*[@role='listbox' or contains(@class,'multi-select-container') or contains(@class,'single-choice-container')][1]",
                    "xpath=ancestor::*[contains(@class,'question-items') or contains(@class,'answers') or contains(@class,'options') or contains(@class,'choices')][1]",
                ]
                for xp in container_xpaths:
                    nodes = el.query_selector_all(xp)
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
    - radio / button: 1 (exclusif)
    - checkbox: nombre total d'options moins options exclusives
    - autres: 1 (par défaut)
    """
    if itype in {"radio", "button"}:
        return 1
    if itype == "checkbox":
        return compute_checkbox_max_select(options, question_text)
    return 1


def _compute_min_select(itype: str, question_text: str | None, options: List[str], max_select: int) -> int:
    if itype in {"radio", "button"}:
        return 1
    if itype == "checkbox":
        return compute_min_select(question_text, options, max_select)
    return 1
