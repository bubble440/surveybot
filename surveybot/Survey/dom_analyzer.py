# Survey/dom_analyzer.py
"""
DOM Analyzer — extraction TEXT-ONLY des questions de survey.

Objectif:
- Scanner le DOM
- Identifier chaque question (1 bloc par question, PAS 1 bloc par option radio)
- Déterminer le type d'input attendu
- Extraire les options associées
- Ajouter une contrainte de cardinalité (max_select)

Aucune dépendance image.
Compatible local / prod.
Pensé pour 100+ bots.
"""

from __future__ import annotations
from typing import List, Dict, Any, Tuple
import re, time
import unicodedata
import os
from Survey.dom_registry import clear_registry, register_target, make_target_id
from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain
from selenium.webdriver.common.by import By
import Survey.dom_registry as dom_registry
from Survey.sliderpoints_extractor import extract_sliderpoints_question_blocks

# =========================
# Helpers texte
# =========================

def _norm(text: str) -> str:
    """Normalisation douce pour comparaison."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ")
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text

def _norm_lc(text: str) -> str:
    return _norm(text).lower()

# --- Champs techniques/ASP.NET à ignorer (anti-pollution prompt) ---
_SYS_FIELD_TOKENS = (
    "__viewstate", "viewstate",
    "__eventvalidation", "eventvalidation",
    "__viewstategenerator", "viewstategenerator",
    "screen", "screener",
    "responsestatus", "clientrs_status",
    "ctl00$content$hf", "hf",  # hidden fields typiques
)

def _looks_like_system_field(el) -> bool:
    """
    Détecte les inputs techniques (ASP.NET / hidden / tracking) pour ne pas les traiter comme questions.
    IMPORTANT: ça évite d'envoyer du bruit à OpenAI -> moins cher + plus fiable.
    """
    try:
        t = (el.get_attribute("type") or "").strip().lower()
        if t == "hidden":
            return True

        id_ = (el.get_attribute("id") or "").strip().lower()
        name = (el.get_attribute("name") or "").strip().lower()
        if any(tok in id_ or tok in name for tok in _SYS_FIELD_TOKENS):
            return True
    except Exception:
        pass
    return False

def _is_actionable_visible(el) -> bool:
    """Retourne True si l'élément est réellement actionnable côté UI.

    Fix principal:
    - Exclure les inputs utilitaires/masqués LimeSurvey (ls-js-hidden) qui polluent l'extraction
      (ex: confirm-clearall), sinon OpenAI renvoie des actions impossibles à appliquer.

    Compat:
    - Inputs masqués mais cliquables via wrapper visible (Decipher/FocusVision: clickableCell / sq-cardrating-button).
    - Inputs masqués mais label visible (custom UI).
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

        def _rect_ok(node) -> bool:
            try:
                r = getattr(node, "rect", None) or {}
                return (r.get("width", 0) or 0) > 2 and (r.get("height", 0) or 0) > 2
            except Exception:
                return False

        # 1) Visible + taille non nulle
        try:
            if el.is_displayed() and _rect_ok(el):
                return True
        except Exception:
            pass

        tag = ((getattr(el, "tag_name", "") or "").lower() or "")
        if tag == "input":
            t = (el.get_attribute("type") or "").strip().lower()
            if t in ("radio", "checkbox"):
                # 2) Wrapper visible (Decipher/FocusVision)
                try:
                    anc = el.find_element(
                        By.XPATH,
                        "ancestor::*[contains(@class,'clickableCell') or contains(@class,'sq-cardrating-button')][1]",
                    )
                    if anc and anc.is_displayed() and _rect_ok(anc):
                        return True
                except Exception:
                    pass

                # 3) Wrapper visible (Cint/QPS): <div class="answer ..."> contient input masqué + label/span visible
                try:
                    anc = el.find_element(
                        By.XPATH,
                        "ancestor::*[contains(concat(' ',normalize-space(@class),' '),' answer ')][1]"
                    )
                    if anc and anc.is_displayed() and _rect_ok(anc):
                        return True
                except Exception:
                    pass

                # 4) Label visible (custom UI) — version robuste sans dépendre de el._parent
                try:
                    el_id = (el.get_attribute("id") or "").strip()
                    if el_id:
                        # a) label adjacent
                        labs = el.find_elements(
                            By.XPATH,
                            f"following-sibling::label[@for='{el_id}'][1] | preceding-sibling::label[@for='{el_id}'][1]"
                        )

                        # b) label sous le parent immédiat
                        if not labs:
                            labs = el.find_elements(By.XPATH, f"ancestor::*[1]//label[@for='{el_id}'][1]")

                        # c) fallback “document” via racine <html> (iframe-safe)
                        if not labs:
                            try:
                                root = el.find_element(By.XPATH, "ancestor-or-self::html[1]")
                                labs = root.find_elements(By.XPATH, f".//label[@for='{el_id}']")
                            except Exception:
                                labs = []

                        for lab in (labs or [])[:5]:
                            try:
                                if lab.is_displayed() and _rect_ok(lab):
                                    return True
                            except Exception:
                                continue
                except Exception:
                    pass

                # 4) Label ancêtre visible (input inside <label> ...)
                try:
                    lab = el.find_element(By.XPATH, "ancestor::label[1]")
                    if lab and lab.is_displayed() and _rect_ok(lab):
                        return True
                except Exception:
                    pass

        return False
    except Exception:
        return False

def _best_xpath_for_element(driver, el) -> str:
    """
    XPath robuste:
    - si id => //*[@id='...']
    - sinon XPath absolu généré via JS (stable sur la page courante)
    """
    try:
        el_id = (el.get_attribute("id") or "").strip()
        if el_id:
            return f"//*[@id='{el_id}']"
    except Exception:
        pass

    # XPath absolu JS
    js = r"""
    function absoluteXPath(element) {
      if (element.tagName.toLowerCase() === 'html') return '/html[1]';
      if (element === document.body) return '/html[1]/body[1]';
      var ix = 0;
      var siblings = element.parentNode.childNodes;
      for (var i = 0; i < siblings.length; i++) {
        var sibling = siblings[i];
        if (sibling === element) {
          var tag = element.tagName.toLowerCase();
          return absoluteXPath(element.parentNode) + '/' + tag + '[' + (ix + 1) + ']';
        }
        if (sibling.nodeType === 1 && sibling.tagName === element.tagName) {
          ix++;
        }
      }
    }
    return absoluteXPath(arguments[0]);
    """
    try:
        xp = driver.execute_script(js, el)
        if xp:
            return xp
    except Exception:
        pass

    return ""

def _xpath_literal(s: str) -> str:
    """
    Literal XPath safe, même si la chaîne contient des quotes.
    """
    s = s or ""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    out = []
    for i, p in enumerate(parts):
        if p:
            out.append(f"'{p}'")
        if i != len(parts) - 1:
            out.append("\"'\"")
    return "concat(" + ", ".join(out) + ")"

def _norm_key(text: str) -> str:
    return _norm_lc(text)

def _is_question_text(text: str) -> bool:
    """Heuristique simple pour identifier une question."""
    if not text:
        return False
    low = text.lower()
    if "?" in text:
        return True
    keywords = [
        "what", "which", "how", "why",
        "quel", "quelle", "quels", "quelles", "combien",
        "où", "ou", "comment", "pourquoi",
        "âge", "age", "gender", "education", "niveau",
    ]
    return any(k in low for k in keywords)

# =========================
# Détection du type d'input
# =========================

def _detect_itype(el) -> str:
    tag = (el.tag_name or "").lower()

    if tag == "input":
        t = (el.get_attribute("type") or "").strip().lower()

        # ⚠️ Hidden = jamais une question
        if t == "hidden":
            return "hidden"

        # Boutons/submit = CTA (pas du texte)
        if t in ("submit", "button", "image", "reset"):
            return "button"

        if t in ("radio", "checkbox"):
            return t

        # Inputs texte usuels
        if t in ("text", "number", "email", "tel", "search", "password", ""):
            return "text"
        if t in ("date", "datetime-local"):
            return "text"

        # Défaut: texte (mais on filtrera via visibilité + system fields)
        return "text"

    if tag == "select":
        return "dropdown"

    if tag == "textarea":
        return "textarea"

    if tag in ("button", "a"):
        return "button"

    role = (el.get_attribute("role") or "").lower()
    if role in ("radio", "checkbox", "button"):
        return role

    return "unknown"

# =========================
# Extraction labels/options
# =========================

def _find_question_text_near_element(driver, el) -> str:
    """
    Cherche un texte "question" visuellement proche (au-dessus) de l'élément input/textarea.
    Objectif: éviter les fallbacks vision quand la question est bien dans le DOM
    mais pas dans le même conteneur HTML (Angular/React très fréquent).
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
            el,
        )
        return (txt or "").strip()
    except Exception:
        return ""

def _find_associated_label(driver, el) -> str:
    """
    Récupère le libellé associé à un input (souvent l'OPTION pour radio/checkbox).
    """
    try:
        el_id = el.get_attribute("id")
        if el_id:
            labels = driver.find_elements(By.XPATH, f"//label[@for='{el_id}']")
            if labels:
                return _norm(labels[0].text or labels[0].get_attribute("innerText") or "")
    except Exception:
        pass

    try:
        parent_label = el.find_element(By.XPATH, "ancestor::label")
        if parent_label:
            return _norm(parent_label.text or parent_label.get_attribute("innerText") or "")
    except Exception:
        pass

    # ✅ NEW: pattern très fréquent (Angular/React) : input + label(vide) + span/div texte
    # Exemple: <input ...><label for="..."></label><span class="_checkboxText">Agree to all</span>
    try:
        for i in range(1, 5):
            sibs = el.find_elements(By.XPATH, f"following-sibling::*[{i}]")
            if not sibs:
                continue
            s = sibs[0]
            t = _norm(s.text or s.get_attribute("innerText") or "")
            if t and len(t) >= 2:
                return t
    except Exception:
        pass

    # fallback léger: aria-label / name
    for attr in ("aria-label", "name", "placeholder"):
        try:
            v = el.get_attribute(attr)
            if v and len(v.strip()) >= 2:
                return _norm(v)
        except Exception:
            pass

    return ""

def _nearest_question_container(el):
    """
    Cherche un conteneur 'question-like' (fieldset / role group / class question...).
    """
    xps = [
        "ancestor::*[self::fieldset or @role='radiogroup' or @role='group' "
        "or contains(@class,'question') or contains(@class,'Question') "
        "or contains(@class,'form-group') or contains(@class,'field')][1]",
        "ancestor::*[self::div or self::section or self::form][1]",
    ]
    for xp in xps:
        try:
            c = el.find_element(By.XPATH, xp)
            if c:
                return c
        except Exception:
            continue
    return None

def _extract_question_from_container(container, options: List[str]) -> str:
    """
    Extrait le texte de QUESTION (pas les options) depuis un conteneur.
    Stratégie:
    - prioriser legend/h1/h2/h3/h4/label "question-like"
    - fallback: lignes de texte du conteneur
    - exclure toute ligne qui est une option connue
    """
    opt_lc = {_norm_lc(o) for o in (options or []) if o}

    candidates: List[str] = []

    # 1) titres/entêtes
    head_xp = (
        ".//*[self::legend or self::h1 or self::h2 or self::h3 or self::h4 or self::h5 "
        "or contains(@class,'question-text') or contains(@class,'QuestionText') "
        "or contains(@class,'question__title') or contains(@data-test-id,'question')]"
    )
    try:
        heads = container.find_elements(By.XPATH, head_xp)
    except Exception:
        heads = []

    for h in heads[:15]:
        try:
            t = _norm(h.text or h.get_attribute("innerText") or "")
            if not t:
                continue
            tlc = _norm_lc(t)
            if tlc in opt_lc:
                continue
            candidates.append(t)
        except Exception:
            continue

    # 2) fallback: texte brut du conteneur
    try:
        raw = (container.text or container.get_attribute("innerText") or "")
        for line in (raw.splitlines() if raw else []):
            t = _norm(line)
            if not t:
                continue
            tlc = _norm_lc(t)
            if tlc in opt_lc:
                continue
            candidates.append(t)
    except Exception:
        pass

    # scoring simple
    best = ""
    best_sc = -1
    for t in candidates:
        tl = _norm(t)
        if len(tl) < 5:
            continue

        sc = 0
        tlc = tl.lower()

        if _is_question_text(tl):
            sc += 3

        # ✅ Bonus "consigne explicite" (souvent le vrai libellé dans les control questions)
        directive_tokens = (
            "veuillez", "merci de", "please",
            "select", "choose",
            "choisir", "choisissez",
            "sélectionnez", "selectionnez",
            "cochez", "cliquez",
            "indiquez", "entrez", "saisissez",
        )
        if any(tok in tlc for tok in directive_tokens):
            sc += 4

        # léger malus pour les phrases d'intro (souvent au-dessus de la vraie consigne)
        boilerplate_tokens = (
            "la qualité de vos réponses",
            "standards de qualité",
            "votre avis est important",
            "merci pour votre participation",
            "nous vous remercions",
        )
        if any(tok in tlc for tok in boilerplate_tokens):
            sc -= 2

        # bonus si ça ressemble à une vraie question et pas à un label court
        sc += min(len(tl), 120) // 20
        if "?" in tl:
            sc += 2

        if sc > best_sc:
            best_sc = sc
            best = tl

    return best

def _group_key_for_choice(el, itype: str) -> str:
    """
    Crée une clé de groupe stable-ish pour radio/checkbox:
    - name si présent (meilleur)
    - aria-labelledby sinon
    - sinon conteneur question
    """
    try:
        name = (el.get_attribute("name") or "").strip()
        if name:
            return f"{itype}:name:{name}"
    except Exception:
        pass

    try:
        labby = (el.get_attribute("aria-labelledby") or "").strip()
        if labby:
            return f"{itype}:labby:{labby}"
    except Exception:
        pass

    c = _nearest_question_container(el)
    try:
        cid = (c.get_attribute("id") or "").strip() if c is not None else ""
        if cid:
            return f"{itype}:container_id:{cid}"
    except Exception:
        pass

    # fallback ultime: id(obj) (pas stable cross-run mais ok pour une page)
    return f"{itype}:container_obj:{id(c) if c is not None else id(el)}"

def _compute_max_select(itype: str, options: List[str]) -> int:
    """
    Règle métier simple:
    - radio / dropdown / text / textarea / button => 1
    - checkbox => multi (cap à 3 par défaut)
    """
    if itype == "checkbox":
        n = len(options or [])
        if n <= 1:
            return 1
        return min(3, n)
    return 1

# =========================
# Sélection de contexte (iframe-aware)
# =========================

def _env_truthy(name: str, default: str = "0") -> bool:
    v = (os.getenv(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "on")

def _wait_for_survey_dom(driver, timeout_s: float = 1.2, step_s: float = 0.2) -> bool:
    """
    Attente courte et bornée: évite le scan DOM trop tôt (page pas encore prête).
    Pas de retry infini: max ~1.2s.
    """
    t0 = time.time()
    while (time.time() - t0) < timeout_s:
        ok = False

        # 1) Chemin rapide JS (souvent le plus fiable)
        try:
            ok = bool(
                driver.execute_script(
                    """
                    const root =
                      document.querySelector('#survey')
                      || document.querySelector('div.question')
                      || document.querySelector('.answers.answers-list')
                      || document.body;
                    if (!root) return false;

                    // On attend un "signal answerable" (inputs OU sliderpoints),
                    // pas juste un conteneur vide rendu trop tôt.
                    const answerable = root.querySelector(
                      "input[type='radio'], input[type='checkbox'], select, textarea, " +
                      "input[type='text'], input[type='number'], input[type='email'], input[type='tel'], input[type='search'], " +
                      ".sq-sliderpoints, .sq-sliderpoints-container, .sliderpoints_legend, " +
                      ".answers.answers-list input, .answers.answers-list select, .answers.answers-list textarea"
                    );
                    return !!answerable;
                    """
                )
            )
        except Exception:
            ok = False

        if ok:
            return True

        # 2) Fallback Selenium (utile si execute_script est instable dans un contexte)
        try:
            if driver.find_elements(
                By.CSS_SELECTOR,
                "#survey input, #survey select, #survey textarea, "
                "div.question input, div.question select, div.question textarea, "
                ".sq-sliderpoints, .sq-sliderpoints-container, .sliderpoints_legend, "
                ".answers.answers-list input, .answers.answers-list select, .answers.answers-list textarea"
            ):
                return True
        except Exception:
            pass

        time.sleep(step_s)

    return False

def _score_dom_context(driver) -> Dict[str, Any]:
    """Score cheap d'un contexte DOM (default ou iframe)."""

    def _safe_is_displayed(el) -> bool:
        try:
            return bool(el.is_displayed())
        except Exception:
            return False

    def _fallback_score() -> Dict[str, Any]:
        # Texte
        try:
            body_text = (driver.find_element(By.TAG_NAME, "body").text or "")
        except Exception:
            body_text = ""
        t = (body_text or "").strip()

        # Inputs/actionnables
        try:
            nodes = driver.find_elements(
                By.CSS_SELECTOR,
                "input, textarea, select, button, a[role='button'], [role='button']",
            )
        except Exception:
            nodes = []

        inputs_count = len(nodes)
        visible_count = sum(1 for el in nodes if _safe_is_displayed(el))

        # Signaux question (inclut FocusVision: inputs ans* souvent masqués)
        try:
            q_nodes = driver.find_elements(
                By.CSS_SELECTOR,
                "input[name^='question_'], textarea[name^='question_'], select[name^='question_'], "
                ".js-question-options input, .js-question-options select, .js-question-options textarea, "
                "div.question input[type='radio'], div.question input[type='checkbox'], "
                ".answers.answers-list input[type='radio'], .answers.answers-list input[type='checkbox']",
            )
        except Exception:
            q_nodes = []
        q_count = len(q_nodes)

        # Labels
        try:
            label_nodes = driver.find_elements(
                By.CSS_SELECTOR,
                ".js-question-options label, label.radio, label.checkbox, .answers.answers-list label[for]",
            )
        except Exception:
            label_nodes = []
        visible_label_count = sum(1 for el in label_nodes if _safe_is_displayed(el))

        # Racines survey
        try:
            has_root = bool(
                driver.find_elements(
                    By.CSS_SELECTOR,
                    ".js-question-options, #templates .question, .survey-content #templates, "
                    "#survey, #survey.survey-container, div[id^='question_'], .sq-cardsort, "
                    ".answers.answers-list, div.question",
                )
            )
        except Exception:
            has_root = False

        has_words = bool(
            re.search(
                r"question|suivant|next|continue|prochaine|étape|sondage|enquête|profil|survey",
                t.lower(),
                re.I,
            )
        )

        score = (
            q_count * 5000
            + visible_label_count * 2000
            + visible_count * 1000
            + min(len(t), 2000)
            + (3000 if has_root else 0)
            + (2000 if has_words else 0)
        )

        return {
            "score": score,
            "q_count": q_count,
            "visible_label_count": visible_label_count,
            "visible_count": visible_count,
            "inputs_count": inputs_count,
            "text_len": len(t),
            "has_survey_root": has_root,
            "has_survey_words": has_words,
        }

    # --- chemin principal JS ---
    try:
        res = driver.execute_script(
            """
            const body = document.body;
            const text = (body && body.innerText) ? body.innerText : "";
            const t = (text || "").trim();
            const textLen = t.length;

            const nodes = document.querySelectorAll(
              "input, textarea, select, button, a[role='button'], [role='button']"
            );
            const inputsCount = nodes ? nodes.length : 0;

            let visibleCount = 0;
            for (const el of nodes) {
              try {
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                const visible = r.width > 2 && r.height > 2 && st.display !== 'none' && st.visibility !== 'hidden';
                if (visible) visibleCount++;
              } catch (e) {}
            }

            const qNodes = document.querySelectorAll(
              "input[name^='question_'], textarea[name^='question_'], select[name^='question_'], " +
              ".js-question-options input, .js-question-options select, .js-question-options textarea"
            );
            let qCount = qNodes ? qNodes.length : 0;

            if (!qCount) {
              const hasCardsort = !!document.querySelector(".sq-cardsort, [class*='sq-cardsort-']");
              if (hasCardsort) qCount = 1;
            }

            const labelNodes = document.querySelectorAll(".js-question-options label, label.radio, label.checkbox");
            let visibleLabelCount = 0;
            for (const el of (labelNodes || [])) {
              try {
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                const visible = r.width > 2 && r.height > 2 && st.display !== 'none' && st.visibility !== 'hidden';
                if (visible) visibleLabelCount++;
              } catch (e) {}
            }

            const hasSurveyRoot = !!document.querySelector(
              ".js-question-options, #templates .question, .survey-content #templates, " +
              "#survey.survey-container, div[id^=\"question_\"], .sq-cardsort"
            );

            const low = t.toLowerCase();
            const hasSurveyWords = /question|suivant|next|continue|prochaine|étape|sondage|enquête|profil|survey/i.test(low);

            return {textLen, inputsCount, visibleCount, qCount, visibleLabelCount, hasSurveyRoot, hasSurveyWords};
            """
        ) or {}
    except Exception:
        res = {}

    text_len = int(res.get("textLen") or 0)
    inputs_count = int(res.get("inputsCount") or 0)
    visible_count = int(res.get("visibleCount") or 0)
    q_count = int(res.get("qCount") or 0)
    visible_label_count = int(res.get("visibleLabelCount") or 0)
    has_root = bool(res.get("hasSurveyRoot") or False)
    has_words = bool(res.get("hasSurveyWords") or False)

    # Si le chemin JS ne remonte rien d'utile (ou a silencieusement échoué),
    # on bascule sur un score Selenium.
    if (
        (not res)
        or (
            text_len == 0
            and inputs_count == 0
            and visible_count == 0
            and q_count == 0
            and visible_label_count == 0
            and (not has_root)
            and (not has_words)
        )
    ):
        return _fallback_score()

    score = (
        q_count * 5000
        + visible_label_count * 2000
        + visible_count * 1000
        + min(text_len, 2000)
        + (3000 if has_root else 0)
        + (2000 if has_words else 0)
    )

    return {
        "score": score,
        "q_count": q_count,
        "visible_label_count": visible_label_count,
        "visible_count": visible_count,
        "inputs_count": inputs_count,
        "text_len": text_len,
        "has_survey_root": has_root,
        "has_survey_words": has_words,
    }

def _select_best_frame_chain(driver, max_depth: int = 2) -> Tuple[List[int], Dict[str, Any]]:
    """
    Parcourt [] + iframes (profondeur <= max_depth) et choisit le meilleur contexte.
    Comportement déterministe, sans retries infinis.
    """
    best_chain: List[int] = []
    best_meta: Dict[str, Any] = {"score": -1}

    for chain in iter_frame_chains(driver, max_depth=max_depth):
        try:
            with switch_to_frame_chain(driver, chain) as ok:
                if not ok:
                    continue
                meta = _score_dom_context(driver)
        except Exception:
            continue

        if int(meta.get("score") or 0) > int(best_meta.get("score") or 0):
            best_chain = list(chain)
            best_meta = meta

    if _env_truthy("DOM_DEBUG_FRAMES", "0"):
        try:
            print(f"[DOM] best_frame_chain={best_chain} meta={best_meta}")
        except Exception:
            pass

    return best_chain, best_meta

# =========================
# API principale
# =========================

# --- FocusVision: answers-list (inputs radio/checkbox masqués + wrapper clickableCell) ---

def _xpath_literal(s: str) -> str:
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"

def _extract_focusvision_answers_list_groups(driver, frame_chain: list[int] | None) -> list[dict]:
    blocks: list[dict] = []

    # question container FocusVision
    q_containers = driver.find_elements(By.CSS_SELECTOR, "div.question[role='radiogroup'], div.question.radio, div.question.checkbox")
    for q in q_containers:
        try:
            answers = q.find_element(By.CSS_SELECTOR, ".answers.answers-list")
        except Exception:
            continue

        # inputs souvent masqués (fir-hidden). Variante FocusVision:
        # - clickableCell peut être sur .element OU sur un ancêtre/descendant.
        # => on élargit un peu, mais toujours sous .answers.answers-list (scope strict).
        inputs = answers.find_elements(
            By.CSS_SELECTOR,
            "input[type='radio'], input[type='checkbox']"
        )
        if len(inputs) < 2:
            continue

        # question texte
        question = ""
        try:
            question = (q.find_element(By.CSS_SELECTOR, ".question-text").text or "").strip()
        except Exception:
            question = (q.text or "").strip().split("\n")[0].strip()

        # regrouper par name
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

                # label visible
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
            target_id = dom_registry.make_target_id("group", group_key, question or name)

            dom_registry.register_target(target_id, {
                "kind": "group",
                "frame_chain": list(frame_chain or []),
                "itype": itype,
                "group_key": group_key,
                "question": question,
                "input_name": name,
                "max_select": 1,
                "options": options,
                "option_xpath_map": option_xpath_map,
            })

            blocks.append({
                "question": question,
                "itype": itype,
                "options": options,
                "max_select": 1,
                "target_id": target_id,
                "context": {"kind": "group", "group_key": group_key},
            })

    return blocks

def _extract_focusvision_cardsort_block(driver, frame_chain: list[int] | None) -> dict | None:
    """
    FocusVision/Decipher cardsort: UI visible = 1 carte active + des buckets.

    Objectif:
    - Construire 1 bloc radio pour la carte visible (row) avec options = buckets
    - Enregistrer un target_id qui clique le bucket VISIBLE (pas un label/input caché)
    """
    try:
        cardsorts = driver.find_elements(By.CSS_SELECTOR, ".sq-cardsort")
    except Exception:
        cardsorts = []

    def _vis(el) -> bool:
        # Plus robuste que is_displayed() sur les UIs cardsort (overflow/scroll).
        try:
            return bool(driver.execute_script(
                """
                const el = arguments[0];
                if (!el) return false;
                const s = window.getComputedStyle(el);
                if (!s) return false;
                if (s.display === "none" || s.visibility === "hidden" || s.opacity === "0") return false;
                const r = el.getBoundingClientRect();
                return (r.width > 8 && r.height > 8);
                """,
                el
            ))
        except Exception:
            try:
                return bool(el.is_displayed())
            except Exception:
                return False

    cs = None
    for el in cardsorts:
        if _vis(el):
            cs = el
            break

    if not cs:
        return None

    # Carte active (visible) : li dans .sq-cardsort-cards
    active = None
    try:
        cards = cs.find_elements(By.CSS_SELECTOR, ".sq-cardsort-cards li")
    except Exception:
        cards = []

    for c in cards:
        try:
            cl = (c.get_attribute("class") or "").lower()
            if "sq-cardsort-completion" in cl:
                continue
            if _vis(c):
                active = c
                break
        except Exception:
            continue

    if not active:
        return None

    # Texte de la carte (row label)
    card_text = ""
    try:
        legend = active.find_elements(By.CSS_SELECTOR, ".sq-cardsort-card-legend")
        if legend:
            card_text = _norm(legend[0].text or legend[0].get_attribute("innerText") or "")
    except Exception as e:
        if os.getenv("RUN_ENV", "local") == "local":
            print(f"[DOM_ANALYZER][WARN] focusvision extract: {type(e).__name__}: {e}")

    if not card_text:
        try:
            card_text = _norm(active.text or active.get_attribute("innerText") or "")
        except Exception:
            card_text = ""

    if not card_text or len(card_text) < 3:
        return None

    # Conteneur question (ex: <div id="question_Q1" class="question ...">)
    container = None
    try:
        container = cs.find_element(
            By.XPATH,
            "ancestor::*[starts-with(@id,'question_') or contains(@class,'question')][1]",
        )
    except Exception:
        container = None

    # Question globale (ex: "Quand avez-vous acheté ... ?")
    global_q = ""
    if container is not None:
        global_q = _extract_question_from_container(container, options=[]) or ""
    global_q = _norm(global_q)

    # Options = buckets (éviter le compteur)
    options: list[str] = []
    option_xpath_map: dict[str, str] = {}

    try:
        buckets = cs.find_elements(By.CSS_SELECTOR, "li.sq-cardsort-bucket")
    except Exception:
        buckets = []

    # Scope XPath: limiter au container si id dispo
    scope_prefix = ""
    try:
        cid = (container.get_attribute("id") or "").strip() if container is not None else ""
        if cid:
            scope_prefix = f"//*[@id={_xpath_literal(cid)}]"
    except Exception:
        scope_prefix = ""

    for b in buckets:
        if not _vis(b):
            continue

        lbl = ""
        try:
            ps = b.find_elements(By.CSS_SELECTOR, ".sq-cardsort-bucket-legend")
            if ps:
                lbl = _norm(ps[0].text or ps[0].get_attribute("innerText") or "")
        except Exception as e:
            if os.getenv("RUN_ENV", "local") == "local":
                print(f"[DOM_ANALYZER][WARN] focusvision extract: {type(e).__name__}: {e}")
            continue

        if not lbl:
            try:
                raw = _norm(b.text or b.get_attribute("innerText") or "")
                # ex: "Aujourd'hui\n1" => garder 1ère ligne
                lbl = _norm((raw.splitlines()[0] if raw else ""))
            except Exception:
                lbl = ""

        if not lbl or lbl in {"<<", ">>", "<", ">"}:
            continue

        options.append(lbl)

        # XPath du bucket (clickable)
        try:
            b_index = (b.get_attribute("index") or "").strip()
        except Exception:
            b_index = ""

        if b_index:
            xp = f"{scope_prefix}//*[contains(@class,'sq-cardsort-bucket') and @index={_xpath_literal(b_index)}]"
        else:
            xp = (
                f"{scope_prefix}//*[contains(@class,'sq-cardsort-bucket')]"
                f"[.//p[contains(@class,'sq-cardsort-bucket-legend') and normalize-space(.)={_xpath_literal(lbl)}]]"
            )

        option_xpath_map[_norm_key(lbl)] = xp

    options = list(dict.fromkeys([o for o in options if o]))
    if not options or not option_xpath_map:
        return None

    # Question envoyée à OpenAI (question globale + carte)
    question = global_q
    if question:
        question = f"{question} — {card_text}"
    else:
        question = card_text

    # group_key stable-ish
    qid = ""
    try:
        if container is not None:
            cid = (container.get_attribute("id") or "").strip()
            if cid.startswith("question_"):
                qid = cid.replace("question_", "", 1)
    except Exception:
        qid = ""

    card_idx = ""
    try:
        card_idx = (active.get_attribute("index") or "").strip()
    except Exception:
        card_idx = ""

    group_key = f"cardsort:{qid}:{card_idx}" if qid or card_idx else f"cardsort:{id(cs)}:{id(active)}"
    target_id = make_target_id("group", group_key, question)

    register_target(
        target_id,
        {
            "kind": "group",
            "itype": "radio",
            "group_key": group_key,
            "question": question,
            "option_xpath_map": option_xpath_map,
            "frame_chain": frame_chain or [],
            "cardsort": True,
            "cardsort_qid": qid,
            "cardsort_card_index": card_idx,
        },
    )

    return {
        "question": question,
        "itype": "radio",
        "options": options,
        "max_select": 1,
        "target_id": target_id,
        "context": {"kind": "group", "group_key": group_key, "cardsort": True},
    }

def _analyze_dom_current_context(driver, frame_chain=None) -> List[Dict[str, Any]]:
    """
    Analyse le DOM courant et retourne une liste de QuestionBlock.
    IMPORTANT: 1 bloc par question (group radio/checkbox).
    """
    
    frame_chain = frame_chain or []
    question_blocks: List[Dict[str, Any]] = []
    clear_registry()

    # --- 0) FocusVision cardsort (UI visible) ---
    # Si présent, on préfère cette stratégie (1 seule carte active) à l'extraction radio/checkbox cachée.
    try:
        cs_block = _extract_focusvision_cardsort_block(driver, frame_chain)
        if cs_block:
            return [cs_block]
    except Exception:
        pass

    # --- 1) Radios / checkboxes groupés ---
    try:
        choice_els = driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)"
        )
    except Exception:
        choice_els = []

    # ✅ Anti-bruit (Decipher/FIR, etc.) :
    # Des icônes SVG portent role="radio"/"checkbox" mais ne sont pas des inputs actionnables.
    # Si on les garde, on duplique les groupes => OpenAI renvoie Q1/Q2 pour la même question.
    try:
        has_real_inputs = any((e.tag_name or "").lower() == "input" for e in choice_els)
        if has_real_inputs:
            filtered = []
            for e in choice_els:
                try:
                    tag = (e.tag_name or "").lower()
                    if tag in {"svg", "path", "polygon", "rect", "circle", "g", "title"}:
                        continue
                    filtered.append(e)
                except Exception:
                    continue
            choice_els = filtered
    except Exception:
        pass

    groups: Dict[str, List[Any]] = {}
    for el in choice_els:
        try:
            itype = _detect_itype(el)
            if itype not in ("radio", "checkbox"):
                continue
            # Anti-bruit: ignorer inputs utilitaires/masqués et non actionnables
            try:
                if _looks_like_system_field(el):
                    continue
            except Exception:
                pass
            if not _is_actionable_visible(el):
                continue
            k = _group_key_for_choice(el, itype)
            groups.setdefault(k, []).append(el)
        except Exception:
            continue

    seen_signatures = set()

    for k, els in groups.items():
        try:
            # type homogène dans une clé donnée
            itype = "radio" if k.startswith("radio:") else "checkbox"

            # options = labels des inputs
            options: List[str] = []
            for e in els:
                lbl = _find_associated_label(driver, e)
                if lbl:
                    options.append(lbl)
            # dédoublonnage conservant l'ordre
            options = list(dict.fromkeys([o for o in options if o]))

            # question = depuis conteneur (et on exclut options)
            container = _nearest_question_container(els[0])
            question = _extract_question_from_container(container, options) if container else ""

            # ✅ Fallback DOM: question parfois hors container (ex: <h2 id="label"> au-dessus du <form>)
            if not question:
                # ✅ Fallback direct: cas très fréquent (Cint/QPS) -> <h2 id="label"> contient la question
                try:
                    if not question:
                        el_label = driver.find_elements(By.CSS_SELECTOR, "#label")
                        if el_label:
                            t = _norm(el_label[0].text)
                            if t:
                                question = t
                except Exception:
                    pass

                near = _norm(_find_question_text_near_element(driver, els[0]))
                if near:
                    near_lc = _norm_lc(near)
                    opt_lc = {_norm_lc(o) for o in (options or []) if o}
                    # filtre anti "Question 1 de 3" / textes d’aide génériques
                    # IMPORTANT: ne pas rejeter une vraie question longue qui contient juste
                    # "Veuillez sélectionner une réponse." (cas Walr, etc.)
                    is_meta = bool(re.match(r"^question\s*\d+", near_lc))
                    if not is_meta:
                        # Ne considérer "veuillez sélectionner..." comme meta QUE si c'est court (= bannière/erreur)
                        if (len(near_lc) < 140) and ("veuillez" in near_lc) and (("sélection" in near_lc) or ("selection" in near_lc)):
                            is_meta = True
                    if (near_lc not in opt_lc) and (not is_meta):
                        question = near

            if not question:
                # dernier recours: bloc "1 option" (rare, mais utile)
                if len(options) == 1 and len(els) == 1:
                    question = options[0]
                else:
                    continue

            # ✅ NEW: si on a une seule checkbox et aucune option détectée, on force option=question
            if not options and len(els) == 1 and question:
                options = [question]

            # IMPORTANT: pour les matrices, plusieurs groupes (1 par colonne) partagent la même question.
            # Si le group_key est basé sur name=..., on dédoublonne par group_key (k), pas par (question, itype).
            sig = k if k.startswith(f"{itype}:name:") else (question, itype)
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)

            # --- target_id + registry pour group (radio/checkbox)
            target_id = make_target_id("group", k, question)

            # map option -> xpath de l'input correspondant
            option_xpath_map = {}
            for e in els:
                try:
                    lbl = _find_associated_label(driver, e)

                    # ✅ NEW: single checkbox => si label vide, on mappe sur la question
                    if not lbl and len(els) == 1 and question:
                        lbl = question

                    if not lbl:
                        continue

                    # ✅ Locator STABLE pour le choix (évite XPath absolu fragile)
                    inp_id = ""
                    inp_type = ""
                    inp_name = ""
                    inp_value = ""
                    try:
                        inp_id = (e.get_attribute("id") or "").strip()
                        inp_type = (e.get_attribute("type") or "").strip().lower()
                        inp_name = (e.get_attribute("name") or "").strip()
                        inp_value = (e.get_attribute("value") or "").strip()
                    except Exception:
                        pass

                    xp = ""

                    # 1) Le plus stable : label[for="<id>"] (ou fallback input#id)
                    # IMPORTANT: sur FocusVision/Decipher-like, l'input radio peut être masqué (fir-hidden)
                    # et le click doit viser le <label for="...">.
                    if inp_id:
                        id_lit = _xpath_literal(inp_id)

                        # FocusVision/Decipher grid: le <label for=...> peut être 0x0 (template caché).
                        # On préfère cliquer la cellule <td> qui contient l'input.
                        in_grid = False
                        try:
                            in_grid = bool(e.find_elements(By.XPATH, "ancestor::table[contains(@class,'grid')][1]"))
                        except Exception:
                            in_grid = False

                        if in_grid:
                            xp = (
                                f"(//*[@id={id_lit}]/ancestor::td[contains(@class,'clickableCell')][1] | "
                                f"//*[@id={id_lit}]/ancestor::td[1] | "
                                f"//label[@for={id_lit}] | "
                                f"//*[@id={id_lit}])[1]"
                            )
                        else:
                            try:
                                has_label = bool(driver.find_elements(By.XPATH, f"//label[@for={id_lit}]"))
                            except Exception:
                                has_label = False

                            if has_label:
                                xp = f"(//label[@for={id_lit}])[1]"
                            else:
                                xp = f"(//*[@id={id_lit}])[1]"

                    # 2) Fallback stable : input par (type,name,value) si pas d'id
                    elif inp_type in ("radio", "checkbox") and inp_name and inp_value:
                        t_lit = _xpath_literal(inp_type)
                        n_lit = _xpath_literal(inp_name)
                        v_lit = _xpath_literal(inp_value)
                        xp = f"(//input[@type={t_lit} and @name={n_lit} and @value={v_lit}]/ancestor::label[1] | //input[@type={t_lit} and @name={n_lit} and @value={v_lit}])[1]"

                    # 3) Dernier recours : XPath absolu
                    else:
                        click_el = e
                        try:
                            lab = e.find_element(By.XPATH, "ancestor::label[1]")
                            if lab:
                                click_el = lab
                        except Exception:
                            pass
                        xp = _best_xpath_for_element(driver, click_el)

                    if not xp:
                        continue

                    option_xpath_map[_norm_key(lbl)] = xp
                except Exception:
                    continue

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": itype,
                    "group_key": k,
                    "question": question,
                    "option_xpath_map": option_xpath_map,  # {norm(label)->xpath}
                    "frame_chain": frame_chain,
                },
            )

            block = {
                "question": question,
                "itype": itype,
                "options": options,
                "max_select": _compute_max_select(itype, options),
                "target_id": target_id,
                "context": {
                    "kind": "group",
                    "group_key": k,
                },
            }

            question_blocks.append(block)
        except Exception:
            continue

    # --- 1b) Groupes de choix "stylés en boutons" (Decipher/Confirmit, etc.) ---
    # Objectif: quand les options ne sont PAS des <input type=radio> visibles,
    # mais une liste de <li>/<button> cliquables (ex: Decipher cardrating)

    def _is_nav_like_choice(text: str) -> bool:
        v = _norm_lc(text)
        if not v:
            return False
        nav_tokens = [
            "continue", "continuer", "next", "suivant",
            "back", "retour", "previous", "précédent", "precedent",
            "ok", "submit", "valider", "envoyer", "send",
            "start", "commencer", "finish", "terminer",
            "close", "fermer", "cancel", "annuler",
            "refuser", "decline",
        ]
        return any(tok in v for tok in nav_tokens)

    def _stable_xpath_for_buttonish(el) -> str:
        """
        Locator stable prioritaire pour Decipher:
        - data-uid est très souvent unique et stable sur la page.
        - sinon data-label + data-index
        - sinon id
        - sinon XPath absolu.
        """
        try:
            uid = (el.get_attribute("data-uid") or "").strip()
            if uid:
                return f"//*[@data-uid={_xpath_literal(uid)}]"

            dlabel = (el.get_attribute("data-label") or "").strip()
            dindex = (el.get_attribute("data-index") or "").strip()
            if dlabel and dindex:
                return f"(//*[@data-label={_xpath_literal(dlabel)} and @data-index={_xpath_literal(dindex)}])[1]"
        except Exception:
            pass

        return _best_xpath_for_element(driver, el)

    try:
        btn_like = driver.find_elements(
            By.CSS_SELECTOR,
            "button, a[role='button'], [role='button'], .sq-cardrating-button"
        )
    except Exception:
        btn_like = []

    btn_groups: Dict[str, Dict[str, Any]] = {}
    for b in btn_like:
        try:
            if not _is_actionable_visible(b):
                continue

            # Filtre Decipher cardrating : ignore disabled / non-clickable
            cls = _norm_lc(b.get_attribute("class") or "")
            if "sq-cardrating-button" in cls:
                if _norm_lc(b.get_attribute("data-clickable") or "") in ("false", "0"):
                    continue
                if _norm_lc(b.get_attribute("data-disabled") or "") in ("true", "1"):
                    continue

            # Texte (pour cardrating, le texte est dans le <li>)
            t = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
            if (not t or len(t) < 2) and "sq-cardrating-button" in cls:
                # backup: certains thèmes remplissent le texte dans .sq-cardrating-content
                try:
                    t = _norm(b.find_element(By.CSS_SELECTOR, ".sq-cardrating-content").text)
                except Exception:
                    pass

            if not t or len(t) < 2:
                continue
            if _is_nav_like_choice(t):
                continue

            cont = _nearest_question_container(b)
            if not cont:
                try:
                    cont = b.find_element(By.XPATH, "ancestor::*[self::div or self::section or self::form][1]")
                except Exception:
                    cont = None
            if not cont:
                continue

            cid = (cont.get_attribute("id") or "").strip()
            ccl = _norm_lc(cont.get_attribute("class") or "")
            gk = f"btn_group:{cid}:{ccl}:{id(cont)}"
            g = btn_groups.setdefault(gk, {"container": cont, "buttons": []})
            g["buttons"].append(b)
        except Exception:
            continue

    for _gk, g in (btn_groups or {}).items():
        try:
            cont = g.get("container")
            btns = g.get("buttons") or []
            if len(btns) < 3:
                continue

            # options = textes des boutons (dédoublonnés)
            options: List[str] = []
            for b in btns:
                tt = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
                if not tt or _is_nav_like_choice(tt):
                    continue
                if tt not in options:
                    options.append(tt)

            if len(options) < 3:
                continue

            question = ""
            if cont:
                question = _extract_question_from_container(cont, options=options) or ""

            if not question:
                question = _norm(_find_question_text_near_element(driver, btns[0]))

            # évite de prendre les bannières d’erreur comme "question"
            qlc = _norm_lc(question)
            if qlc and ("un problème est survenu" in qlc or ((len(qlc) < 140) and ("veuillez" in qlc) and (("sélection" in qlc) or ("selection" in qlc)))):
                # on tente un near-text sur un autre bouton (souvent plus bas = plus proche du vrai libellé)
                for cand in btns[1:3]:
                    near2 = _norm(_find_question_text_near_element(driver, cand))
                    near2_lc = _norm_lc(near2)
                    if near2 and ("un problème est survenu" not in near2_lc) and not ("veuillez" in near2_lc and "sélection" in near2_lc):
                        question = near2
                        break

            question = _norm(question)
            if not question:
                continue

            sig = (question, "radio")
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)

            # group_key stable-ish: id/class du conteneur + quelques options
            cid = (cont.get_attribute("id") or "").strip() if cont else ""
            ccl = _norm_lc(cont.get_attribute("class") or "") if cont else ""
            opt_sig = "|".join(_norm_key(o) for o in (options[:5] or []))
            group_key = f"radio:button_group:{cid}:{ccl}:{opt_sig}"

            target_id = make_target_id("group", group_key, question)

            option_xpath_map = {}
            for b in btns:
                lbl = _norm(b.text or b.get_attribute("innerText") or b.get_attribute("value") or "")
                if not lbl or _is_nav_like_choice(lbl):
                    continue
                xp = _best_xpath_for_element(driver, b)
                if xp:
                    option_xpath_map[_norm_key(lbl)] = xp

            if not option_xpath_map:
                continue

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                },
            )

            question_blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )
        except Exception:
            continue

    # --- 2) Autres inputs (dropdown / text / textarea / button) ---
    try:
        other_inputs = driver.find_elements(
            By.CSS_SELECTOR,
            "input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea, select, button, a[role='button']",
        )
    except Exception:
        other_inputs = []

    for el in other_inputs:
        try:
            itype = _detect_itype(el)

            # 1) On ignore les champs techniques/hidden
            if itype == "hidden" or _looks_like_system_field(el):
                continue

            # 2) On ignore les éléments non actionnables/visibles
            if not _is_actionable_visible(el):
                continue

            if itype in ("radio", "checkbox", "unknown"):
                continue

            # on ne veut pas transformer un "bouton next" en question
            if itype == "button":
                # 1) filtres structurels (navigation)
                bid = (el.get_attribute("id") or "").strip().lower()
                bname = (el.get_attribute("name") or "").strip().lower()

                if bid in {"next_button", "back_button", "skip_button"}:
                    continue
                if bname in {"next", "back"}:
                    continue

                try:
                    # conteneurs nav typiques (YouGov & autres)
                    if el.find_elements(
                        By.XPATH,
                        "ancestor::*[@role='navigation' or @id='mainNav' or contains(@class,'nav-buttons')][1]"
                    ):
                        continue
                except Exception:
                    pass

                # 2) filtre textuel (plus large)
                txt = _norm(el.text or el.get_attribute("innerText") or "")
                tlc = _norm_lc(txt)
                if tlc in {
                    "next", "suivant", "continue", "continuer",
                    "next page", "previous page",
                    "page suivante", "page précédente",
                }:
                    continue

            container = _nearest_question_container(el) or el
            question = _extract_question_from_container(container, options=[]) or _find_associated_label(driver, el)
            # question = _norm(question)
            question = ""
            if container:
                question = _extract_question_from_container(container, options=[]) or ""

            if not question:
                # important pour YouGov-like: question visible au-dessus mais pas bien "liée" au input
                question = _find_question_text_near_element(driver, el) or ""

            if not question:
                question = _find_associated_label(driver, el) or ""
            question = _norm(question)

            if not question:
                continue

            sig = (question, itype)
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)

            options: List[str] = []
            if itype == "dropdown":
                try:
                    for o in el.find_elements(By.TAG_NAME, "option"):
                        if o.get_attribute("disabled"):
                            continue
                        t = _norm(o.text or o.get_attribute("innerText") or "")
                        if t:
                            options.append(t)
                    options = list(dict.fromkeys(options))
                except Exception:
                    pass

            # --- target_id + registry pour single input
            single_key = f"{itype}:{(el.get_attribute('id') or '').strip()}:{(el.get_attribute('name') or '').strip()}"
            target_id = make_target_id("single", single_key, question)

            xpath = _best_xpath_for_element(driver, el)
            register_target(
                target_id,
                {
                    "kind": "single",
                    "itype": itype,
                    "question": question,
                    "xpath": xpath,
                    "frame_chain": frame_chain,
                },
            )

            block = {
                "question": question,
                "itype": itype,
                "options": options,
                "max_select": _compute_max_select(itype, options),
                "target_id": target_id,
                "context": {
                    "kind": "single",
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

def analyze_dom(driver) -> List[Dict[str, Any]]:
    """
    Analyse le DOM et retourne une liste de QuestionBlock.
    Frame-aware: choisit automatiquement le meilleur contexte (default ou iframe) jusqu'à depth=DOM_FRAME_MAX_DEPTH (défaut=2).
    """
    dom_registry.clear_registry()

    _wait_for_survey_dom(driver)
    max_depth = int(os.getenv("DOM_FRAME_MAX_DEPTH", "2") or "2")
    best_chain, _meta = _select_best_frame_chain(driver, max_depth=max_depth)

    # Scan dans le contexte choisi; retour à default_content garanti.
    blocks: List[Dict[str, Any]] = []
    chain: List[Any] = []
    with switch_to_frame_chain(driver, best_chain) as ok:
        chain = best_chain if ok else []

        # --- FocusVision/Decipher sliderpoints (matrix dropdowns) ---
        sp_blocks = extract_sliderpoints_question_blocks(driver)
        if sp_blocks:
            return sp_blocks

        blocks = _analyze_dom_current_context(driver, frame_chain=chain)
        blocks.extend(_extract_focusvision_answers_list_groups(driver, frame_chain=chain))

    # Fallback strict: si on a scanné un iframe et qu'on n'a rien, tente default_content une seule fois.
    if not blocks and chain:
        with switch_to_frame_chain(driver, []) as ok:
            if ok:
                sp_blocks = extract_sliderpoints_question_blocks(driver)
                if sp_blocks:
                    return sp_blocks
                blocks = _analyze_dom_current_context(driver)
                blocks.extend(_extract_focusvision_answers_list_groups(driver))

    return blocks
