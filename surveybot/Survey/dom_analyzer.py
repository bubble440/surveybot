# Survey/dom_analyzer.py
"""
DOM Analyzer Ã¢â‚¬â€ extraction TEXT-ONLY des questions de survey.

Objectif:
- Scanner le DOM
- Identifier chaque question (1 bloc par question, PAS 1 bloc par option radio)
- DÃƒÂ©terminer le type d'input attendu
- Extraire les options associÃƒÂ©es
- Ajouter une contrainte de cardinalitÃƒÂ© (max_select)

Aucune dÃƒÂ©pendance image.
Compatible local / prod.
PensÃƒÂ© pour 100+ bots.
"""

from __future__ import annotations
from typing import List, Dict, Any, Tuple
import re, time, zlib, os
import unicodedata
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

# --- Champs techniques/ASP.NET ÃƒÂ  ignorer (anti-pollution prompt) ---
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
    DÃƒÂ©tecte les inputs techniques (ASP.NET / hidden / tracking) pour ne pas les traiter comme questions.
    IMPORTANT: ÃƒÂ§a ÃƒÂ©vite d'envoyer du bruit ÃƒÂ  OpenAI -> moins cher + plus fiable.
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
    """Retourne True si l'ÃƒÂ©lÃƒÂ©ment est rÃƒÂ©ellement actionnable cÃƒÂ´tÃƒÂ© UI.

    Fix principal:
    - Exclure les inputs utilitaires/masquÃƒÂ©s LimeSurvey (ls-js-hidden) qui polluent l'extraction
      (ex: confirm-clearall), sinon OpenAI renvoie des actions impossibles ÃƒÂ  appliquer.

    Compat:
    - Inputs masquÃƒÂ©s mais cliquables via wrapper visible (Decipher/FocusVision: clickableCell / sq-cardrating-button).
    - Inputs masquÃƒÂ©s mais label visible (custom UI).
    """
    try:
        # 0) LimeSurvey: ignorer tout ce qui est dans un bloc masquÃƒÂ© "ls-js-hidden"
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
        # 1bis) <select> masquÃƒÂ© mais contrÃƒÂ´lÃƒÂ© par un proxy visible (ex: bootstrap-select / selectpicker)
        # Exemple: <select class="selectpicker bs-select-hidden"> + bouton .dropdown-toggle visible.
        if tag == "select":
            try:
                cls = (el.get_attribute("class") or "").lower()
                if ("bs-select-hidden" in cls) or ("selectpicker" in cls):
                    proxy = None

                    # Ã¢Å“â€¦ Ipsos: le proxy bootstrap-select est souvent un SIBLING (pas un ancÃƒÂªtre)
                    for xp in (
                        "ancestor::*[contains(concat(' ',normalize-space(@class),' '),' bootstrap-select ')][1]",
                        "following-sibling::*[contains(concat(' ',normalize-space(@class),' '),' bootstrap-select ')][1]",
                        "preceding-sibling::*[contains(concat(' ',normalize-space(@class),' '),' bootstrap-select ')][1]",
                    ):
                        try:
                            proxy = el.find_element(By.XPATH, xp)
                            if proxy:
                                break
                        except Exception:
                            proxy = None

                    if proxy:
                        # si le bouton proxy est visible, on considÃƒÂ¨re le select comme actionnable
                        btn = None
                        try:
                            btn = proxy.find_element(
                                By.CSS_SELECTOR,
                                "button.dropdown-toggle, button[data-toggle='dropdown']",
                            )
                        except Exception:
                            btn = None

                        if btn:
                            try:
                                if btn.is_displayed() and _rect_ok(btn):
                                    return True
                            except Exception:
                                pass

                        try:
                            if proxy.is_displayed() and _rect_ok(proxy):
                                return True
                        except Exception:
                            pass
            except Exception:
                pass

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

                # 3) Wrapper visible (Cint/QPS): <div class="answer ..."> contient input masquÃƒÂ© + label/span visible
                try:
                    anc = el.find_element(
                        By.XPATH,
                        "ancestor::*[contains(concat(' ',normalize-space(@class),' '),' answer ')][1]"
                    )
                    if anc and anc.is_displayed() and _rect_ok(anc):
                        return True
                except Exception:
                    pass

                # 4) Label visible (custom UI) Ã¢â‚¬â€ version robuste sans dÃƒÂ©pendre de el._parent
                try:
                    el_id = (el.get_attribute("id") or "").strip()
                    if el_id:
                        # a) labels proches (souvent un label "proxy" vide est sibling du input)
                        labs = []
                        try:
                            labs.extend(el.find_elements(
                                By.XPATH,
                                f"following-sibling::label[@for='{el_id}'] | preceding-sibling::label[@for='{el_id}']"
                            ))
                        except Exception:
                            labs = labs or []

                        # b) labels dans le conteneur immÃƒÂ©diat (ex: label texte dans une colonne voisine)
                        try:
                            labs.extend(el.find_elements(By.XPATH, f"ancestor::*[1]//label[@for='{el_id}']"))
                        except Exception:
                            pass

                        # c) fallback Ã¢â‚¬Å“documentÃ¢â‚¬Â via racine <html> (iframe-safe) Ã¢â‚¬â€ toujours en plus,
                        # car certaines UIs (ex: CMIX/Materialize) mettent un label sibling vide (pictogramme)
                        # et le vrai label (texte) ailleurs dans la ligne.
                        try:
                            root = el.find_element(By.XPATH, "ancestor-or-self::html[1]")
                            labs.extend(root.find_elements(By.XPATH, f".//label[@for='{el_id}']"))
                        except Exception:
                            pass

                        for lab in (labs or [])[:20]:
                            try:
                                if not lab.is_displayed():
                                    continue

                                # Ã¢Å“â€¦ CMIX/Materialize: le label peut avoir une bbox 0x0 (display:contents/pseudo-element),
                                # mais contenir du texte/descendants visibles.
                                if _rect_ok(lab):
                                    return True

                                t = _norm(lab.text or lab.get_attribute("innerText") or "")
                                if t:
                                    return True

                                # Dernier recours: un enfant visible avec bbox non nulle
                                try:
                                    kids = lab.find_elements(By.XPATH, ".//*")
                                except Exception:
                                    kids = []
                                for kid in (kids or [])[:8]:
                                    try:
                                        if kid.is_displayed() and _rect_ok(kid):
                                            return True
                                    except Exception:
                                        continue
                            except Exception:
                                continue
                except Exception:
                    pass

                # 4) Label ancÃƒÂªtre visible (input inside <label> ...)
                try:
                    lab = el.find_element(By.XPATH, "ancestor::label[1]")
                    if lab and lab.is_displayed():
                        if _rect_ok(lab):
                            return True
                        t = _norm(lab.text or lab.get_attribute("innerText") or "")
                        if t:
                            return True
                        try:
                            kids = lab.find_elements(By.XPATH, ".//*")
                        except Exception:
                            kids = []
                        for kid in (kids or [])[:8]:
                            try:
                                if kid.is_displayed() and _rect_ok(kid):
                                    return True
                            except Exception:
                                continue
                except Exception:
                    pass

        return False
    except Exception:
        return False

def _best_xpath_for_element(driver, el) -> str:
    """
    XPath robuste:
    - si id => //*[@id='...']
    - sinon XPath absolu gÃƒÂ©nÃƒÂ©rÃƒÂ© via JS (stable sur la page courante)
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
    Literal XPath safe, mÃƒÂªme si la chaÃƒÂ®ne contient des quotes.
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
        "oÃƒÂ¹", "ou", "comment", "pourquoi",
        "ÃƒÂ¢ge", "age", "gender", "education", "niveau",
    ]
    return any(k in low for k in keywords)

# =========================
# DÃƒÂ©tection du type d'input
# =========================

def _detect_itype(el) -> str:
    tag = (el.tag_name or "").lower()

    if tag == "input":
        t = (el.get_attribute("type") or "").strip().lower()

        # Ã¢Å¡Â Ã¯Â¸Â Hidden = jamais une question
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

        # DÃƒÂ©faut: texte (mais on filtrera via visibilitÃƒÂ© + system fields)
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

def _dropdown_field_hint(driver, el) -> str:
    """Sous-label pour distinguer plusieurs dropdowns dans la mÃƒÂªme question.

    Cas typique: "Quelle est votre date de naissance ?" avec 2 selects (Mois / AnnÃƒÂ©e),
    souvent rendus via bootstrap-select (select masquÃƒÂ© + bouton visible).
    """
    try:
        # 1) name/id
        nid = f"{(el.get_attribute('name') or '')} {(el.get_attribute('id') or '')}".lower()
        if any(k in nid for k in ("month", "mois")):
            return "Mois"
        if any(k in nid for k in ("year", "annee", "annÃƒÂ©e")):
            return "AnnÃƒÂ©e"

        # 2) classes ancÃƒÂªtres (monthPicker/yearPicker, etc.)
        try:
            anc = el.find_element(
                By.XPATH,
                "ancestor-or-self::*[contains(@class,'month') or contains(@class,'mois') or contains(@class,'year') or contains(@class,'annee')][1]"
            )
            cls = (anc.get_attribute("class") or "").lower()
            if ("month" in cls) or ("mois" in cls):
                return "Mois"
            if ("year" in cls) or ("annee" in cls) or ("annÃƒÂ©e" in cls):
                return "AnnÃƒÂ©e"
        except Exception:
            pass

        # 3) bootstrap-select proxy button text
        try:
            anc = el.find_element(
                By.XPATH,
                "ancestor::*[contains(concat(' ',normalize-space(@class),' '),' bootstrap-select ')][1]"
            )
            btns = anc.find_elements(By.CSS_SELECTOR, "button.dropdown-toggle, button[data-toggle='dropdown']")
            for b in (btns or [])[:2]:
                t = _norm(b.text or b.get_attribute("innerText") or "")
                tl = (t or "").lower()
                if tl in ("mois", "month"):
                    return "Mois"
                if tl in ("annÃƒÂ©e", "annee", "year"):
                    return "AnnÃƒÂ©e"
        except Exception:
            pass

        # 4) first option (placeholder)
        try:
            opts = el.find_elements(By.TAG_NAME, "option")
            for o in (opts or [])[:3]:
                if o.get_attribute("disabled"):
                    continue
                t = _norm(o.text or o.get_attribute("innerText") or "")
                tl = (t or "").lower()
                if tl in ("mois", "month"):
                    return "Mois"
                if tl in ("annÃƒÂ©e", "annee", "year"):
                    return "AnnÃƒÂ©e"
        except Exception:
            pass

        return ""
    except Exception:
        return ""

# =========================
# Extraction labels/options
# =========================

def _find_question_text_near_element(driver, el) -> str:
    """
    Cherche un texte "question" visuellement proche (au-dessus) de l'ÃƒÂ©lÃƒÂ©ment input/textarea.
    Objectif: ÃƒÂ©viter les fallbacks vision quand la question est bien dans le DOM
    mais pas dans le mÃƒÂªme conteneur HTML (Angular/React trÃƒÂ¨s frÃƒÂ©quent).
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

              // On veut un bloc au-dessus (ou trÃƒÂ¨s lÃƒÂ©gÃƒÂ¨rement overlap) et proche verticalement
              const gap = r.top - rr.bottom;
              if (gap < -10 || gap > 320) continue;

              // Overlap horizontal minimum (ÃƒÂ©vite de prendre le header de la page)
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
    RÃ©cupÃ¨re le libellÃ© associÃ© Ã  un input (souvent l'OPTION pour radio/checkbox).

    Note Qualtrics: Il peut y avoir plusieurs <label for="id">:
    - Un label vide (aria-hidden) pour l'icÃ´ne visuelle
    - Un label avec le vrai texte dans span.LabelWrapper
    On itÃ¨re donc sur tous les labels et on prend le premier avec du texte.

    """
    try:
        el_id = el.get_attribute("id")
        if el_id:
            # Qualtrics/autres: plusieurs labels possibles, prendre celui avec du texte
            # (le premier label peut Ãªtre vide/aria-hidden, juste pour l'icÃ´ne)
            labels = driver.find_elements(By.XPATH, f"//label[@for='{el_id}']")
            for lbl in labels:
                try:
                    # Ignorer les labels aria-hidden (souvent vides)
                    if (lbl.get_attribute("aria-hidden") or "").lower() == "true":
                        continue
                    txt = _norm(lbl.text or lbl.get_attribute("innerText") or "")
                    if txt and len(txt) >= 2:
                        return txt
                except Exception:
                    continue
    except Exception:
        pass

    try:
        parent_label = el.find_element(By.XPATH, "ancestor::label")
        if parent_label:
            return _norm(parent_label.text or parent_label.get_attribute("innerText") or "")
    except Exception:
        pass

    # Ã¢Å“â€¦ NEW: pattern trÃƒÂ¨s frÃƒÂ©quent (Angular/React) : input + label(vide) + span/div texte
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

    # fallback lÃƒÂ©ger: aria-label / name
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
    StratÃƒÂ©gie:
    - prioriser legend/h1/h2/h3/h4/label "question-like"
    - fallback: lignes de texte du conteneur
    - exclure toute ligne qui est une option connue
    """
    opt_lc = {_norm_lc(o) for o in (options or []) if o}

    candidates: List[str] = []

    # 1) titres/entÃƒÂªtes
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

        # Ã¢Å“â€¦ Bonus "consigne explicite" (souvent le vrai libellÃƒÂ© dans les control questions)
        directive_tokens = (
            "veuillez", "merci de", "please",
            "select", "choose",
            "choisir", "choisissez",
            "sÃƒÂ©lectionnez", "selectionnez",
            "cochez", "cliquez",
            "indiquez", "entrez", "saisissez",
        )
        if any(tok in tlc for tok in directive_tokens):
            sc += 4

        # lÃƒÂ©ger malus pour les phrases d'intro (souvent au-dessus de la vraie consigne)
        boilerplate_tokens = (
            "la qualitÃƒÂ© de vos rÃƒÂ©ponses",
            "standards de qualitÃƒÂ©",
            "votre avis est important",
            "merci pour votre participation",
            "nous vous remercions",
        )
        if any(tok in tlc for tok in boilerplate_tokens):
            sc -= 2

        # bonus si ÃƒÂ§a ressemble ÃƒÂ  une vraie question et pas ÃƒÂ  un label court
        sc += min(len(tl), 120) // 20
        if "?" in tl:
            sc += 2

        if sc > best_sc:
            best_sc = sc
            best = tl

    return best

# Regex pour dÃ©tecter les noms indexÃ©s:
# - Decipher: ans9501.0.16 â†’ prÃ©fixe ans9501.0 (sÃ©parateur .)
# - Qualtrics: QR~QID29~6 â†’ prÃ©fixe QR~QID29 (sÃ©parateur ~)
# Permet de regrouper checkboxes avec noms uniques mais prÃ©fixe commun.
_INDEXED_NAME_PATTERN = re.compile(r'^(.+)[.~]\d+$')

def _group_key_for_choice(el, itype: str) -> str:
    """
    CrÃƒÂ©e une clÃƒÂ© de groupe stable-ish pour radio/checkbox:
    - name si prÃƒÂ©sent (meilleur)
    - Pour checkboxes avec noms indexÃƒÂ©s (pattern prefix.N), regroupe par prÃƒÂ©fixe + conteneur
    - aria-labelledby sinon
    - sinon conteneur question
    """
    try:
        name = (el.get_attribute("name") or "").strip()
        if name:
            # DÃƒÂ©tecter le pattern indexÃƒÂ© pour les checkboxes (Decipher/FocusVision)
            # Ex: "ans9501.0.16" Ã¢â€ â€™ prÃƒÂ©fixe "ans9501.0" Ã¢â€ â€™ regrouper avec "ans9501.0.17", etc.
            if itype == "checkbox":
                m = _INDEXED_NAME_PATTERN.match(name)
                if m:
                    prefix = m.group(1)
                    # Combine avec container_id pour ÃƒÂ©viter de fusionner des questions diffÃƒÂ©rentes
                    c = _nearest_question_container(el)
                    cid = ""
                    try:
                        cid = (c.get_attribute("id") or "").strip() if c is not None else ""
                    except Exception:
                        pass
                    if cid:
                        return f"{itype}:indexed:{prefix}:{cid}"
                    return f"{itype}:indexed:{prefix}"
            
            # Comportement standard: regrouper par name exact
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

    return f"{itype}:container_obj:{id(c) if c is not None else id(el)}"

def _compute_max_select(itype: str, options: List[str]) -> int:
    """
    RÃƒÂ¨gle mÃƒÂ©tier simple:
    - radio / dropdown / text / textarea / button => 1
    - checkbox => multi (cap ÃƒÂ  3 par dÃƒÂ©faut)
    """
    if itype == "checkbox":
        n = len(options or [])
        if n <= 1:
            return 1
        return min(3, n)
    return 1

# =========================
# SÃƒÂ©lection de contexte (iframe-aware)
# =========================

def _env_truthy(name: str, default: str = "0") -> bool:
    v = (os.getenv(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "on")

def _wait_for_survey_dom(driver, timeout_s: float = 1.2, step_s: float = 0.2) -> bool:
    """
    Attente courte et bornÃƒÂ©e: ÃƒÂ©vite le scan DOM trop tÃƒÂ´t (page pas encore prÃƒÂªte).
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
                    // pas juste un conteneur vide rendu trop tÃƒÂ´t.
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

        # Signaux question (inclut FocusVision: inputs ans* souvent masquÃƒÂ©s)
        try:
            q_nodes = driver.find_elements(
                By.CSS_SELECTOR,
                "input[name^='question_'], textarea[name^='question_'], select[name^='question_'], "
                ".js-question-options input, .js-question-options select, .js-question-options textarea, "
                "div.question input[type='radio'], div.question input[type='checkbox'], "
                ".cm-question-wrapper input[type='radio'], .cm-question-wrapper input[type='checkbox'], "
                ".cm-question-wrapper select, .cm-question-wrapper textarea,"
                ".answers.answers-list input[type='radio'], .answers.answers-list input[type='checkbox']",
            )
        except Exception:
            q_nodes = []
        q_count = len(q_nodes)

        # Labels
        try:
            label_nodes = driver.find_elements(
                By.CSS_SELECTOR,
                ".js-question-options label, label.radio, label.checkbox, .answers.answers-list label[for],"
                ".cm-question-wrapper label.cm-radio-label[for], .cm-question-wrapper label.cm-checkbox-label[for]",
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
                    ".cm-question-wrapper, .cm-response-group, .cm-survey-layout,"
                    ".answers.answers-list, div.question",
                )
            )
        except Exception:
            has_root = False

        has_words = bool(
            re.search(
                r"question|suivant|next|continue|prochaine|ÃƒÂ©tape|sondage|enquÃƒÂªte|profil|survey",
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
            ".js-question-options input, .js-question-options select, .js-question-options textarea, " +
            ".cm-question-wrapper input[type='radio'], .cm-question-wrapper input[type='checkbox'], " +
            ".cm-question-wrapper select, .cm-question-wrapper textarea"
            );

            const labelNodes = document.querySelectorAll(
            ".js-question-options label, label.radio, label.checkbox, " +
            ".cm-question-wrapper label.cm-radio-label[for], .cm-question-wrapper label.cm-checkbox-label[for]"
            );

            const hasSurveyRoot = !!document.querySelector(
            ".js-question-options, #templates .question, .survey-content #templates, " +
            "#survey.survey-container, div[id^=\"question_\"], .sq-cardsort, " +
            ".cm-question-wrapper, .cm-response-group, .cm-survey-layout"
            );

            const low = t.toLowerCase();
            const hasSurveyWords = /question|suivant|next|continue|prochaine|ÃƒÂ©tape|sondage|enquÃƒÂªte|profil|survey/i.test(low);

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

    # Si le chemin JS ne remonte rien d'utile (ou a silencieusement ÃƒÂ©chouÃƒÂ©),
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
    Comportement dÃƒÂ©terministe, sans retries infinis.
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

# --- FocusVision: answers-list (inputs radio/checkbox masquÃƒÂ©s + wrapper clickableCell) ---

def _xpath_literal(s: str) -> str:
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"

def _extract_angular_material_radio_groups(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extraction Angular Material radio groups (mat-radio-group / mat-radio-button).
    
    Concerne: sites modernes Angular Material (ex: innovatemr, EdgeSurvey).
    Structure typique:
      <mat-radio-group name="radioOptField" ...>
        <mat-radio-button id="mat-radio-X" ...>
          <input type="radio" class="mdc-radio__native-control" value="1" ...>
          <label class="mdc-label" for="mat-radio-X-input"> 1 </label>
        </mat-radio-button>
        ...
      </mat-radio-group>
    """
    blocks: list[dict] = []
    
    try:
        radio_groups = driver.find_elements(By.CSS_SELECTOR, "mat-radio-group")
    except Exception:
        return blocks
    
    for group in radio_groups:
        try:
            # Nom du groupe (clé de regroupement)
            name = (group.get_attribute("name") or "").strip()
            if not name:
                continue
            
            # Trouver tous les mat-radio-button visibles
            buttons = group.find_elements(By.CSS_SELECTOR, "mat-radio-button")
            if len(buttons) < 2:
                continue
            
            # Extraire les options et input_ids
            options: list[str] = []
            input_ids: list[str] = []
            
            for btn in buttons:
                try:
                    # Vérifier visibilité du bouton
                    try:
                        if not btn.is_displayed():
                            continue
                        r = btn.rect or {}
                        if r.get("width", 0) <= 2 or r.get("height", 0) <= 2:
                            continue
                    except Exception:
                        continue
                    
                    # Extraire le label (plusieurs méthodes)
                    label_txt = ""
                    
                    # 1) label.mdc-label (Angular Material standard)
                    try:
                        label_el = btn.find_element(By.CSS_SELECTOR, "label.mdc-label")
                        label_txt = (label_el.text or label_el.get_attribute("innerText") or "").strip()
                    except Exception:
                        pass
                    
                    # 2) Fallback: texte complet du bouton
                    if not label_txt:
                        label_txt = (btn.text or btn.get_attribute("innerText") or "").strip()
                    
                    if not label_txt:
                        continue
                    
                    # Trouver l'input radio sous-jacent
                    try:
                        inp = btn.find_element(By.CSS_SELECTOR, "input[type='radio']")
                        inp_id = (inp.get_attribute("id") or "").strip()
                        if not inp_id:
                            continue
                        
                        options.append(label_txt)
                        input_ids.append(inp_id)
                    except Exception:
                        continue
                        
                except Exception:
                    continue
            
            # Au moins 2 options pour créer un block
            if len(options) < 2 or len(input_ids) < 2:
                continue
            
            # Extraire la question (chercher h5, h3, mat-card-title, etc.)
            question = ""
            try:
                # Conteneur parent (souvent un form ou div.survey-window)
                container = None
                try:
                    container = group.find_element(By.XPATH, "ancestor::form[1]")
                except Exception:
                    try:
                        container = group.find_element(By.XPATH, "ancestor::div[contains(@class,'survey')][1]")
                    except Exception:
                        container = group.find_element(By.XPATH, "ancestor::div[1]")
                
                if container:
                    # Chercher le texte de question (h5, h3, mat-card-title, etc.)
                    for sel in ["h5.question-text", "h3.question-text", "h5", "h3", "mat-card-title", "div.question-text"]:
                        try:
                            q_el = container.find_element(By.CSS_SELECTOR, sel)
                            question = (q_el.text or q_el.get_attribute("innerText") or "").strip()
                            if question:
                                break
                        except Exception:
                            continue
            except Exception:
                pass
            
            question = _norm(question)
            if not question:
                # Fallback: utiliser le name comme question
                question = f"Question {name}"
            
            # Construire option_xpath_map (on clique sur le mat-radio-button ou label)
            option_xpath_map: dict[str, str] = {}
            clean_options: list[str] = []
            
            for opt_txt, inp_id in zip(options, input_ids):
                if not opt_txt or not inp_id:
                    continue
                k = _norm_lc(opt_txt)
                if not k or k in option_xpath_map:
                    continue
                
                # XPath: cliquer sur le label visible (pas l'input masqué)
                # Stratégie: mat-radio-button contenant cet input
                xpath_click = (
                    f"//input[@id={_xpath_literal(inp_id)}]"
                    f"/ancestor::mat-radio-button[1]"
                )
                option_xpath_map[k] = xpath_click
                clean_options.append(opt_txt)
            
            if len(clean_options) < 2:
                continue
            
            # Déterminer itype (toujours radio pour mat-radio-group)
            itype = "radio"
            
            # Créer target_id et enregistrer
            group_key = f"{itype}:name:{name}"
            target_id = make_target_id("group", group_key, question)
            
            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": itype,
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": list(frame_chain or []),
                },
            )
            
            blocks.append(
                {
                    "question": question,
                    "itype": itype,
                    "options": clean_options,
                    "max_select": _compute_max_select(itype, clean_options),
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )
            
        except Exception as e:
            if os.getenv("RUN_ENV", "local") == "local":
                print(f"[DOM_ANALYZER][WARN] angular_material extract: {type(e).__name__}: {e}")
            continue
    
    return blocks

def _extract_focusvision_answers_list_groups(driver, frame_chain: list[int] | None) -> list[dict]:
    blocks: list[dict] = []

    # question container FocusVision
    q_containers = driver.find_elements(By.CSS_SELECTOR, "div.question[role='radiogroup'], div.question.radio, div.question.checkbox")
    for q in q_containers:
        try:
            answers = q.find_element(By.CSS_SELECTOR, ".answers.answers-list")
        except Exception:
            continue

        # inputs souvent masquÃƒÂ©s (fir-hidden). Variante FocusVision:
        # - clickableCell peut ÃƒÂªtre sur .element OU sur un ancÃƒÂªtre/descendant.
        # => on ÃƒÂ©largit un peu, mais toujours sous .answers.answers-list (scope strict).
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

                # IMPORTANT: on clique un wrapper cliquable (pas l'input masquÃƒÂ©).
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
    - Enregistrer un target_id qui clique le bucket VISIBLE (pas un label/input cachÃƒÂ©)
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

    # Question globale (ex: "Quand avez-vous achetÃƒÂ© ... ?")
    global_q = ""
    if container is not None:
        global_q = _extract_question_from_container(container, options=[]) or ""
    global_q = _norm(global_q)

    # Options = buckets (ÃƒÂ©viter le compteur)
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
                # ex: "Aujourd'hui\n1" => garder 1ÃƒÂ¨re ligne
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

    # Question envoyÃƒÂ©e ÃƒÂ  OpenAI (question globale + carte)
    question = global_q
    if question:
        question = f"{question} Ã¢â‚¬â€ {card_text}"
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

def _extract_askandanswer_mobile_matrix_rows(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Ask&Answer / FirstInsight (Angular Material) : matrices en mode *mobile*
    rendues comme une liste de <mat-expansion-panel class="mobile-matrix-question">.

    ProblÃƒÂ¨me : les <input type=radio> des panels repliÃƒÂ©s ne sont pas "visibles" (height=0, visibility:hidden)
    => notre extraction gÃƒÂ©nÃƒÂ©rique (qui filtre sur visibilitÃƒÂ©) ne sort que la/les lignes dÃƒÂ©jÃƒÂ  ouvertes.

    StratÃƒÂ©gie DOM-only, prÃƒÂ©dictible:
    - dÃƒÂ©tecter les panels mobile-matrix-question
    - crÃƒÂ©er 1 bloc radio par ligne (header = libellÃƒÂ© de la ligne)
    - options = textes des labels dans le panel
    - registry: option_xpath_map pointe sur label[for=inputId] DANS le panel
      + pre_click_xpaths pour ouvrir le panel avant de cliquer l'option
    """
    frame_chain = list(frame_chain or [])

    try:
        panels = driver.find_elements(By.CSS_SELECTOR, "mat-expansion-panel.mobile-matrix-question")
    except Exception:
        panels = []

    if not panels:
        return []

    # Question globale (titre de la carte)
    global_q = ""
    try:
        titles = driver.find_elements(By.CSS_SELECTOR, "mat-card-title div")
        if titles:
            global_q = _norm(titles[0].text or titles[0].get_attribute("innerText") or "")
    except Exception:
        global_q = ""

    blocks: list[dict] = []

    # Budget (ÃƒÂ©vite prompts ÃƒÂ©normes sur des listes trÃƒÂ¨s longues)
    try:
        max_rows = int(os.getenv("AA_MATRIX_MAX_ROWS", "40") or "40")
        if max_rows <= 0:
            max_rows = 40
    except Exception:
        max_rows = 40

    def _open_panel_if_needed(panel) -> None:
        """
        Angular Material: le contenu (radios) peut ÃƒÂªtre rendu via *ngIf uniquement quand le panel est ouvert.
        On ouvre le panel (1 fois) puis on attend briÃƒÂ¨vement que les radios apparaissent.
        """
        try:
            hdr = panel.find_element(By.CSS_SELECTOR, "mat-expansion-panel-header")
        except Exception:
            return

        try:
            if (hdr.get_attribute("aria-expanded") or "").strip().lower() == "true":
                return
        except Exception:
            pass

        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", hdr)
        except Exception:
            pass
        time.sleep(0.05)

        pre_count = 0
        try:
            pre_count = len(panel.find_elements(By.CSS_SELECTOR, "mat-radio-button"))
        except Exception:
            pre_count = 0

        try:
            hdr.click()
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", hdr)
            except Exception:
                return

        t0 = time.time()
        while time.time() - t0 < 1.2:
            # 1) Angular Material met ÃƒÂ  jour aria-expanded sur le header (signal fiable).
            try:
                if (hdr.get_attribute("aria-expanded") or "").strip().lower() == "true":
                    break
            except Exception:
                pass

            # 2) fallback: si le contenu est lazy-rendered, attendre l'apparition des radios.
            if pre_count == 0:
                try:
                    if panel.find_elements(By.CSS_SELECTOR, "mat-radio-button"):
                        break
                except Exception:
                    pass

            time.sleep(0.05)

    for panel in panels[:max_rows]:
        try:
            panel_id = (panel.get_attribute("id") or "").strip()
            if not panel_id:
                panel_id = f"panel_{zlib.adler32((panel.get_attribute('outerHTML') or '').encode('utf-8'))}"

            # libellÃƒÂ© de ligne = header
            row_label = ""
            try:
                htxt = panel.find_elements(By.CSS_SELECTOR, "mat-expansion-panel-header .matrix-text-color")
                if htxt:
                    row_label = _norm(htxt[0].text or htxt[0].get_attribute("innerText") or "")
            except Exception:
                row_label = ""

            if not row_label:
                # fallback: 1ÃƒÂ¨re ligne du header (souvent "Nom (dÃƒÂ©tails)" puis la sÃƒÂ©lection en dessous)
                try:
                    hdrs = panel.find_elements(By.CSS_SELECTOR, "mat-expansion-panel-header")
                    if hdrs:
                        raw = hdrs[0].text or hdrs[0].get_attribute("innerText") or ""
                        raw = (raw.splitlines()[0] if raw else "")
                        row_label = _norm(raw)
                except Exception:
                    row_label = ""

            if not row_label:
                continue

            # options (dans le panel body)
            options: list[str] = []

            def _collect_opt_nodes():
                try:
                    return panel.find_elements(By.CSS_SELECTOR, "mat-radio-button .mat-radio-label-content")
                except Exception:
                    return []

            def _read_options(nodes) -> list[str]:
                opts: list[str] = []
                for n in nodes:
                    try:
                        t = _norm(n.text or n.get_attribute("innerText") or "")
                        if t and t not in opts:
                            opts.append(t)
                    except Exception:
                        continue
                return opts

            opt_nodes = _collect_opt_nodes()
            options = _read_options(opt_nodes)

            # Si le panel est repliÃƒÂ©, Selenium peut retourner "" pour du texte non visible.
            # On force une ouverture (1 fois) puis on relit.
            if not options:
                _open_panel_if_needed(panel)
                opt_nodes = _collect_opt_nodes()
                options = _read_options(opt_nodes)

            if not options:
                continue

            # registry: map option -> xpath (label[for=inputId]) scoped au panel
            def _build_option_xpath_map() -> dict[str, str]:
                m: dict[str, str] = {}
                try:
                    rbs = panel.find_elements(By.CSS_SELECTOR, "mat-radio-button")
                except Exception:
                    rbs = []

                # 1) mapping stable : on prÃƒÂ©fÃƒÂ¨re l'attribut @value de l'input (beaucoup plus robuste que le texte)
                for rb in rbs:
                    try:
                        lab_txt = ""
                        try:
                            lc = rb.find_elements(By.CSS_SELECTOR, ".mat-radio-label-content")
                            if lc:
                                lab_txt = _norm(lc[0].text or lc[0].get_attribute("innerText") or "")
                        except Exception:
                            lab_txt = ""

                        if not lab_txt:
                            try:
                                lab_txt = _norm(rb.text or rb.get_attribute("innerText") or "")
                            except Exception:
                                lab_txt = ""

                        if not lab_txt:
                            continue

                        pid = _xpath_literal(panel_id)
                        # Si on peut, on construit un XPath sur @value (stable)
                        val = ""
                        try:
                            inp = rb.find_element(By.CSS_SELECTOR, "input.mat-radio-input")
                            val = (inp.get_attribute("value") or "").strip()
                        except Exception:
                            val = ""

                        if val:
                            vlit = _xpath_literal(val)
                            xp = (
                                f"(//mat-expansion-panel[@id={pid}]"
                                f"//mat-radio-button[.//input[@type='radio' and @value={vlit}]]"
                                f"//label[contains(@class,'mat-radio-label')])[1]"
                            )
                        else:
                            # Fallback texte (si @value indisponible)
                            lit = _xpath_literal(lab_txt)
                            xp = (
                                f"(//mat-expansion-panel[@id={pid}]"
                                f"//mat-radio-button[.//*[contains(@class,'mat-radio-label-content') and normalize-space(.)={lit}]]"
                                f"//label[contains(@class,'mat-radio-label')])[1]"
                            )
                        m[_norm_key(lab_txt)] = xp
                    except Exception:
                        continue

                # 2) fallback: certains DOM (ex: mat-table) n'ont pas le texte dans chaque radio.
                #    Dans ce cas, on mappe par position (ordre des options) -> n-iÃƒÂ¨me mat-radio-button du panel.
                if not m and options:
                    try:
                        pid = _xpath_literal(panel_id)
                        for i, opt in enumerate(options):
                            if not opt:
                                continue
                            xp = (
                                f"(//mat-expansion-panel[@id={pid}]//*[contains(@class,'mat-expansion-panel-body')]//mat-radio-button)[{i+1}]"
                                f"//label[contains(@class,'mat-radio-label')][1]"
                            )
                            m[_norm_key(opt)] = xp
                    except Exception:
                        pass

                return m

            option_xpath_map = _build_option_xpath_map()
            if not option_xpath_map:
                _open_panel_if_needed(panel)
                option_xpath_map = _build_option_xpath_map()

            if not option_xpath_map:
                continue

            group_key = f"aa_mobile_matrix_row:{panel_id}"
            question = f"{global_q} Ã¢â‚¬â€ {row_label}" if global_q else row_label
            target_id = make_target_id("group", group_key, question)

            # prÃƒÂ©-clic : ouvrir le panel avant clic option
            pre_click_xpaths = []
            try:
                pid = _xpath_literal(panel_id)
                pre_click_xpaths = [f"(//mat-expansion-panel[@id={pid}]//mat-expansion-panel-header)[1]"]
            except Exception:
                pre_click_xpaths = []

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "radio",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "pre_click_xpaths": pre_click_xpaths,
                    "frame_chain": frame_chain,
                    "aa_mobile_matrix": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key, "aa_mobile_matrix": True},
                }
            )
        except Exception:
            continue

    return blocks

def _extract_askandanswer_selection_list_questions(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Ask&Answer / FirstInsight (Angular Material) : questions rendues via <mat-selection-list>.

    ProblÃƒÂ¨me:
    - les options ne sont pas des <input type=checkbox>, donc l'extraction gÃƒÂ©nÃƒÂ©rique (radios/checkbox) ne voit rien.
    - le seul <input> prÃƒÂ©sent est souvent l'option "Autre (veuillez prÃƒÂ©ciser)" => on extrait une fausse question.

    StratÃƒÂ©gie DOM-only, stricte et non-invasive:
    - ne s'active que si on dÃƒÂ©tecte un <app-survey-page> ET des <mat-selection-list> sous appQuestionContainer
    - retourne 1 bloc par selection-list:
        - question = mat-card-title
        - options = texte des mat-list-option (fallback mat-label pour l'option Autre)
        - option_xpath_map = XPath stable sur l'id de chaque mat-list-option (answer-*-*)

    Objectif:
    - corriger ce provider sans impacter les cas canoniques non-Angular.
    """
    frame_chain = list(frame_chain or [])

    # Gate strict : pages Ask&Answer (Angular) uniquement
    try:
        if not driver.find_elements(By.CSS_SELECTOR, "app-survey-page"):
            return []
    except Exception:
        return []

    try:
        lists = driver.find_elements(
            By.CSS_SELECTOR,
            "div[id^='appQuestionContainer-'] mat-selection-list[role='listbox']",
        )
    except Exception:
        lists = []

    if not lists:
        return []

    blocks: list[dict] = []

    # Budget (ÃƒÂ©vite prompts ÃƒÂ©normes si provider change et renvoie une liste trÃƒÂ¨s longue)
    try:
        max_lists = int(os.getenv("AA_SELECTION_LIST_MAX", "10") or "10")
        if max_lists <= 0:
            max_lists = 10
    except Exception:
        max_lists = 10

    for sl in lists[:max_lists]:
        try:
            # options candidates
            try:
                opt_els = sl.find_elements(By.CSS_SELECTOR, "mat-list-option[role='option']")
            except Exception:
                opt_els = []

            # ignore templates/vides
            if len(opt_els) < 2:
                continue

            # remonter au conteneur de question
            q_container = None
            try:
                q_container = sl.find_element(
                    By.XPATH,
                    "ancestor::div[starts-with(@id,'appQuestionContainer-')][1]",
                )
            except Exception:
                q_container = None

            # texte de question
            question = ""
            try:
                scope = q_container or sl
                titles = scope.find_elements(By.CSS_SELECTOR, "mat-card-title div")
                if titles:
                    question = _norm(titles[0].text or titles[0].get_attribute("innerText") or "")
            except Exception:
                question = ""

            if not question:
                continue

            # itype : checkbox (multi) par dÃƒÂ©faut; si aria-multiselectable=false => radio
            itype = "checkbox"
            try:
                am = (sl.get_attribute("aria-multiselectable") or "").strip().lower()
                if am in {"false", "0", "no"}:
                    itype = "radio"
            except Exception:
                pass

            # options + mapping option->xpath
            options: list[str] = []
            option_xpath_map: dict[str, str] = {}

            for opt in opt_els:
                try:
                    label = _norm(opt.text or opt.get_attribute("innerText") or "")
                    # nettoie les multi-lignes (l'option "Autre" peut inclure du bruit)
                    if label:
                        label = _norm(label.splitlines()[0])

                    # fallback robuste pour l'option "Autre (veuillez prÃƒÂ©ciser)"
                    if not label:
                        try:
                            labs = opt.find_elements(By.CSS_SELECTOR, "mat-label")
                            if labs:
                                label = _norm(labs[0].text or labs[0].get_attribute("innerText") or "")
                        except Exception:
                            label = ""

                    if not label:
                        continue

                    # xpath stable : l'id answer-*-* est unique et cliquable
                    xp = ""
                    try:
                        oid = (opt.get_attribute("id") or "").strip()
                        if oid:
                            xp = f"(//*[@id={_xpath_literal(oid)}])[1]"
                        else:
                            xp = _best_xpath_for_element(driver, opt)
                    except Exception:
                        xp = ""

                    if not xp:
                        continue

                    nk = _norm_key(label)
                    if nk in option_xpath_map:
                        continue

                    option_xpath_map[nk] = xp
                    options.append(label)
                except Exception:
                    continue

            if len(options) < 2 or not option_xpath_map:
                continue

            sl_id = ""
            cont_id = ""
            try:
                sl_id = (sl.get_attribute("id") or "").strip()
            except Exception:
                sl_id = ""
            try:
                cont_id = (q_container.get_attribute("id") or "").strip() if q_container else ""
            except Exception:
                cont_id = ""

            group_key = f"aa_selection_list:{cont_id}:{sl_id}".strip(":")
            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": itype,
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                    "aa_selection_list": True,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": itype,
                    "options": options,
                    "max_select": _compute_max_select(itype, options),
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key, "aa_selection_list": True},
                }
            )

        except Exception:
            continue

    return blocks

def _extract_cmix_radio_question_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """CMIX (survey.cmix.com) : extraction DOM-only des questions radio.

    Bug visÃƒÂ© (capture CMIX): la page affiche des radios (ex: politique de confidentialitÃƒÂ©)
    mais l'extraction gÃƒÂ©nÃƒÂ©rique peut retourner 0 question_blocks, dÃƒÂ©clenchant le fallback
    CTA-only et sautant la question.

    StratÃƒÂ©gie:
    - activation stricte uniquement si le markup CMIX est dÃƒÂ©tectÃƒÂ© (.cm-question-wrapper + .cm-radio-label)
    - 1 bloc par groupe radio (name) dans un wrapper
    - mapping option->xpath en privilÃƒÂ©giant le label texte (.cm-radio-label) plutÃƒÂ´t que le label "bouton" (.cm-radio-input)
    """

    frame_chain = list(frame_chain or [])

    # Gate strict: CMIX radio wrappers
    try:
        if not driver.find_elements(By.CSS_SELECTOR, ".cm-question-wrapper .cm-radio-label"):
            return []
    except Exception:
        return []

    try:
        wrappers = driver.find_elements(By.CSS_SELECTOR, ".cm-question-wrapper")
    except Exception:
        wrappers = []

    if not wrappers:
        return []

    blocks: list[dict] = []

    for w in wrappers[:25]:
        try:
            # wrapper visible (ÃƒÂ©vite templates hors ÃƒÂ©cran)
            try:
                if not w.is_displayed():
                    continue
            except Exception:
                pass

            # question text (CMIX)
            question = ""
            try:
                qels = w.find_elements(By.CSS_SELECTOR, ".cm-question-text")
                if qels:
                    question = _norm(qels[0].text or qels[0].get_attribute("innerText") or "")
            except Exception:
                question = ""

            if not question:
                # fallback (rare): premiÃƒÂ¨re ligne non vide du wrapper
                raw = _norm(w.text or w.get_attribute("innerText") or "")
                if raw:
                    question = _norm(raw.splitlines()[0])

            if not question:
                continue

            # radios dans le wrapper
            try:
                radios = w.find_elements(By.CSS_SELECTOR, "input[type='radio'][id][name]")
            except Exception:
                radios = []

            if len(radios) < 2:
                continue

            # group par name (CMIX utilise name numeric pour le groupe)
            by_name: dict[str, list[Any]] = {}
            for r in radios:
                try:
                    if _looks_like_system_field(r):
                        continue
                except Exception:
                    pass

                # On accepte les inputs masquÃƒÂ©s si le label texte existe
                try:
                    rid = (r.get_attribute("id") or "").strip()
                    rname = (r.get_attribute("name") or "").strip()
                    if not rid or not rname:
                        continue
                    # label texte (pas le label "cercle")
                    lbls = w.find_elements(By.CSS_SELECTOR, f"label.cm-radio-label[for='{rid}']")
                    if not lbls:
                        continue
                    t = _norm(lbls[0].text or lbls[0].get_attribute("innerText") or "")
                    if not t or len(t) < 2:
                        continue
                    by_name.setdefault(rname, []).append(r)
                except Exception:
                    continue

            for rname, els in by_name.items():
                if len(els) < 2:
                    continue

                options: list[str] = []
                option_xpath_map: dict[str, str] = {}

                for r in els:
                    try:
                        rid = (r.get_attribute("id") or "").strip()
                        if not rid:
                            continue
                        lbls = w.find_elements(By.CSS_SELECTOR, f"label.cm-radio-label[for='{rid}']")
                        if not lbls:
                            continue
                        label = _norm(lbls[0].text or lbls[0].get_attribute("innerText") or "")
                        if not label:
                            continue

                        # XPath stable: label texte CMIX (ÃƒÂ©vite le label .cm-radio-input sans texte)
                        rid_lit = _xpath_literal(rid)
                        xp = (
                            f"(//label[contains(concat(' ',normalize-space(@class),' '),' cm-radio-label ') and @for={rid_lit}])[1]"
                        )

                        nk = _norm_key(label)
                        if nk in option_xpath_map:
                            continue

                        option_xpath_map[nk] = xp
                        options.append(label)
                    except Exception:
                        continue

                if len(options) < 2 or not option_xpath_map:
                    continue

                group_key = f"cmix_radio:name:{rname}"
                target_id = make_target_id("group", group_key, question)

                register_target(
                    target_id,
                    {
                        "kind": "group",
                        "itype": "radio",
                        "group_key": group_key,
                        "question": question,
                        "option_xpath_map": option_xpath_map,
                        "frame_chain": frame_chain,
                        "cmix": True,
                    },
                )

                blocks.append(
                    {
                        "question": question,
                        "itype": "radio",
                        "options": options,
                        "max_select": 1,
                        "target_id": target_id,
                        "context": {"kind": "group", "group_key": group_key, "cmix": True},
                    }
                )

        except Exception:
            continue

    return blocks

def _extract_areyounet_matrix_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    Extrait les matrices AreYouNet (div.MatriceViewElement).
    Retourne 1 question_block par ligne de la matrice.
    """
    blocks: list[dict] = []
    frame_chain = frame_chain or []

    # DÃƒÂ©tection stricte: prÃƒÂ©sence de MatriceViewElement
    try:
        matrices = driver.find_elements(By.CSS_SELECTOR, "div.MatriceViewElement")
    except Exception:
        return []

    if not matrices:
        return []

    rx_radio = re.compile(r"switch_radio\((?:'|\")(?P<qname>[^'\"]+)(?:'|\")\s*,\s*(?P<idx>\d+)")
    rx_checkbox = re.compile(r"switch_checkbox\((?:'|\")(?P<qname>[^'\"]+)(?:'|\")\s*,\s*(?P<idx>\d+)")
    
    # DÃ©duplication: Ã©viter de traiter le mÃªme qname deux fois (tables imbriquÃ©es, etc.)
    seen_qnames: set[str] = set()

    for matrix in matrices:
        try:
            # 1) Extraire le titre global de la question
            title = ""
            try:
                title_el = matrix.find_element(By.CSS_SELECTOR, "span.elementTitle")
                title = _norm(title_el.text or title_el.get_attribute("innerText") or "")
            except Exception:
                pass

            if not title:
                # Fallback: chercher dans p.titleQuestionElement
                try:
                    title_el = matrix.find_element(By.CSS_SELECTOR, "p.titleQuestionElement")
                    title = _norm(title_el.text or title_el.get_attribute("innerText") or "")
                except Exception:
                    pass

            if not title:
                continue

            # 2) Extraire les headers de colonnes (options communes ÃƒÂ  toutes les lignes)
            col_headers: list[str] = []
            try:
                header_cells = matrix.find_elements(By.CSS_SELECTOR, "td.tableHeader")
                for hc in header_cells:
                    txt = _norm(hc.text or hc.get_attribute("innerText") or "")
                    if txt:
                        col_headers.append(txt)
            except Exception:
                pass

            if len(col_headers) < 2:
                continue

            # 3) Extraire les lignes (chaque ligne = 1 question)
            # Structure: <tr> contenant <td class="tableRow">Label</td> + plusieurs <td onclick="switch_radio(...)">
            try:
                rows = matrix.find_elements(By.CSS_SELECTOR, "tr")
            except Exception:
                continue

            for row in rows:
                try:
                    # Chercher le label de ligne (td.tableRow)
                    row_label = ""
                    try:
                        row_label_el = row.find_element(By.CSS_SELECTOR, "td.tableRow")
                        row_label = _norm(row_label_el.text or row_label_el.get_attribute("innerText") or "")
                    except Exception:
                        continue

                    if not row_label:
                        continue

                    # Chercher d'abord switch_radio, sinon switch_checkbox
                    clickables = row.find_elements(By.CSS_SELECTOR, "td[onclick*='switch_radio']")
                    cell_type = "radio"
                    if len(clickables) < 2:
                        clickables = row.find_elements(By.CSS_SELECTOR, "td[onclick*='switch_checkbox']")
                        cell_type = "checkbox"
                    if len(clickables) < 2:
                        continue

                    # Extraire le qname depuis le premier onclick
                    qname = ""
                    rx = rx_radio if cell_type == "radio" else rx_checkbox
                    for cl in clickables:
                        try:
                            oc = (cl.get_attribute("onclick") or "").strip()
                            m = rx.search(oc)
                            if m:
                                qname = (m.group("qname") or "").strip()
                                break
                        except Exception:
                            continue

                    if not qname:
                        continue

                    # DÃ©duplication: Ã©viter de traiter le mÃªme qname deux fois
                    if qname in seen_qnames:
                        continue
                    seen_qnames.add(qname)

                    # Construire la question complÃƒÂ¨te
                    question = f"{title} [{row_label}]"

                    # Construire option_xpath_map: option_label -> xpath du td cliquable
                    option_xpath_map: dict[str, str] = {}
                    ayn_value_map: dict[str, str] = {}

                    for idx, header in enumerate(col_headers):
                        if idx >= len(clickables):
                            break

                        # XPath pour cibler le td avec onclick contenant qname et idx
                        func_name = "switch_radio" if cell_type == "radio" else "switch_checkbox"
                        # XPath strict: matcher 'QNAME',IDX pour Ã©viter les matchs partiels (ex: QA03:221621_1 vs QA03:221621_11)
                        xp = (
                            f"//td[contains(@onclick,\"{func_name}('{qname}',{idx}\")]"
                        )
                        option_xpath_map[_norm_key(header)] = xp

                        # Extraire la valeur associÃ©e (input hidden *_rad_{idx}_value ou *_chk_{idx}_value)
                        try:
                            suffix = "rad" if cell_type == "radio" else "chk"
                            v_name = f"{qname}_{suffix}_{idx}_value"
                            v_el = clickables[idx].find_element(By.CSS_SELECTOR, f"input[name='{v_name}']")
                            value = (v_el.get_attribute("value") or "").strip()
                            if value:
                                ayn_value_map[_norm_key(header)] = value
                        except Exception:
                            pass

                    if len(option_xpath_map) < 2:
                        continue

                    # Enregistrer le bloc
                    group_key = f"areyounet:matrix:{qname}"
                    target_id = make_target_id("group", group_key, question)

                    itype = cell_type  # "radio" ou "checkbox"
                    max_select = 1 if cell_type == "radio" else len(col_headers) - 1  # checkbox: multi-select (sauf NSP)

                    register_target(
                        target_id,
                        {
                            "kind": "group",
                            "itype": "radio",
                            "group_key": group_key,
                            "question": question,
                            "option_xpath_map": option_xpath_map,
                            "frame_chain": frame_chain,
                            "ayn_field_name": qname,
                            "ayn_value_map": ayn_value_map,
                        },
                    )

                    blocks.append(
                        {
                            "question": question,
                            "itype": itype,
                            "options": col_headers.copy(),
                            "max_select": max_select,
                            "target_id": target_id,
                            "context": {"kind": "group", "group_key": group_key},
                        }
                    )

                except Exception:
                    continue

        except Exception:
            continue

    return blocks

def _extract_areyounet_switch_radio_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    blocks: list[dict] = []
    frame_chain = frame_chain or []

    # DÃƒÂ©tection STRICTE pour ne pas impacter les cas canoniques.
    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "td[id^='QCB_'], div[id^='QCB_']")
    except Exception:
        return []

    rx = re.compile(r"switch_radio\((?:'|\")(?P<qname>[^'\"]+)(?:'|\")\s*,\s*(?P<idx>\d+)")

    for cont in containers:
        try:
            clickables = cont.find_elements(By.CSS_SELECTOR, "[onclick*='switch_radio(']")
        except Exception:
            continue

        if not clickables:
            continue

        cont_id = ""
        try:
            cont_id = (cont.get_attribute("id") or "").strip()
        except Exception:
            cont_id = ""

        # question text
        question = ""
        try:
            q_el = cont.find_element(By.CSS_SELECTOR, "p.titleQuestionElement .elementTitle")
            question = _norm(q_el.text or q_el.get_attribute("innerText") or "")
        except Exception:
            # fallback cheap: premiÃƒÂ¨re ligne "question-like"
            try:
                raw = cont.get_attribute("innerText") or cont.text or ""
                for line in (raw.splitlines() if raw else []):
                    t = _norm(line)
                    if _is_question_text(t):
                        question = t
                        break
            except Exception:
                pass

        if not question:
            continue

        by_qname: dict[str, dict[int, dict[str, str]]] = {}

        for el in clickables:
            try:
                oc = (el.get_attribute("onclick") or "").strip()
            except Exception:
                oc = ""
            if not oc:
                continue

            m = rx.search(oc)
            if not m:
                continue

            qname = (m.group("qname") or "").strip()
            try:
                idx = int(m.group("idx"))
            except Exception:
                continue

            if not qname:
                continue

            # ------------------------------------------------------------
            # AreYouNet: toutes les options peuvent ÃƒÂªtre sur la MÃƒÅ ME row.
            # On cherche le label dans le TD courant ou le TD sibling suivant,
            # PAS dans toute la row (sinon on prend le mauvais label).
            # ------------------------------------------------------------
            label = ""
            value = ""

            # 1) Chercher span.elementText dans le TD courant (cas oÃƒÂ¹ onclick est sur le TD label)
            try:
                sp = el.find_elements(By.CSS_SELECTOR, "span.elementText")
                if sp:
                    label = _norm(sp[0].text or sp[0].get_attribute("innerText") or "")
            except Exception:
                pass

            # 2) Si pas trouvÃƒÂ©, chercher dans le TD sibling suivant immÃƒÂ©diat
            if not label:
                try:
                    next_td = el.find_element(By.XPATH, "following-sibling::td[1]")
                    sp = next_td.find_elements(By.CSS_SELECTOR, "span.elementText")
                    if sp:
                        label = _norm(sp[0].text or sp[0].get_attribute("innerText") or "")
                except Exception:
                    pass

            # 3) Fallback: texte brut du TD courant (si pas de span.elementText)
            if not label:
                try:
                    raw = el.get_attribute("innerText") or el.text or ""
                    label = _norm(raw)
                except Exception:
                    pass

            # option value (utile pour valider la sÃƒÂ©lection cÃƒÂ´tÃƒÂ© dispatcher)
            # Chercher dans le TD courant ou remonter ÃƒÂ  la row si nÃƒÂ©cessaire
            try:
                v_name = f"{qname}_rad_{idx}_value"
                v_el = el.find_element(By.CSS_SELECTOR, f"input[name='{v_name}']")
                value = (v_el.get_attribute("value") or "").strip()
            except Exception:
                # Fallback: chercher dans la row entiÃƒÂ¨re pour le value hidden
                try:
                    row = el.find_element(By.XPATH, "ancestor::tr[1]")
                    v_el = row.find_element(By.CSS_SELECTOR, f"input[name='{v_name}']")
                    value = (v_el.get_attribute("value") or "").strip()
                except Exception:
                    value = ""

            if not label:
                continue

            by_qname.setdefault(qname, {})[idx] = {"label": label, "value": value}

        for qname, idx_map in by_qname.items():
            if len(idx_map) < 2:
                continue  # ÃƒÂ©vite de prendre un bruit isolÃƒÂ©

            options = [idx_map[i]["label"] for i in sorted(idx_map.keys()) if idx_map[i].get("label")]
            if len(options) < 2:
                continue

            group_key = f"areyounet:switch_radio:{qname}"
            target_id = make_target_id("group", group_key, question)

            option_xpath_map: dict[str, str] = {}
            ayn_value_map: dict[str, str] = {}

            # XPath stable-ish: scope par container id + tokens onclick
            base = f"//*[@id={_xpath_literal(cont_id)}]" if cont_id else "//*"
            for i in sorted(idx_map.keys()):
                lbl = idx_map[i].get("label") or ""
                if not lbl:
                    continue

                xp = (
                    f"({base}//*[contains(@onclick,'switch_radio') and "
                    f"contains(@onclick,{_xpath_literal(qname)}) and "
                    f"contains(@onclick,{_xpath_literal(','+str(i))}) and "
                    f".//span[contains(@class,'elementText')]][1] | "
                    f"{base}//*[contains(@onclick,'switch_radio') and "
                    f"contains(@onclick,{_xpath_literal(qname)}) and "
                    f"contains(@onclick,{_xpath_literal(','+str(i))})][1])"
                )

                option_xpath_map[_norm_key(lbl)] = xp
                if idx_map[i].get("value"):
                    ayn_value_map[_norm_key(lbl)] = idx_map[i]["value"]

            if len(option_xpath_map) < 2:
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
                    # AreYouNet: sÃƒÂ©lection stockÃƒÂ©e dans un input hidden name=qname
                    "ayn_field_name": qname,
                    "ayn_value_map": ayn_value_map,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "radio",
                    "options": options,
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )

    return blocks

def _extract_areyounet_switch_checkbox_blocks(driver, frame_chain: list[int] | None) -> list[dict]:
    """
    AreYouNet CHECKBOX (areyounet.com / runet) : checkboxes simulÃ©es via onclick switch_checkbox().
    Pattern: <td onclick="switch_checkbox('QA04:215604',0,...)"><img class="img_checkbox">
    Les vrais inputs sont tous hidden ; la sÃ©lection se fait via JS sur les images.
    """
    blocks: list[dict] = []
    frame_chain = frame_chain or []

    # DÃ©tection STRICTE : conteneurs AreYouNet uniquement
    try:
        containers = driver.find_elements(By.CSS_SELECTOR, "td[id^='QCB_'], div[id^='QCB_']")
    except Exception:
        return []

    rx = re.compile(r"switch_checkbox\((?:'|\")(?P<qname>[^'\"]+)(?:'|\")\s*,\s*(?P<idx>\d+)")

    for cont in containers:
        try:
            clickables = cont.find_elements(By.CSS_SELECTOR, "[onclick*='switch_checkbox(']")
        except Exception:
            continue

        if not clickables:
            continue

        cont_id = ""
        try:
            cont_id = (cont.get_attribute("id") or "").strip()
        except Exception:
            cont_id = ""

        # question text
        question = ""
        try:
            q_el = cont.find_element(By.CSS_SELECTOR, "p.titleQuestionElement .elementTitle")
            question = _norm(q_el.text or q_el.get_attribute("innerText") or "")
        except Exception:
            # fallback: premiÃ¨re ligne "question-like"
            try:
                raw = cont.get_attribute("innerText") or cont.text or ""
                for line in (raw.splitlines() if raw else []):
                    t = _norm(line)
                    if _is_question_text(t):
                        question = t
                        break
            except Exception:
                pass

        if not question:
            continue

        by_qname: dict[str, dict[int, dict[str, str]]] = {}

        for el in clickables:
            try:
                oc = (el.get_attribute("onclick") or "").strip()
            except Exception:
                oc = ""
            if not oc:
                continue

            m = rx.search(oc)
            if not m:
                continue

            qname = (m.group("qname") or "").strip()
            try:
                idx = int(m.group("idx"))
            except Exception:
                continue

            if not qname:
                continue

            # AreYouNet: options sur la MÃŠME row.
            # Chercher le label dans le TD courant ou le TD sibling suivant.
            label = ""
            value = ""

            # 1) span.elementText dans le TD courant
            try:
                sp = el.find_elements(By.CSS_SELECTOR, "span.elementText")
                if sp:
                    label = _norm(sp[0].text or sp[0].get_attribute("innerText") or "")
            except Exception:
                pass

            # 2) TD sibling suivant immÃ©diat
            if not label:
                try:
                    next_td = el.find_element(By.XPATH, "following-sibling::td[1]")
                    sp = next_td.find_elements(By.CSS_SELECTOR, "span.elementText")
                    if sp:
                        label = _norm(sp[0].text or sp[0].get_attribute("innerText") or "")
                except Exception:
                    pass

            # 3) Fallback: texte brut du TD courant
            if not label:
                try:
                    raw = el.get_attribute("innerText") or el.text or ""
                    label = _norm(raw)
                except Exception:
                    pass

            # option value : pattern {qname}_chk_{idx}_value
            try:
                v_name = f"{qname}_chk_{idx}_value"
                v_el = el.find_element(By.CSS_SELECTOR, f"input[name='{v_name}']")
                value = (v_el.get_attribute("value") or "").strip()
            except Exception:
                # Fallback: chercher dans la row entiÃ¨re
                try:
                    row = el.find_element(By.XPATH, "ancestor::tr[1]")
                    v_el = row.find_element(By.CSS_SELECTOR, f"input[name='{v_name}']")
                    value = (v_el.get_attribute("value") or "").strip()
                except Exception:
                    value = ""

            if not label:
                continue

            by_qname.setdefault(qname, {})[idx] = {"label": label, "value": value}

        for qname, idx_map in by_qname.items():
            if len(idx_map) < 2:
                continue

            options = [idx_map[i]["label"] for i in sorted(idx_map.keys()) if idx_map[i].get("label")]
            if len(options) < 2:
                continue

            group_key = f"areyounet:switch_checkbox:{qname}"
            target_id = make_target_id("group", group_key, question)

            option_xpath_map: dict[str, str] = {}
            ayn_value_map: dict[str, str] = {}

            base = f"//*[@id={_xpath_literal(cont_id)}]" if cont_id else "//*"
            for i in sorted(idx_map.keys()):
                lbl = idx_map[i].get("label") or ""
                if not lbl:
                    continue

                xp = (
                    f"({base}//*[contains(@onclick,'switch_checkbox') and "
                    f"contains(@onclick,{_xpath_literal(qname)}) and "
                    f"contains(@onclick,{_xpath_literal(','+str(i))}) and "
                    f".//span[contains(@class,'elementText')]][1] | "
                    f"{base}//*[contains(@onclick,'switch_checkbox') and "
                    f"contains(@onclick,{_xpath_literal(qname)}) and "
                    f"contains(@onclick,{_xpath_literal(','+str(i))})][1])"
                )

                option_xpath_map[_norm_key(lbl)] = xp
                if idx_map[i].get("value"):
                    ayn_value_map[_norm_key(lbl)] = idx_map[i]["value"]

            if len(option_xpath_map) < 2:
                continue

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": "checkbox",
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                    # AreYouNet: sÃ©lection stockÃ©e dans input hidden name=qname
                    "ayn_field_name": qname,
                    "ayn_value_map": ayn_value_map,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "checkbox",
                    "options": options,
                    "max_select": len(options),  # checkbox = multi-select
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )

    return blocks

def _analyze_dom_current_context(driver, frame_chain=None) -> List[Dict[str, Any]]:
    """
    Analyse le DOM courant et retourne une liste de QuestionBlock.
    IMPORTANT: 1 bloc par question (group radio/checkbox).
    """
    
    frame_chain = frame_chain or []
    question_blocks: List[Dict[str, Any]] = []
    clear_registry()

    # --- 0) FocusVision cardsort (UI visible) ---
    # Si prÃƒÂ©sent, on prÃƒÂ©fÃƒÂ¨re cette stratÃƒÂ©gie (1 seule carte active) ÃƒÂ  l'extraction radio/checkbox cachÃƒÂ©e.
    try:
        cs_block = _extract_focusvision_cardsort_block(driver, frame_chain)
        if cs_block:
            return [cs_block]
    except Exception:
        pass

    # --- 0b) Ask&Answer / FirstInsight : matrice mobile (expansion panels) ---
    # Objectif: extraire TOUTES les lignes mÃƒÂªme si les panels sont repliÃƒÂ©s (inputs non visibles).
    try:
        aa_blocks = _extract_askandanswer_mobile_matrix_rows(driver, frame_chain)
        if aa_blocks:
            return aa_blocks
    except Exception:
        pass

    # --- 0c) Ask&Answer / FirstInsight : listes multi (mat-selection-list) ---
    # Objectif: ÃƒÂ©viter de prendre l'input 'Autre (veuillez prÃƒÂ©ciser)' comme une question.
    try:
        aa_sl_blocks = _extract_askandanswer_selection_list_questions(driver, frame_chain)
        if aa_sl_blocks:
            return aa_sl_blocks
    except Exception:
        pass

    # --- 0d) CMIX (survey.cmix.com) : radios rendus via .cm-question-wrapper ---
    # Objectif: ÃƒÂ©viter le fallback CTA-only quand les radios sont visibles mais non extraites.
    try:
        cmix_blocks = _extract_cmix_radio_question_blocks(driver, frame_chain)
        if cmix_blocks:
            return cmix_blocks
    except Exception:
        pass

    # --- 0e) AreYouNet MATRICE (areyounet.com / runet) : grilles frÃƒÂ©quence/satisfaction ---
    # Objectif: extraire les matrices (1 ligne = 1 question radio).
    try:
        ayn_matrix_blocks = _extract_areyounet_matrix_blocks(driver, frame_chain)
        if ayn_matrix_blocks:
            return ayn_matrix_blocks
    except Exception:
        pass

    # --- 0f) AreYouNet SIMPLE (areyounet.com / runet) : radios via onclick switch_radio() ---
    # Objectif: Ã©viter le fallback vision alors que le DOM est exploitable (options Oui/Non).
    try:
        ayn_blocks = _extract_areyounet_switch_radio_blocks(driver, frame_chain)
        if ayn_blocks:
            return ayn_blocks
    except Exception:
        pass

    # --- 0g) AreYouNet CHECKBOX (areyounet.com / runet) : checkboxes via onclick switch_checkbox() ---
    # Objectif: extraire les checkboxes simulÃ©es (img + hidden inputs) sans fallback vision.
    try:
        ayn_chk_blocks = _extract_areyounet_switch_checkbox_blocks(driver, frame_chain)
        if ayn_chk_blocks:
            return ayn_chk_blocks
    except Exception:
        pass

    # --- 1) Radios / checkboxes groupÃ©s ---
    try:
        choice_els = driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='radio'], input[type='checkbox'], [role='radio']:not(svg), [role='checkbox']:not(svg)"
        )
    except Exception:
        choice_els = []

    # Ã¢Å“â€¦ Anti-bruit (Decipher/FIR, etc.) :
    # Des icÃƒÂ´nes SVG portent role="radio"/"checkbox" mais ne sont pas des inputs actionnables.
    # Si on les garde, on duplique les groupes => OpenAI renvoie Q1/Q2 pour la mÃƒÂªme question.
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
            # Anti-bruit: ignorer inputs utilitaires/masquÃƒÂ©s et non actionnables
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
    seen_multi_text_groups = set()

    for k, els in groups.items():
        try:
            # type homogÃƒÂ¨ne dans une clÃƒÂ© donnÃƒÂ©e
            itype = "radio" if k.startswith("radio:") else "checkbox"

            # options = labels des inputs
            options: List[str] = []
            for e in els:
                lbl = _find_associated_label(driver, e)
                if lbl:
                    options.append(lbl)
            # dÃƒÂ©doublonnage conservant l'ordre
            options = list(dict.fromkeys([o for o in options if o]))

            # question = depuis conteneur (et on exclut options)
            container = _nearest_question_container(els[0])
            question = _extract_question_from_container(container, options) if container else ""

            # Ã¢Å“â€¦ Fallback DOM: question parfois hors container (ex: <h2 id="label"> au-dessus du <form>)
            if not question:
                # Ã¢Å“â€¦ Fallback direct: cas trÃƒÂ¨s frÃƒÂ©quent (Cint/QPS) -> <h2 id="label"> contient la question
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
                    # filtre anti "Question 1 de 3" / textes dÃ¢â‚¬â„¢aide gÃƒÂ©nÃƒÂ©riques
                    # IMPORTANT: ne pas rejeter une vraie question longue qui contient juste
                    # "Veuillez sÃƒÂ©lectionner une rÃƒÂ©ponse." (cas Walr, etc.)
                    is_meta = bool(re.match(r"^question\s*\d+", near_lc))
                    if not is_meta:
                        # Ne considÃƒÂ©rer "veuillez sÃƒÂ©lectionner..." comme meta QUE si c'est court (= banniÃƒÂ¨re/erreur)
                        if (len(near_lc) < 140) and ("veuillez" in near_lc) and (("sÃƒÂ©lection" in near_lc) or ("selection" in near_lc)):
                            is_meta = True
                    if (near_lc not in opt_lc) and (not is_meta):
                        question = near

            if not question:
                # dernier recours: bloc "1 option" (rare, mais utile)
                if len(options) == 1 and len(els) == 1:
                    question = options[0]
                else:
                    continue

            # Ã¢Å“â€¦ NEW: si on a une seule checkbox et aucune option dÃƒÂ©tectÃƒÂ©e, on force option=question
            if not options and len(els) == 1 and question:
                options = [question]

            # IMPORTANT: pour les matrices, plusieurs groupes (1 par colonne) partagent la mÃƒÂªme question.
            # Si le group_key est basÃƒÂ© sur name=..., on dÃƒÂ©doublonne par group_key (k), pas par (question, itype).
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

                    # Ã¢Å“â€¦ NEW: single checkbox => si label vide, on mappe sur la question
                    if not lbl and len(els) == 1 and question:
                        lbl = question

                    if not lbl:
                        continue

                    # Ã¢Å“â€¦ Locator STABLE pour le choix (ÃƒÂ©vite XPath absolu fragile)
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
                    # IMPORTANT: sur FocusVision/Decipher-like, l'input radio peut ÃƒÂªtre masquÃƒÂ© (fir-hidden)
                    # et le click doit viser le <label for="...">.
                    if inp_id:
                        id_lit = _xpath_literal(inp_id)

                        # FocusVision/Decipher grid: le <label for=...> peut ÃƒÂªtre 0x0 (template cachÃƒÂ©).
                        # On prÃƒÂ©fÃƒÂ¨re cliquer la cellule <td> qui contient l'input.
                        in_grid = False
                        try:
                            in_grid = bool(e.find_elements(By.XPATH, "ancestor::table[contains(@class,'grid')][1]"))
                        except Exception:
                            in_grid = False

                        if in_grid:
                            xp = (
                                f"(//*[@id={id_lit}]/ancestor::td[contains(@class,'clickableCell')][1] | "
                                f"//*[@id={id_lit}]/ancestor::td[1] | "
                                f"//label[@for={id_lit}]//*[normalize-space(.)!=''] | "
                                f"//label[@for={id_lit}] | "
                                f"//*[@id={id_lit}])"
                            )
                        else:
                            try:
                                has_label = bool(driver.find_elements(By.XPATH, f"//label[@for={id_lit}]"))
                            except Exception:
                                has_label = False

                            if has_label:
                                xp = f"(//label[@for={id_lit}]//*[normalize-space(.)!=''] | //label[@for={id_lit}] | //*[@id={id_lit}])"
                            else:
                                xp = f"//*[@id={id_lit}]"

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

    # --- 1b) Groupes de choix "stylÃƒÂ©s en boutons" (Decipher/Confirmit, etc.) ---
    # Objectif: quand les options ne sont PAS des <input type=radio> visibles,
    # mais une liste de <li>/<button> cliquables (ex: Decipher cardrating)

    def _is_nav_like_choice(text: str) -> bool:
        v = _norm_lc(text)
        if not v:
            return False
        nav_tokens = [
            "continue", "continuer", "next", "suivant",
            "back", "retour", "previous", "prÃƒÂ©cÃƒÂ©dent", "precedent",
            "ok", "submit", "valider", "envoyer", "send",
            "start", "commencer", "finish", "terminer",
            "close", "fermer", "cancel", "annuler",
            "refuser", "decline",
        ]
        return any(tok in v for tok in nav_tokens)

    def _stable_xpath_for_buttonish(el) -> str:
        """
        Locator stable prioritaire pour Decipher:
        - data-uid est trÃƒÂ¨s souvent unique et stable sur la page.
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
                # backup: certains thÃƒÂ¨mes remplissent le texte dans .sq-cardrating-content
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

            # options = textes des boutons (dÃƒÂ©doublonnÃƒÂ©s)
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

            # ÃƒÂ©vite de prendre les banniÃƒÂ¨res dÃ¢â‚¬â„¢erreur comme "question"
            qlc = _norm_lc(question)
            if qlc and ("un problÃƒÂ¨me est survenu" in qlc or ((len(qlc) < 140) and ("veuillez" in qlc) and (("sÃƒÂ©lection" in qlc) or ("selection" in qlc)))):
                # on tente un near-text sur un autre bouton (souvent plus bas = plus proche du vrai libellÃƒÂ©)
                for cand in btns[1:3]:
                    near2 = _norm(_find_question_text_near_element(driver, cand))
                    near2_lc = _norm_lc(near2)
                    if near2 and ("un problÃƒÂ¨me est survenu" not in near2_lc) and not ("veuillez" in near2_lc and "sÃƒÂ©lection" in near2_lc):
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

            # 2) On ignore les ÃƒÂ©lÃƒÂ©ments non actionnables/visibles
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
                    "page suivante", "page prÃƒÂ©cÃƒÂ©dente",
                }:
                    continue

            container = _nearest_question_container(el) or el

            question = ""
            if container:
                question = _extract_question_from_container(container, options=[]) or ""

            # --- [PATCH] Multi-dropdown (DOB/date) : ÃƒÂ©viter de prendre "Mois"/"AnnÃƒÂ©e" comme question ---
            # Sur IPSOS (bootstrap-select), le conteneur des <select> peut exposer seulement les libellÃƒÂ©s de champs
            # ("Mois", "AnnÃƒÂ©e") et masquer le vrai libellÃƒÂ© question ("Quelle est votre date de naissance ?").
            # Si on envoie "AnnÃƒÂ©e Ã¢â‚¬â€ Mois" ÃƒÂ  OpenAI, il peut rÃƒÂ©pondre une annÃƒÂ©e absurde (ex: 2026).
            multi = False
            hint = None
            try:
                if itype == "dropdown" and container:
                    sels = container.find_elements(By.TAG_NAME, "select")
                    multi = bool(sels and len(sels) >= 2)
                    if multi:
                        hint = _dropdown_field_hint(driver, el)
                        field_labels = {"mois", "month", "annÃƒÂ©e", "annee", "year", "jour", "day"}
                        qlc = _norm_lc(question)
                        if (qlc in field_labels) or (hint and qlc == _norm_lc(hint)):
                            alt = _find_question_text_near_element(driver, el) or ""
                            alt_lc = _norm_lc(alt)
                            if alt and alt_lc not in field_labels:
                                question = alt
            except Exception:
                pass

            if not question:
                # important pour YouGov-like: question visible au-dessus mais pas bien "liÃƒÂ©e" au input
                question = _find_question_text_near_element(driver, el) or ""

            if not question:
                question = _find_associated_label(driver, el) or ""
            question = _norm(question)

            if not question:
                continue

            # --- [NEW] Multi-text ("une rÃƒÂ©ponse par case") : regrouper plusieurs inputs texte sous une mÃƒÂªme question ---
            if itype in ("text", "textarea"):
                try:
                    cont_id = (container.get_attribute("id") or "").strip()
                    nm = (el.get_attribute("name") or "").strip()

                    # prefix: "QA03:948176_1" -> "QA03:948176"
                    prefix = nm
                    m_pref = re.match(r"^(.*)_(\d{1,3})$", nm)
                    if m_pref:
                        prefix = m_pref.group(1)

                    # fallback si container id vide: on stabilise avec un xpath de container (rare)
                    if not cont_id and container:
                        try:
                            cont_id = _best_xpath_for_element(driver, container) or ""
                        except Exception:
                            cont_id = ""

                    group_key = f"multitext:{cont_id}:{prefix}"
                    if group_key in seen_multi_text_groups:
                        continue

                    # collect peers dans le mÃƒÂªme container
                    try:
                        peers = container.find_elements(By.CSS_SELECTOR, "input[type='text'], textarea")
                    except Exception:
                        peers = []

                    fields = []
                    peer_names = []
                    for p in peers:
                        try:
                            pt = _detect_itype(p)
                            if pt != itype:
                                continue
                            if _looks_like_system_field(p):
                                continue
                            if not _is_actionable_visible(p):
                                continue
                            pn = (p.get_attribute("name") or "").strip()
                            if pn:
                                peer_names.append(pn)
                            fields.append(p)
                        except Exception:
                            continue

                    if len(fields) >= 2:
                        # signal fort: texte "par case" OU naming "_1/_2/_3..." avec mÃƒÂªme prefix
                        container_txt = _norm_lc(container.text or container.get_attribute("innerText") or "")
                        has_one_per_box = (
                            ("par case" in container_txt)
                            or ("one per box" in container_txt)
                            or ("one per field" in container_txt)
                        )

                        same_prefix_count = 0
                        if prefix and prefix != nm:
                            for pn in peer_names:
                                mm = re.match(r"^(.*)_(\d{1,3})$", pn)
                                if mm and mm.group(1) == prefix:
                                    same_prefix_count += 1

                        if has_one_per_box or same_prefix_count >= 2:
                            max_items = min(3, len(fields))
                            multi_target_id = make_target_id("multi", group_key, question)

                            field_payloads = []
                            for f in fields:
                                try:
                                    fid = (f.get_attribute("id") or "").strip()
                                    fname = (f.get_attribute("name") or "").strip()
                                    ftag = (f.tag_name or "").strip().lower()
                                    fxp = _best_xpath_for_element(driver, f)

                                    falt = []
                                    try:
                                        if ftag and fname:
                                            falt.append(f"//{ftag}[@name={_xpath_literal(fname)}]")
                                        elif fname:
                                            falt.append(f"//*[@name={_xpath_literal(fname)}]")
                                    except Exception:
                                        pass
                                    try:
                                        if fid:
                                            falt.append(f"//*[@id='{fid}']")
                                    except Exception:
                                        pass

                                    falt = [x for x in dict.fromkeys(falt) if x and x != fxp][:4]

                                    field_payloads.append(
                                        {"xpath": fxp, "alt_xpaths": falt, "name": fname, "id": fid, "tag": ftag}
                                    )
                                except Exception:
                                    continue

                            if field_payloads:
                                register_target(
                                    multi_target_id,
                                    {
                                        "kind": "multi_text",
                                        "itype": itype,
                                        "question": question,
                                        "fields": field_payloads,
                                        "frame_chain": frame_chain,
                                        "meta": {"max_items": max_items, "multi_text": True},
                                    },
                                )

                                question_blocks.append(
                                    {
                                        "question": question,
                                        "itype": itype,
                                        "options": [],
                                        "max_select": max_items,  # Ã¢Å“â€¦ 2Ã¢â‚¬â€œ3 rÃƒÂ©ponses max
                                        "target_id": multi_target_id,
                                        "context": {
                                            "kind": "multi_text",
                                            "fields_count": len(field_payloads),
                                            "max_items": max_items,
                                            "name_prefix": prefix or "",
                                        },
                                    }
                                )

                                seen_multi_text_groups.add(group_key)
                                continue
                except Exception:
                    pass

            # SpÃƒÂ©cial: plusieurs dropdowns dans le mÃƒÂªme conteneur (ex: DOB Mois/AnnÃƒÂ©e).
            # - Enrichit la question avec un sous-label (Mois/AnnÃƒÂ©e) si possible
            # - Ãƒâ€°vite de dÃƒÂ©dupliquer ÃƒÂ  tort deux <select> distincts
            if itype == "dropdown":
                try:
                    if not multi and container:
                        sels = container.find_elements(By.TAG_NAME, "select")
                        if len(sels) >= 2:
                            multi = True
                    if multi:
                        if not hint:
                            hint = _dropdown_field_hint(driver, el)
                        if hint and hint.lower() not in (question or "").lower():
                            question = _norm(f"{question} Ã¢â‚¬â€ {hint}")
                except Exception:
                    pass
            sig = (question, itype)
            if itype == "dropdown":
                try:
                    sig = (
                        question,
                        itype,
                        (el.get_attribute("name") or "").strip(),
                        (el.get_attribute("id") or "").strip(),
                    )
                except Exception:
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
            el_id = (el.get_attribute("id") or "").strip()
            el_name = (el.get_attribute("name") or "").strip()
            el_tag = (el.tag_name or "").strip().lower()

            single_key = f"{itype}:{el_id}:{el_name}"
            target_id = make_target_id("single", single_key, question)

            xpath = _best_xpath_for_element(driver, el)

            # Locators alternatifs (stables) : en pratique, @name survit aux re-render Wicket/Bootstrap-select
            alt_xpaths = []
            try:
                if el_tag and el_name:
                    alt_xpaths.append(f"//{el_tag}[@name={_xpath_literal(el_name)}]")
                elif el_name:
                    alt_xpaths.append(f"//*[@name={_xpath_literal(el_name)}]")
            except Exception:
                pass

            try:
                if el_id:
                    alt_xpaths.append(f"//*[@id='{el_id}']")
            except Exception:
                pass

            # dÃƒÂ©dup + retirer le primary xpath
            alt_xpaths = [x for x in dict.fromkeys(alt_xpaths) if x and x != xpath][:4]

            register_target(
                target_id,
                {
                    "kind": "single",
                    "itype": itype,
                    "question": question,
                    "xpath": xpath,
                    "alt_xpaths": alt_xpaths,
                    "tag": el_tag,
                    "name": el_name,
                    "id": el_id,
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

def _extract_decipher_answers_list_fallback(driver, frame_chain: List[Any]) -> List[Dict[str, Any]]:
    """
    Fallback DOM strict (Decipher/FocusVision).
    DÃƒÂ©clenchÃƒÂ© uniquement quand l'analyse standard retourne 0 block.
    Objectif: extraire (1) radio/checkbox groups dans .answers.answers-list/.clickableCell
              (2) bouton #btn_continue (input type=image)
    """
    try:
        data = driver.execute_script(
            r"""
            function norm(s){
              return (s || "").replace(/\s+/g, " ").trim();
            }

            const out = [];

            // --- 1) Groups radio/checkbox FocusVision/Decipher ---
            const qNodes = Array.from(document.querySelectorAll(
              "div.question[role='radiogroup'], div.question.radio, div.question.checkbox"
            ));

            for (const q of qNodes) {
              const answers = q.querySelector(".answers.answers-list");
              if (!answers) continue;

              const inputs = Array.from(answers.querySelectorAll("input[type='radio'], input[type='checkbox']"));
              if (inputs.length < 2) continue;

              const qtext = norm((q.querySelector(".question-text") || q).innerText);
              if (!qtext) continue;

              // group by (itype,name)
              const groups = new Map();

              for (const inp of inputs) {
                const itype = (inp.type || "").toLowerCase();
                const name = norm(inp.getAttribute("name"));
                const id = norm(inp.getAttribute("id"));
                if (!name || !id) continue;
                if (itype !== "radio" && itype !== "checkbox") continue;

                // label: prioritÃƒÂ© label[for=id]
                let label = "";
                try {
                  // id contient des '.' => OK dans un attribut [for="..."]
                  const safe = id.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
                  const labEl = answers.querySelector('label[for="' + safe + '"]');
                  label = norm(labEl ? labEl.innerText : "");
                } catch(e) {}

                // fallback: texte du wrapper cliquable
                if (!label) {
                  const cell = inp.closest(".clickableCell") || inp.closest(".sq-cardrating-button") || inp.closest(".element");
                  label = norm(cell ? cell.innerText : "");
                }
                if (!label) continue;

                const key = itype + "|" + name;
                if (!groups.has(key)) groups.set(key, { itype, name, question: qtext, options: [] });

                groups.get(key).options.push({ label, id });
              }

              // flush groups
              for (const g of groups.values()) {
                // dedupe
                const seen = new Set();
                const opts = [];
                const ids = [];
                for (const o of g.options) {
                  const k = (o.label || "").toLowerCase();
                  if (!k || seen.has(k)) continue;
                  seen.add(k);
                  opts.push(o.label);
                  ids.push(o.id);
                }
                if (opts.length >= 2) {
                  out.push({
                    kind: "group",
                    itype: g.itype,
                    name: g.name,
                    question: g.question,
                    options: opts,
                    input_ids: ids
                  });
                }
              }
            }

            // --- 2) Bouton continue Decipher ---
            const btn = document.querySelector("#btn_continue");
            if (btn && btn.id && btn.name) {
              const label = norm(
                btn.getAttribute("value")
                || btn.getAttribute("aria-label")
                || btn.getAttribute("title")
                || btn.getAttribute("alt")
                || btn.name
                || btn.id
              );
              out.push({ kind: "btn_continue", id: btn.id, name: btn.name, label });
            }

            return out;
            """
        )
    except Exception:
        return []

    if not isinstance(data, list) or not data:
        return []

    blocks: List[Dict[str, Any]] = []

    # --- Groups ---
    for g in data:
        try:
            if (g or {}).get("kind") != "group":
                continue

            itype = (g.get("itype") or "").strip().lower()
            name = (g.get("name") or "").strip()
            question = _norm(g.get("question") or "")
            options = [(_norm(x) or "") for x in (g.get("options") or [])]
            input_ids = [((x or "").strip()) for x in (g.get("input_ids") or [])]

            if itype not in ("radio", "checkbox") or not name or not question:
                continue
            if len(options) < 2 or len(input_ids) < 2:
                continue

            option_xpath_map: Dict[str, str] = {}
            clean_options: List[str] = []
            for opt_txt, inp_id in zip(options, input_ids):
                if not opt_txt or not inp_id:
                    continue
                k = _norm_lc(opt_txt)
                if not k or k in option_xpath_map:
                    continue

                # Clique le wrapper visible (Decipher/FocusVision)
                xpath_click = (
                    f"//input[@id={_xpath_literal(inp_id)}]/ancestor::*["
                    f"contains(concat(' ',normalize-space(@class),' '),' clickableCell ') "
                    f"or contains(concat(' ',normalize-space(@class),' '),' sq-cardrating-button ') "
                    f"or contains(concat(' ',normalize-space(@class),' '),' element ')"
                    f"][1]"
                )
                option_xpath_map[k] = xpath_click
                clean_options.append(opt_txt)

            if len(clean_options) < 2:
                continue

            group_key = f"{itype}:name:{name}"
            target_id = make_target_id("group", group_key, question)

            register_target(
                target_id,
                {
                    "kind": "group",
                    "itype": itype,
                    "group_key": group_key,
                    "question": question,
                    "option_xpath_map": option_xpath_map,
                    "frame_chain": frame_chain,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": itype,
                    "options": clean_options,
                    "max_select": _compute_max_select(itype, clean_options),
                    "target_id": target_id,
                    "context": {"kind": "group", "group_key": group_key},
                }
            )
        except Exception:
            continue

    # --- Continue button ---
    for b in data:
        try:
            if (b or {}).get("kind") != "btn_continue":
                continue

            btn_id = (b.get("id") or "").strip()
            btn_name = (b.get("name") or "").strip()
            label = _norm(b.get("label") or "")

            if not btn_id or not btn_name:
                continue

            question = label or "Continue"
            single_key = f"button:{btn_id}:{btn_name}"
            target_id = make_target_id("single", single_key, question)

            xpath = f"//*[@id={_xpath_literal(btn_id)}]"
            alt_xpaths = [f"//input[@name={_xpath_literal(btn_name)}]"]

            register_target(
                target_id,
                {
                    "kind": "single",
                    "itype": "button",
                    "question": question,
                    "xpath": xpath,
                    "alt_xpaths": alt_xpaths,
                    "tag": "input",
                    "name": btn_name,
                    "id": btn_id,
                    "frame_chain": frame_chain,
                },
            )

            blocks.append(
                {
                    "question": question,
                    "itype": "button",
                    "options": [],
                    "max_select": 1,
                    "target_id": target_id,
                    "context": {
                        "kind": "single",
                        "tag": "input",
                        "name": btn_name,
                        "id": btn_id,
                        "role": None,
                    },
                }
            )
        except Exception:
            continue

    return blocks

def analyze_dom(driver) -> List[Dict[str, Any]]:
    """
    Analyse le DOM et retourne une liste de QuestionBlock.
    Frame-aware: choisit automatiquement le meilleur contexte (default ou iframe) jusqu'ÃƒÂ  depth=DOM_FRAME_MAX_DEPTH (dÃƒÂ©faut=2).
    """
    dom_registry.clear_registry()

    _wait_for_survey_dom(driver)
    max_depth = int(os.getenv("DOM_FRAME_MAX_DEPTH", "2") or "2")
    best_chain, _meta = _select_best_frame_chain(driver, max_depth=max_depth)

    # Scan dans le contexte choisi; retour ÃƒÂ  default_content garanti.
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
        blocks.extend(_extract_angular_material_radio_groups(driver, frame_chain=chain))

        if not blocks:
            blocks = _extract_decipher_answers_list_fallback(driver, frame_chain=chain)

    # Fallback strict: si on a scannÃƒÂ© un iframe et qu'on n'a rien, tente default_content une seule fois.
    if not blocks and chain:
        with switch_to_frame_chain(driver, []) as ok:
            if ok:
                sp_blocks = extract_sliderpoints_question_blocks(driver)
                if sp_blocks:
                    return sp_blocks
                blocks = _analyze_dom_current_context(driver)
                blocks.extend(_extract_focusvision_answers_list_groups(driver))
                blocks.extend(_extract_angular_material_radio_groups(driver))

                if not blocks:
                    blocks = _extract_decipher_answers_list_fallback(driver, frame_chain=chain)

    return blocks