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
import re
import unicodedata
import os
from Survey.dom_registry import clear_registry, register_target, make_target_id
from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain
from selenium.webdriver.common.by import By

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
    """
    Filtre 'cheap' anti-faux-positifs: affiché + dimensions non nulles.

    NEW: certains moteurs (Decipher/Confirmit) masquent l'input (radio/checkbox)
    mais rendent cliquable un ancêtre visible (td.clickableCell / li.sq-cardrating-button).
    Dans ce cas, on considère l'input comme actionable pour permettre l'extraction DOM.
    """
    try:
        if el.is_displayed():
            r = getattr(el, "rect", None) or {}
            return (r.get("width", 0) or 0) > 2 and (r.get("height", 0) or 0) > 2

        # --- NEW: input masqué mais conteneur cliquable visible ---
        tag = (el.tag_name or "").lower()
        if tag == "input":
            t = (el.get_attribute("type") or "").strip().lower()
            if t in ("radio", "checkbox"):
                try:
                    anc = el.find_element(
                        By.XPATH,
                        "ancestor::*[contains(@class,'clickableCell') or contains(@class,'sq-cardrating-button')][1]"
                    )
                    if anc and anc.is_displayed():
                        r = getattr(anc, "rect", None) or {}
                        return (r.get("width", 0) or 0) > 2 and (r.get("height", 0) or 0) > 2
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
        if _is_question_text(tl):
            sc += 3
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

def _score_dom_context(driver) -> Dict[str, Any]:
    """Score cheap d'un contexte DOM (default ou iframe)."""
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

            // Signaux "survey question" (plus discriminants que juste inputsCount)
            const qNodes = document.querySelectorAll(
              "input[name^='question_'], textarea[name^='question_'], select[name^='question_'], " +
              ".js-question-options input, .js-question-options select, .js-question-options textarea"
            );
            const qCount = qNodes ? qNodes.length : 0;

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

            const hasSurveyRoot = !!document.querySelector(".js-question-options, #templates .question, .survey-content #templates");

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

    # Score: signaux question >> visible inputs >> texte. Bonus vocabulaire + root.
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

def _analyze_dom_current_context(driver, frame_chain=None) -> List[Dict[str, Any]]:
    """
    Analyse le DOM courant et retourne une liste de QuestionBlock.
    IMPORTANT: 1 bloc par question (group radio/checkbox).
    """
    
    frame_chain = frame_chain or []
    question_blocks: List[Dict[str, Any]] = []
    clear_registry()

    # --- 1) Radios / checkboxes groupés ---
    try:
        choice_els = driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox']"
        )
    except Exception:
        choice_els = []

    groups: Dict[str, List[Any]] = {}
    for el in choice_els:
        try:
            itype = _detect_itype(el)
            if itype not in ("radio", "checkbox"):
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
                    is_meta = bool(re.match(r"^question\s*\d+", near_lc)) or ("veuillez" in near_lc and "sélection" in near_lc)
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

            sig = (question, itype)
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
                    if inp_id:
                        id_lit = _xpath_literal(inp_id)
                        xp = f"(//label[@for={id_lit}] | //*[@id={id_lit}])[1]"

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
            if qlc and ("un problème est survenu" in qlc or ("veuillez" in qlc and "sélection" in qlc)):
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
                txt = _norm(el.text or el.get_attribute("innerText") or "")
                if _norm_lc(txt) in {"next", "suivant", "continue", "continuer"}:
                    continue

            container = _nearest_question_container(el) or el
            question = _extract_question_from_container(container, options=[]) or _find_associated_label(driver, el)
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
    max_depth = int(os.getenv("DOM_FRAME_MAX_DEPTH", "2") or "2")
    best_chain, _meta = _select_best_frame_chain(driver, max_depth=max_depth)

    # Scan dans le contexte choisi; retour à default_content garanti.
    with switch_to_frame_chain(driver, best_chain) as ok:
        chain = best_chain if ok else []
        blocks = _analyze_dom_current_context(driver, frame_chain=chain)

    # Fallback strict: si on a scanné un iframe et qu'on n'a rien, tente default_content une seule fois.
    if not blocks and chain:
        blocks = _analyze_dom_current_context(driver, frame_chain=[])

    return blocks
