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
from Survey.dom_registry import clear_registry, register_target, make_target_id

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
    """
    try:
        if not el.is_displayed():
            return False
        r = getattr(el, "rect", None) or {}
        return (r.get("width", 0) or 0) > 2 and (r.get("height", 0) or 0) > 2
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
# API principale
# =========================

def analyze_dom(driver) -> List[Dict[str, Any]]:
    """
    Analyse le DOM courant et retourne une liste de QuestionBlock.
    IMPORTANT: 1 bloc par question (group radio/checkbox).
    """
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
            question = _extract_question_from_container(container, options) if container is not None else ""

            # fallback si on n'a pas trouvé: on évite de créer un "bloc option"
            if not question:
                # si la question est introuvable, on préfère ne pas envoyer ce groupe à OpenAI
                # (sinon on recrée le problème initial: 1 bloc par option).
                continue

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
                    if not lbl:
                        continue
                    xp = _best_xpath_for_element(driver, e)
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
