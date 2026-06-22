"""
input_dropdown.py - Gestion des dropdowns et <select> pour input_handler

Ce module contient:
- Détection et ouverture de dropdowns (natifs et custom)
- Sélection d'options dans dropdowns
- Vérification de l'état (rempli/vide)
- Support: <select>, Angular Material, MUI, Select2, jQuery UI, ARIA

Dépendances:
- input_utils pour les fonctions de normalisation et constantes
"""

def _pw_page(d):
    """Extrait la Page Playwright native depuis un PlaywrightDriverShim ou retourne d tel quel."""
    if hasattr(d, "_page"):
        return d._page
    return d


def _handle(el):
    """Extrait le ElementHandle natif depuis un PlaywrightElementShim (_h) ou retourne el."""
    if hasattr(el, "_h"):
        return el._h
    return el



import time
import re

# Import depuis input_utils
from Survey.input_utils import (
    norm_txt,
    norm_text,
    DROPDOWN_PLACEHOLDERS,
    PLACEHOLDER_TOKENS,
    similarity,
)
from Survey.log_utils import log_debug


# =============================================================================
# HELPERS DE DÉTECTION DROPDOWN
# =============================================================================

def has_native_selects(driver) -> bool:
    """Vérifie si la page contient des <select> natifs."""
    return bool(driver.find_elements("tag name", "select"))


def select_like_elements(driver):
    """
    Retourne tous les éléments qui ressemblent à des dropdowns.
    Inclut: <select>, role=combobox, aria-haspopup=listbox, .select classes.
    """
    els = []
    els += driver.find_elements("tag name", "select")
    els += driver.find_elements(
        "css selector", "[role='combobox'], [aria-haspopup='listbox']"
    )
    # togglers fréquents (custom selects)
    els += driver.find_elements(
        "xpath",
        "//*[contains(@class,'select') and (self::div or self::button or self::span)]",
    )
    # éviter les doublons
    seen, uniq = set(), []
    for e in els:
        try:
            if e._id not in seen and e.is_displayed():
                seen.add(e._id)
                uniq.append(e)
        except Exception:
            continue
    return uniq


def element_signature_text(driver, el) -> str:
    """
    Concatène tout ce qui décrit un champ dropdown (labels/aria/placeholder).
    Utilisé pour matcher le bon dropdown avec un hint.
    """
    bits = []
    try:
        # label for=…
        eid = el.get_attribute("id")
        if eid:
            try:
                lbl = driver.find_element("xpath", f"//label[@for='{eid}']")
                if lbl.text.strip():
                    bits.append(lbl.text)
            except Exception:
                pass
        # aria-label / labelledby
        a = (el.get_attribute("aria-label") or "").strip()
        if a:
            bits.append(a)
        labby = (el.get_attribute("aria-labelledby") or "").strip()
        if labby:
            for ref in labby.split():
                try:
                    n = driver.find_element("id", ref)
                    t = (n.text or n.get_attribute("innerText") or "").strip()
                    if t:
                        bits.append(t)
                except Exception:
                    continue
        # placeholder
        ph = (el.get_attribute("placeholder") or "").strip()
        if ph:
            bits.append(ph)
        # texte du conteneur question
        try:
            q = el.find_element(
                "xpath",
                "ancestor::*[contains(@class,'Question') or contains(@class,'question') or contains(@class,'body') or self::fieldset][1]",
            )
            t = (q.text or "").strip()
            if t:
                bits.append(t)
        except Exception:
            pass
    except Exception:
        pass
    sig = " ".join(bits)
    return norm_txt(sig)


def viewport_penalty(driver, el) -> float:
    """
    Pénalise les éléments dans le header/footer (sélecteur de langue, navbar...).
    Retourne un score négatif pour ces zones.
    """
    try:
        r = el.rect
        y = r.get("y", 0)
        htot = (
            driver.execute_script(
                "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, window.innerHeight);"
            )
            or 2000
        )
        # pénalise header/footer
        if y < 120:
            return -0.75
        if y > htot - 220:
            return -0.5
    except Exception:
        pass
    return 0.0


def best_dropdown_for_hint(driver, hint: str | None, context_hint: str | None = None):
    """
    Trouve le dropdown le plus pertinent selon le hint fourni.
    
    Args:
        driver: WebDriver
        hint: hint de recherche (ex: "année", "mois", "pays")
        context_hint: contexte de question optionnel
    
    Returns:
        WebElement du dropdown le plus pertinent, ou None
    """
    cands = select_like_elements(driver)
    if not cands:
        return None
    if not hint:
        return cands[0]
    
    h = norm_txt(hint)
    c = norm_txt(context_hint) if context_hint else ""
    
    # Disambiguation spécifique année/mois
    want = None
    if any(k in h for k in ("année", "annee", "year", "years")):
        want = "year"
    elif any(k in h for k in ("mois", "month", "months")):
        want = "month"

    best, best_score = None, -1e9
    for el in cands:
        try:
            sig = element_signature_text(driver, el)
            sim = similarity(h, sig)
            if want:
                try:
                    nid = norm_txt(((el.get_attribute("name") or "") + " " + (el.get_attribute("id") or "") + " " + (el.get_attribute("aria-label") or "")).strip())
                except Exception:
                    nid = ""
                try:
                    self_txt = norm_txt((el.text or el.get_attribute("innerText") or "").strip())
                except Exception:
                    self_txt = ""
                pool = f"{nid} {self_txt}"
                if want == "year":
                    if any(t in pool for t in ("année", "annee", "year", "years")):
                        sim += 0.55
                    else:
                        sim -= 0.25
                else:
                    if any(t in pool for t in ("mois", "month", "months")):
                        sim += 0.55
                    else:
                        sim -= 0.25

            # boost si le hint ressemble à des champs typiques
            if any(k in h for k in ("an", "année", "year", "mois", "month", "pays", "country", "ville", "city", "state", "province")):
                if any(k in sig for k in ("an", "année", "year", "mois", "month", "pays", "country", "ville", "city", "state", "province")):
                    if c:
                        sim += 0.35 * similarity(c, sig)
            
            score = sim + viewport_penalty(driver, el)
            if score > best_score:
                best, best_score = el, score
        except Exception:
            continue
    return best


# =============================================================================
# LECTURE DE VALEUR DROPDOWN
# =============================================================================

def dropdown_visible_value(driver, ctrl) -> str:
    """
    Lit le texte AFFICHÉ par le composant dropdown (pas l'input caché).
    Gère: <select>, Angular Material, MUI, Select2, jQuery UI.
    
    Returns:
        Texte affiché, ou "" si rien de fiable
    """
    # 1) <select> natif
    try:
        if ctrl.tag_name.lower() == "select":
            try:
                _sel_el = _handle(ctrl)  # Playwright: use .select_option() and .evaluate() for options
                if sel.first_selected_option:
                    return sel.first_selected_option.text or ""
            except Exception:
                val = ctrl.get_attribute("value") or ""
                if val:
                    try:
                        opt = ctrl.find_element("xpath", f".//option[@value={repr(val)}]")
                        return opt["text"] or val
                    except Exception:
                        return val
    except Exception:
        pass

    # 2) MatSelect (Angular Material)
    for xp in [
        ".//div[contains(@class,'mat-select-value')]/span[contains(@class,'mat-select-value-text')]",
        ".//span[contains(@class,'mat-select-value-text')]",
    ]:
        try:
            el = ctrl.find_element("xpath", xp)
            txt = (el.text or el.get_attribute("innerText") or "").strip()
            if txt:
                return txt
        except Exception:
            pass

    # 3) MUI (Material-UI)
    for xp in [
        ".//*[contains(@class,'MuiSelect-select') and not(contains(@class,'MuiSelect-nativeInput'))]",
        ".//*[contains(@class,'MuiSelect-select') and @role='button']",
    ]:
        try:
            el = ctrl.find_element("xpath", xp)
            txt = (el.text or el.get_attribute("innerText") or "").strip()
            if txt:
                return txt
        except Exception:
            pass

    # 4) Select2
    for xp in [".//span[contains(@class,'select2-selection__rendered')]"]:
        try:
            el = ctrl.find_element("xpath", xp)
            txt = (el.get_attribute("title") or el.text or "").strip()
            if txt:
                return txt
        except Exception:
            pass

    # 5) jQuery UI / mobiles / ARIA button-like
    try:
        btn = ctrl
        if btn.get_attribute("role") != "button":
            btn = ctrl.find_element("xpath", ".//*[@role='button' or @aria-haspopup='listbox']")
        txt = (btn.text or btn.get_attribute("innerText") or "").strip()
        if txt:
            return txt
    except Exception:
        pass

    # 6) fallback: texte direct du contrôle
    try:
        txt = (ctrl.text or ctrl.get_attribute("innerText") or "").strip()
        return txt
    except Exception:
        return ""


def is_dropdown_filled(driver, ctrl) -> bool:
    """
    True si la valeur affichée semble une vraie valeur (≠ placeholder/vide).
    Gère <select> natif et la plupart des rendus UI.
    """
    # cas natif <select>
    try:
        if ctrl.tag_name.lower() == "select":
            val = ctrl.get_attribute("value") or ""
            if val and norm_txt(val) not in DROPDOWN_PLACEHOLDERS:
                return True
            try:
                _sel_el = _handle(ctrl)  # Playwright: use .select_option() and .evaluate() for options
                txt = norm_txt(sel.first_selected_option.text or "")
                return bool(txt and txt not in DROPDOWN_PLACEHOLDERS)
            except Exception:
                return False
    except Exception:
        pass

    # texte rendu par le composant
    txt = norm_txt(dropdown_visible_value(driver, ctrl))
    if not txt:
        return False

    # placeholders les + fréquents
    if txt in DROPDOWN_PLACEHOLDERS:
        return False

    # phrases usuelles
    for bad in ("veuillez", "please", "select", "sélectionner", "selectionner", "choose"):
        if txt.startswith(bad):
            return False

    return True


# =============================================================================
# OUVERTURE DE DROPDOWN
# =============================================================================

def open_first_dropdown(driver) -> bool:
    """
    Ouvre un dropdown visible (natif <select> ou custom role=combobox / bouton).
    Ne sélectionne pas d'option ici ; juste « abaisser » le menu.
    """
    # 1) <select> natif
    selects = driver.find_elements("tag name", "select")
    for s in selects:
        try:
            if s.is_displayed():
                _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(s))
                time.sleep(0.1)
                s.click()
                print("🔒 Dropdown (natif) ouvert... source: input_dropdown.py")
                return True
        except Exception:
            continue

    # 2) Dropdowns customs
    togglers = []
    togglers += driver.find_elements("css selector", "[role='combobox']")
    togglers += driver.find_elements("css selector", "[aria-haspopup='listbox']")
    togglers += driver.find_elements(
        "xpath",
        "//*[contains(@class,'select') and (self::div or self::button or self::span)]",
    )
    for t in togglers:
        try:
            if t.is_displayed() and t.rect.get("width", 0) > 20 and t.rect.get("height", 0) > 15:
                _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(t))
                time.sleep(0.1)
                try:
                    t.click()
                except Exception:
                    _handle(t).hover(); _handle(t).click()
                time.sleep(0.2)
                print("🔒 Dropdown (custom) ouvert. source: input_dropdown.py")
                return True
        except Exception:
            continue

    print("❌ Aucun dropdown à ouvrir. source: input_dropdown.py")
    return False


def open_dropdown_generic(driver, hint: str | None = None, context_hint: str | None = None) -> bool:
    """
    Ouvre le dropdown le plus pertinent selon le hint.
    Marque driver._ui_overlay_opened pour suivi.
    
    Args:
        driver: WebDriver
        hint: indice pour trouver le bon dropdown
        context_hint: contexte de question optionnel
    
    Returns:
        True si ouverture réussie
    """
    el = best_dropdown_for_hint(driver, hint, context_hint=context_hint)
    if not el:
        print("❌ Aucun dropdown à ouvrir. source: input_dropdown.py")
        return False
    
    # Les <select> natifs ne doivent pas être "ouverts" ici.
    # Leur sélection se fait directement dans select_option_with_hint().
    # Cliquer/focuser un select natif puis envoyer ARROW_DOWN peut modifier
    # une valeur déjà correcte avant la vraie sélection.
    if el.tag_name.lower() == "select":
        try:
            try:
                already_filled = is_dropdown_filled(driver, el)
            except Exception:
                already_filled = False

            _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(el))
            try:
                driver._ui_overlay_opened = {
                    "type": "dropdown",
                    "native": True,
                    "hint": hint or "",
                    "ts": time.time(),
                    "anchor": el,
                    "filled": already_filled
                }
                driver._last_dropdown_hint = hint or ""
            except Exception:
                pass
            print("ℹ️ Dropdown natif repéré sans ouverture; sélection directe attendue. source: input_dropdown.py")
            return True
        except Exception:
            print("⚠️ Select natif ciblé: ouverture impossible → on continuera par sélection directe. source: input_dropdown.py")
            return True

    try:
        try:
            already_filled = is_dropdown_filled(driver, el)
        except Exception:
            already_filled = False

        _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(el))
        try:
            el.click()
        except Exception:
            _handle(el).hover(); _handle(el).click()
        time.sleep(0.05)
        try:
            _pw_page(driver).keyboard.press("ArrowDown")
        except Exception:
            pass
        try:
            driver._ui_overlay_opened = {
                "type": "dropdown",
                "native": False,
                "hint": hint or "",
                "ts": time.time(),
                "anchor": el,
                "filled": already_filled
            }
            driver._last_dropdown_hint = hint or ""
        except Exception:
            pass
        print("🔒 Dropdown (custom) ouvert. source: input_dropdown.py")
        return True
    except Exception:
        print("❌ Échec à l'ouverture du dropdown ciblé. source: input_dropdown.py")
        return False


# =============================================================================
# SÉLECTION D'OPTIONS
# =============================================================================

def select_option_with_hint(driver, option_text: str, field_hint: str | None = None, context_hint: str | None = None) -> bool:
    """
    Tente de sélectionner 'option_text' si un <select> est présent
    ou si un menu custom est ouvert (ul/li, role=option...).
    
    Version basique sans hint de champ.
    """
    target = norm_txt(option_text)

    # Ne pas "sélectionner" un placeholder
    if target in {"mois", "année", "annee", "month", "year"}:
        print(f"⚠️ Valeur placeholder ignorée: '{option_text}'. source: input_dropdown.py")
        return False

    # A) <select> natif
    selects = driver.find_elements("tag name", "select")
    for s in selects:
        try:
            # Sélection via JS (robuste même si <select> hidden / bootstrap-select)
            ok_js = False
            if target:
                try:
                    ok_js = bool(driver.execute_script(
                        """
                        const sel = arguments[0];
                        const target = arguments[1];
                        if (!sel || !_pw_page(driver).evaluate("el => Array.from(el.options).map(o => ({value:o.value,text:o.text.trim()}))", _sel_el)) return false;

                        const norm = (x) => (x || "").toString().trim().toLowerCase();
                        const tgt = norm(target);

                        let found = null;
                        for (const opt of _pw_page(driver).evaluate("el => Array.from(el.options).map(o => ({value:o.value,text:o.text.trim()}))", _sel_el)) {
                            const t = norm(opt.textContent);
                            if (!t) continue;
                            if (t === tgt || t.includes(tgt)) { found = opt; break; }
                        }
                        if (!found) return false;

                        sel.value = found.value;
                        found.selected = true;

                        try { sel.dispatchEvent(new Event('input', {bubbles:true})); } catch(e){}
                        try { sel.dispatchEvent(new Event('change',{bubbles:true})); } catch(e){}
                        try { sel.dispatchEvent(new Event('blur',  {bubbles:true})); } catch(e){}

                        try {
                          if (window.jQuery && window.jQuery(sel).selectpicker) {
                            window.jQuery(sel).selectpicker('refresh');
                          }
                        } catch(e){}

                        return true;
                        """,
                        s, target
                    ))
                except Exception:
                    ok_js = False

            if ok_js:
                print(f"✓ Option sélectionnée (JS/select) : {option_text}. source: input_dropdown.py")
                try:
                    driver._ui_overlay_opened = None
                except Exception:
                    pass
                return True

            # fallback Selenium Select
            _sel_el = _handle(s)  # Playwright: use .select_option() and .evaluate() for options
            for opt in _pw_page(driver).evaluate("el => Array.from(el.options).map(o => ({value:o.value,text:o.text.trim()}))", _sel_el):
                ot = norm_txt(opt["text"])
                if target and (target == ot or target in ot):
                    _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(s))
                    time.sleep(0.1)
                    try:
                        _sel_el.select_option(label=opt["text"])
                    except Exception:
                        if opt.get_attribute("value"):
                            _sel_el.select_option(value=opt.get_attribute("value"))
                        else:
                            opt.click()

                    driver.execute_script("""
                      const s = arguments[0];
                      try { s.dispatchEvent(new Event('input', {bubbles:true})); } catch(e){}
                      try { s.dispatchEvent(new Event('change',{bubbles:true})); } catch(e){}
                      try { s.dispatchEvent(new Event('blur',  {bubbles:true})); } catch(e){}
                    """, s)
                    print(f"✓ Option sélectionnée (natif) : {opt['text']}. source: input_dropdown.py")
                    try:
                        driver._ui_overlay_opened = None
                    except:
                        pass
                    return True
            # match value
            for opt in _pw_page(driver).evaluate("el => Array.from(el.options).map(o => ({value:o.value,text:o.text.trim()}))", _sel_el):
                ov = norm_txt(opt.get_attribute("value") or "")
                if target and target == ov:
                    _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(s))
                    time.sleep(0.1)
                    _sel_el.select_option(value=opt.get_attribute("value"))
                    print(f"✓ Option sélectionnée (natif/value) : {opt.get_attribute('value')}. source: input_dropdown.py")
                    return True
        except Exception:
            continue

    # B) Dropdown custom : suppose menu déjà ouvert
    candidates = []
    candidates += driver.find_elements("xpath", "//li[normalize-space(.)!='']")
    candidates += driver.find_elements("css selector", "[role='option']")
    candidates += driver.find_elements(
        "xpath", "//*[contains(@class,'option') and normalize-space(text())!='']"
    )
    for c in candidates:
        try:
            txt = norm_txt(c.get_attribute("innerText") or c.text)
            if not txt:
                continue
            if target and (target == txt or target in txt):
                _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(c))
                time.sleep(0.1)
                c.click()
                time.sleep(0.2)
                print(f"✓ Option sélectionnée (custom) : {option_text}. source: input_dropdown.py")
                try:
                    driver._ui_overlay_opened = None
                except:
                    pass
                return True
        except Exception:
            continue

    print(f"❌ Option '{option_text}' introuvable dans dropdown. source: input_dropdown.py")
    return False


def select_option_with_hint(
    driver, 
    option_text: str, 
    field_hint: str | None = None, 
    context_hint: str | None = None
) -> bool:
    """
    Sélectionne option_text dans le dropdown le plus pertinent en un seul enchaînement.
    - <select> natif: sélection directe (pas d'ouverture).
    - dropdown custom: ouvre puis sélectionne tout de suite.
    - 2 tentatives max si le menu se referme.
    
    Args:
        driver: WebDriver
        option_text: texte de l'option à sélectionner
        field_hint: hint pour identifier le dropdown
        context_hint: contexte de question optionnel
    
    Returns:
        True si sélection réussie
    """
    target = norm_txt(option_text)

    def _pick_matching_option(options, target_text: str):
        """
        Retourne la meilleure option selon une stratégie simple et robuste:
        1) match exact sur texte normalisé ou value normalisée,
        2) sinon match partiel uniquement (target inclus dans texte option).
        """
        if not target_text:
            return None

        partial_candidate = None
        for opt in options:
            try:
                ot = norm_txt(opt["text"])
                ov = norm_txt(opt.get_attribute("value") or "")
            except Exception:
                continue

            if target_text == ot or target_text == ov:
                return opt

            if partial_candidate is None and ot and target_text in ot:
                partial_candidate = opt

        return partial_candidate

    # Disambiguation robuste mois/année
    _MONTHS_FR = {
        "janvier", "février", "fevrier", "mars", "avril", "mai", "juin",
        "juillet", "août", "aout", "septembre", "octobre", "novembre", "décembre", "decembre",
    }

    def _forced_hint_from_value(val_norm: str) -> str | None:
        if not val_norm:
            return None
        if val_norm in _MONTHS_FR:
            return "mois"
        if re.fullmatch(r"\d{4}", val_norm):
            try:
                y = int(val_norm)
                if 1900 <= y <= 2100:
                    return "année"
            except Exception:
                pass
        return None

    forced_hint = _forced_hint_from_value(target)
    effective_hint = forced_hint or field_hint or option_text

    def _select_bootstrap_option(anchor_el, wanted_text: str) -> bool:
        """Sélection stricte d'une option bootstrap-select via l'ancre <a> de menu ouvert."""
        try:
            aid = (anchor_el.get_attribute("id") or "").strip()
        except Exception:
            aid = ""
        if not aid:
            return False

        try:
            menu_anchors = driver.find_elements(
                "xpath",
                (
                    "//button[@data-id=" + repr(aid) + "]"
                    "/following-sibling::div[contains(@class,'dropdown-menu') and contains(@class,'open')]"
                    "//ul[contains(@class,'dropdown-menu') and contains(@class,'inner')]"
                    "//li[not(contains(@class,'disabled'))]/a"
                ),
            )
        except Exception:
            menu_anchors = []

        for a in menu_anchors:
            try:
                txt = norm_txt(a.get_attribute("innerText") or a.text)
                if not txt:
                    continue
                if wanted_text == txt or wanted_text in txt:
                    _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:'center'})", _handle(a))
                    a.click()
                    print(f"✓ Option sélectionnée (bootstrap-select) : {option_text}. source: input_dropdown.py")
                    try:
                        driver._ui_overlay_opened = None
                    except Exception:
                        pass
                    return True
            except Exception:
                continue

        return False

    # --- NATIF <select>: sélection directe (sans ouvrir)
    selects = driver.find_elements("tag name", "select")
    if selects:
        s = best_dropdown_for_hint(driver, effective_hint, context_hint=context_hint)
        try_selects = []
        if s is not None:
            try_selects.append(s)
        try_selects += [el for el in selects if (s is None or getattr(el, "_id", id(el)) != getattr(s, "_id", id(s)))]
        
        for sel_el in try_selects:
            try:
                # GfK mrIWeb: .mrDropdown in .platform_clone — Selenium Select won't
                # trigger Angular $digest; must click .cb_el then .cb_item_row.
                sel_classes = (sel_el.get_attribute("class") or "").lower()
                if "mrdropdown" in sel_classes:
                    # Locate .combo_master: primary — via .acc_ct ancestor; fallback — preceding-sibling of .platform_clone
                    combo_masters = sel_el.find_elements(
                        "xpath",
                        "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' acc_ct ')][1]"
                        "/div[contains(concat(' ',normalize-space(@class),' '),' combo_master ')]",
                    )
                    log_debug("gfk-combo", f"combo_masters via acc_ct: {len(combo_masters)}")
                    if not combo_masters:
                        combo_masters = sel_el.find_elements(
                            "xpath",
                            "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' platform_clone ')][1]"
                            "/preceding-sibling::div[contains(concat(' ',normalize-space(@class),' '),' combo_master ')][1]",
                        )
                        log_debug("gfk-combo", f"combo_masters via preceding-sibling: {len(combo_masters)}")
                    if combo_masters:
                        cm = combo_masters[0]
                        cb_els = cm.find_elements("css selector", ".cb_el")
                        if cb_els:
                            _pw_page(driver).evaluate("(el) => el.click()", _handle(cb_els[0]))
                            # Wait for .b_l_ct (Angular ng-show) to become visible
                            deadline = time.time() + 2.0
                            cb_list = None
                            while time.time() < deadline:
                                b_l_cts = cm.find_elements("css selector", ".b_l_ct")
                                if b_l_cts and b_l_cts[0].is_displayed():
                                    lists = b_l_cts[0].find_elements("css selector", ".cb_list")
                                    if lists:
                                        cb_list = lists[0]
                                        break
                                time.sleep(0.05)
                            # Fallback: try button.combo_button if .b_l_ct still hidden
                            if cb_list is None:
                                btns = cb_els[0].find_elements("css selector", "button.combo_button")
                                if btns:
                                    _pw_page(driver).evaluate("(el) => el.click()", _handle(btns[0]))
                                    deadline2 = time.time() + 2.0
                                    while time.time() < deadline2:
                                        b_l_cts = cm.find_elements("css selector", ".b_l_ct")
                                        if b_l_cts and b_l_cts[0].is_displayed():
                                            lists = b_l_cts[0].find_elements("css selector", ".cb_list")
                                            if lists:
                                                cb_list = lists[0]
                                                break
                                        time.sleep(0.05)
                            if cb_list:
                                for row in cb_list.find_elements("css selector", ".cb_item_row"):
                                    try:
                                        items = row.find_elements("css selector", ".cb_item")
                                        if not items:
                                            continue
                                        txt = norm_txt(items[0].get_attribute("innerText") or items[0].text)
                                        if target == txt or (target and target in txt):
                                            _pw_page(driver).evaluate("(el) => el.click()", _handle(row))
                                            print(f"✓ Option sélectionnée (gfk-combo) : {option_text}. source: input_dropdown.py")
                                            try:
                                                driver._ui_overlay_opened = None
                                            except Exception:
                                                pass
                                            return True
                                    except Exception:
                                        continue
                            log_debug("gfk-combo", f"cb_list not visible or option '{option_text}' not found")
                    # mrDropdown detected but selection failed — don't fall through to Selenium Select
                    continue

                _sel_el = _handle(sel_el)  # Playwright: use .select_option() and .evaluate() for options
                opt = _pick_matching_option(_pw_page(driver).evaluate("el => Array.from(el.options).map(o => ({value:o.value,text:o.text.trim()}))", _sel_el), target)
                if not opt:
                    continue

                _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(sel_el))
                try:
                    _sel_el.select_option(label=opt["text"])
                except Exception:
                    if opt.get_attribute("value"):
                        S.select_by_value(opt.get_attribute("value"))
                    else:
                        opt.click()

                try:
                    driver.execute_script("""
                      const s = arguments[0];
                      try { s.dispatchEvent(new Event('input', {bubbles:true})); } catch(e){}
                      try { s.dispatchEvent(new Event('change',{bubbles:true})); } catch(e){}
                      try { s.dispatchEvent(new Event('blur',  {bubbles:true})); } catch(e){}
                      try { s.dispatchEvent(new Event('focusout',{bubbles:true})); } catch(e){}
                    """, sel_el)
                except Exception:
                    pass

                print(f"✓ Option sélectionnée (natif) : {opt['text']}. source: input_dropdown.py")
                try:
                    driver._ui_overlay_opened = None
                except Exception:
                    pass
                return True
            except Exception:
                continue

    # --- CUSTOM: ouvrir puis sélectionner (avec retries)
    for attempt in range(2):
        opened = open_dropdown_generic(driver, hint=effective_hint, context_hint=context_hint)

        if opened:
            try:
                ov = getattr(driver, "_ui_overlay_opened", None) or {}
                anchor = ov.get("anchor")
                if anchor is not None:
                    anchor_cls = norm_txt(anchor.get_attribute("class") or "")
                    if anchor.tag_name.lower() == "select" and ("bs-select-hidden" in anchor_cls or "bootstrap-select" in anchor_cls):
                        if _select_bootstrap_option(anchor, target):
                            return True
            except Exception:
                pass
        
        # Si aucune option à appliquer et champ déjà rempli, skip
        if not option_text:
            try:
                ov = getattr(driver, "_ui_overlay_opened", None)
                if ov and ov.get("type") == "dropdown" and ov.get("filled") is True:
                    print("✓ Dropdown déjà rempli, on ne modifie pas. source: input_dropdown.py")
                    try:
                        driver._ui_overlay_opened = None
                    except Exception:
                        pass
                    return True
            except Exception:
                pass

        # Rechercher des options visibles
        deadline = time.time() + 1.0
        while time.time() < deadline:
            candidates = []
            candidates += driver.find_elements("css selector", "[role='option']")
            candidates += driver.find_elements("xpath", "//li[normalize-space(.)!='']")
            candidates += driver.find_elements(
                "xpath",
                "//*[contains(@class,'option') and normalize-space(text())!='']",
            )

            for c in candidates:
                try:
                    if not c.is_displayed():
                        continue
                    txt = norm_txt(c.get_attribute("innerText") or c.text)
                    if target and (target == txt or target in txt):
                        _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(c))
                        c.click()
                        print(f"✓ Option sélectionnée (custom) : {option_text}. source: input_dropdown.py")
                        try:
                            driver._ui_overlay_opened = None
                        except Exception:
                            pass
                        return True
                except Exception:
                    continue
            time.sleep(0.05)

        print("↻ Menu refermé / option non visible, nouvelle tentative… source: input_dropdown.py")

    print(f"❌ Option '{option_text}' introuvable dans dropdown. source: input_dropdown.py")
    return False
