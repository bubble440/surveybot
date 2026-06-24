"""
input_text.py - Gestion des champs texte pour input_handler

Ce module contient:
- Saisie dans input/textarea/contenteditable
- Gestion des champs numériques
- Gestion des champs date (month/day/year)
- Patches spécifiques (Swagbucks, PureSpectrum captcha)
- Fallbacks multiples (send_keys, ActionChains, CDP, JS)

Dépendances:
- input_utils pour les fonctions utilitaires
"""






import re
import time

# Import depuis input_utils
from Survey.input_utils import (
    norm_txt,
    set_input_value_with_events,
    find_inputs_by_hint,
    find_context_container,
    DATE_HINTS,
)


# =============================================================================
# HELPERS DE SAISIE
# =============================================================================

def type_via_cdp(driver, text: str):
    """
    Frappe via Chrome DevTools Protocol (événements clavier natifs).
    Plus robuste que send_keys sur certains frameworks.
    """
    for ch in text:
        try:
            driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                "type": "keyDown", "text": ch, "unmodifiedText": ch
            })
            driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                "type": "keyUp", "text": ch, "unmodifiedText": ch
            })
        except Exception:
            pass


def react_set_value_and_fire(driver, el, value: str):
    """
    Pose la valeur via le setter natif (React/PRDG friendly) puis déclenche
    les événements que ces frameworks attendent.
    """
    try:
        driver.evaluate("""([el, v]) => {
            const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value") || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
            if (d && d.set) { d.set.call(el, v); } else { el.value = v; }
            try { el.dispatchEvent(new Event("input", {bubbles:true})); } catch(e){}
            try { el.dispatchEvent(new Event("change", {bubbles:true})); } catch(e){}
        }""", [el, value])
        return True
    except Exception:
        return False


def is_numeric_field(el) -> bool:
    """Détecte si un champ est numérique (type, inputmode, pattern)."""
    t = (el.get_attribute("type") or "").lower()
    im = (el.get_attribute("inputmode") or "").lower()
    pattern = el.get_attribute("pattern") or ""
    return (
        t in ("number", "tel")
        or im in ("numeric", "decimal")
        or bool(re.search(r"\d", pattern))
    )


# =============================================================================
# PATCHES SPÉCIFIQUES
# =============================================================================

def swagbucks_zip_patch(driver, value: str) -> bool:
    """
    Patch ciblé Swagbucks (champ zip):
    - cible #profilerNumericInput
    - clear + saisie "humaine" (CDP) + events JS
    - lève le 'disabled' sur le bouton Continue et clique
    """
    try:
        el = driver.query_selector("#profilerNumericInput")
        if el is None:
            return False  # not Swagbucks
    except Exception:
        return False

    # normalise: on ne garde que des chiffres si dispo
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        digits = value or "95000"

    try:
        driver.evaluate("(el) => el.scrollIntoView({block:\'center\'})", el)
        time.sleep(0.05)
        try:
            el.click()
        except Exception:
            el.hover(); el.click()
        # clear
        try:
            driver.keyboard.press("Control+a")
            driver.keyboard.press("Delete")
        except Exception:
            pass

        # 1) Frappe simulée via CDP : un premier caractère pour lever 'disabled'
        first = (digits or value or "9")[0]
        try:
            driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyDown", "text": first, "unmodifiedText": first})
            driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyUp", "text": first, "unmodifiedText": first})
            time.sleep(0.05)
        except Exception:
            try:
                el.send_keys(first)
            except Exception:
                pass

        # 2) Pose de la valeur complète via setter natif + events
        react_set_value_and_fire(driver, el, digits or value or "95000")

        # 3) Tentative de lever 'disabled'
        driver.evaluate("""() => { const btn = document.querySelector('button#profilerSubmit, button.profilerSubmit, button[id*="profilerSubmit"]'); if (btn) { try { btn.removeAttribute('disabled'); } catch(e){} } }""")
        time.sleep(0.15)

        # 4) Clique "Continue"
        try:
            # Import dynamique pour éviter circular import
            from Survey.input_frame import click_cta_strong_any_context
            if click_cta_strong_any_context(driver, "continue"):
                return True
        except Exception:
            pass
        
        try:
            btn = driver.find_element("css selector", "button#profilerSubmit, button.profilerSubmit, button[id*='profilerSubmit']")
            driver.evaluate("(el) => el.scrollIntoView({block:\'center\'})", btn)
            try:
                btn.click()
            except Exception:
                btn.hover(); btn.click()
            time.sleep(0.2)
        except Exception:
            pass

        # Vérification finale de la valeur
        cur = el.get_attribute("value") or ""
        return cur.strip() == digits
    except Exception:
        return False


# =============================================================================
# FONCTION PRINCIPALE DE SAISIE TEXTE
# =============================================================================

def fill_text_input(driver, text: str, context_hint: str | None = None, element_id: str | None = None) -> bool:
    """
    Saisie fiable dans input/textarea/contenteditable :
    - scroll+focus
    - clear (CTRL+A, DELETE)
    - filtrage chiffres si le champ est numérique
    - fallback JS (dispatch 'input' & 'change')
    - petit 'nudge' clavier pour React/Angular
    
    Args:
        driver: WebDriver
        text: texte à saisir
        context_hint: contexte de question pour scoping
    
    Returns:
        True si saisie réussie
    """

    # Champ texte générique
    selector = "input[type='text'], input[type='search'], input[type='number'], textarea, [contenteditable='true'], input[type='textarea']"
    field = None
    scope = find_context_container(driver, context_hint)
    print(f"fill_text_input: scope hint='{context_hint}' -> {('none' if scope is None else scope.tag_name)}")

    # --- Voie rapide captcha PureSpectrum ---------------------------------
    try:
        ctx_lc = (context_hint or "").lower()
        has_pscaptcha = bool(driver.find_elements("id", "pscaptcha"))
        if has_pscaptcha or ("captcha" in ctx_lc or "taper le code" in ctx_lc or "code ci-dessus" in ctx_lc or "recop" in ctx_lc):
            try:
                scope = driver.find_element("xpath", "//*[@id='pscaptcha']/ancestor::*[self::h5 or self::div or self::section][1]")
            except Exception:
                try:
                    scope = driver.find_element(
                        "xpath",
                        "//h5[contains(@class,'covered-if')][contains(translate(normalize-space(.),"
                        " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'taper le code')]/ancestor::*[self::h5 or self::div or self::section][1]"
                    )
                except Exception:
                    pass

            if scope is not None:
                try:
                    field = scope.find_element("xpath",
                        ".//input[contains(translate(@ng-change,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'handlepscaptcha') "
                        " or starts-with(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'ans')]"
                    )
                except Exception:
                    try:
                        field = scope.find_element("xpath", ".//textarea | .//input[@type='textarea']")
                    except Exception:
                        pass
    except Exception:
        pass

    if scope is not None:
        try:
            cands = [e for e in scope.find_elements("css selector", selector) if e.is_displayed()]

            def _score_input(el):
                s = 0
                tag = (el.tag_name or "").lower()
                if tag in ("input", "textarea"):
                    s += 2
                typ = (el.get_attribute("type") or "").lower()
                if typ in ("number", "tel"):
                    s += 2
                im = (el.get_attribute("inputmode") or "").lower()
                if im in ("numeric", "decimal"):
                    s += 2
                aid = (el.get_attribute("id") or "").lower()
                name = (el.get_attribute("name") or "").lower()
                ph = ((el.get_attribute("placeholder") or "") + " " + (el.get_attribute("aria-label") or "")).lower()
                if any(k in ph for k in ("postal", "zip")):
                    s += 3
                if "profilernumericinput" in aid:
                    s += 10
                if "profiler" in aid or "profiler" in name:
                    s += 1
                ngc = (el.get_attribute("ng-change") or "").lower()
                if "handlepscaptcha" in ngc:
                    s += 8
                if aid.startswith("ans"):
                    s += 2
                if typ == "textarea":
                    s += 2
                return s

            if cands:
                cands.sort(key=_score_input, reverse=True)
                field = cands[0]
            else:
                field = None

            print(f"fill_text_input: champ trouvé dans le scope -> {('none' if field is None else field.tag_name)}")
        except Exception:
            field = None

        # --- Bloc date-triplet (Month/Day/Year) --------------------------------
        kind = (context_hint or "").strip().lower()
        lbl = norm_txt(text or "")

        if kind in ("month", "day", "year") or lbl.isdigit():
            targets = []
            if kind in ("month", "day", "year"):
                targets = find_inputs_by_hint(driver, kind)
            else:
                if len(lbl) == 4:
                    targets = find_inputs_by_hint(driver, "year")
                    kind = "year"
                elif len(lbl) <= 2:
                    m = find_inputs_by_hint(driver, "month")
                    d = find_inputs_by_hint(driver, "day")
                    y = find_inputs_by_hint(driver, "year")
                    if m and d and y:
                        targets = find_inputs_by_hint(driver, "month") if kind == "month" or not kind else find_inputs_by_hint(driver, kind)
                    else:
                        targets = m or d
                        kind = "month" if m else "day"

            if targets:
                el = None
                for t in targets:
                    try:
                        if t.is_displayed() and t.is_enabled():
                            el = t
                            break
                    except Exception:
                        continue

                if el is not None:
                    raw = "".join(ch for ch in lbl if ch.isdigit())
                    limit = DATE_HINTS.get(kind, {}).get("maxlen", None)
                    if limit:
                        raw = raw[:limit]
                        if kind in ("month", "day") and len(raw) == 1:
                            raw = "0" + raw
                    set_input_value_with_events(driver, el, raw if raw else lbl)
                    return True

    if field is None and element_id:
        try:
            field = driver.find_element("id", element_id)
        except Exception:
            try:
                field = driver.find_element("name", element_id)
            except Exception:
                pass

    if field is None:
        field = driver.wait_for_selector(selector, state='attached', timeout=10_000)

    # Cas particulier "code postal" / ZIP
    try:
        ctx_lc = (context_hint or "").lower()
        ph_lc = " ".join([
            (field.get_attribute("placeholder") or ""),
            (field.get_attribute("aria-label") or ""),
            (field.get_attribute("name") or ""),
            (field.get_attribute("id") or ""),
        ]).lower()

        is_zip_ctx = (
            any(k in ctx_lc for k in ("postal", "zip", "code postal"))
            or any(k in ph_lc for k in ("postal", "zip", "zipcode", "codepostal"))
        )

        is_swagbucks = False
        try:
            is_swagbucks = bool(driver.find_elements("id", "profilerNumericInput"))
        except Exception:
            pass

        raw_value = re.sub(r"\s+", " ", (text or "")).strip()
        digits_only = re.sub(r"\D", "", raw_value)

        if is_zip_ctx:
            placeholder_digits = re.sub(r"\D", "", field.get_attribute("placeholder") or "")
            fr_hint = ("code postal" in ctx_lc) or ("for fr" in ctx_lc) or ("france" in ctx_lc)
            safe_zip = "75001" if fr_hint else "10001"

            suspicious = (
                digits_only in ("12345", "00000", "99999")
                or (placeholder_digits and digits_only == placeholder_digits)
            )
            if suspicious:
                digits_only = safe_zip

            try:
                mx = int((field.get_attribute("maxlength") or "").strip() or 0)
            except Exception:
                mx = 0
            if mx and len(digits_only) > mx:
                digits_only = digits_only[:mx]

            text = digits_only or raw_value

        if is_swagbucks:
            print(f"[ZIP] ctx='{context_hint}' swag=True -> trying swagbucks patch")
            if swagbucks_zip_patch(driver, digits_only or raw_value):
                return True

    except Exception:
        pass

    # Mise au centre + clic fiable
    try:
        print("Scroll to field")
        driver.evaluate("(el) => el.scrollIntoView({block:\'center\'})", field)
    except Exception:
        pass
    try:
        print("Click field")
        field.click()
    except Exception:
        field.hover(); field.click()

    # Nettoyage du champ
    try:
        print("Clear field")
        driver.keyboard.press("Control+a")
        driver.keyboard.press("Delete")
    except Exception:
        pass

    value = re.sub(r"\s+", " ", text).strip()
    if is_numeric_field(field):
        print("[NUM] champ numérique détecté")
        digits = re.sub(r"\D", "", value)
        if digits:
            value = digits

    # Saisie clavier
    try:
        print("Saisie via send_keys()")
        field.send_keys(value)
    except Exception:
        pass

    # Vérifier
    current = field.get_attribute("value") or field.get_attribute("textContent") or ""
    if current.strip() != value:
        # Tentative B : frappe char-par-char avec ActionChains
        try:
            print("Saisie via ActionChains")
            field.hover(); field.click()
            driver.keyboard.press("Control+a")
            driver.keyboard.press("Delete")
            for ch in value:
                driver.keyboard.type(ch)
        except Exception:
            pass

        current = field.get_attribute("value") or field.get_attribute("textContent") or ""

    # [NUM fallback 1]
    if is_numeric_field(field):
        only_digits = re.sub(r"\D", "", value)
        if only_digits and only_digits != current.strip():
            try:
                print("[NUM] resaisie avec chiffres seulement")
                field.hover(); field.click()
                driver.keyboard.press("Control+a")
                driver.keyboard.press("Delete")
                field.send_keys(only_digits)
            except Exception:
                pass
            current = field.get_attribute("value") or field.get_attribute("textContent") or ""

    if current.strip() != value:
        # Tentative C : frappe via CDP
        try:
            print("Saisie via CDP")
            field.hover(); field.click()
            driver.keyboard.press("Control+a")
            driver.keyboard.press("Delete")
            type_via_cdp(driver, value)
        except Exception:
            pass

        current = field.get_attribute("value") or field.get_attribute("textContent") or ""

    if current.strip() != value:
        # Fallback JS + events (React/Angular)
        driver.evaluate("""([el, v]) => { if (el.isContentEditable) { el.textContent = v; } else { el.value = v; } el.dispatchEvent(new Event("input",{bubbles:true})); el.dispatchEvent(new Event("change",{bubbles:true})); }""", [field, value])
        # Petit "nudge" pour forcer la MAJ contrôlée
        try:
            print("Petit nudge clavier")
            field.send_keys(" ")
            driver.keyboard.press("Backspace")
        except Exception:
            pass

    # [NUM fallback 2]
    current = field.get_attribute("value") or field.get_attribute("textContent") or ""
    if is_numeric_field(field) and current.strip() != re.sub(r"\D", "", value):
        print("[NUM] patch JS digits-only")
        digits = re.sub(r"\D", "", value)
        driver.evaluate("""([el, v]) => { if (el) { el.value = v; el.setAttribute("value", v); el.dispatchEvent(new Event("input",{bubbles:true})); el.dispatchEvent(new Event("change",{bubbles:true})); el.dispatchEvent(new Event("blur",{bubbles:true})); } }""", [field, digits])
        time.sleep(0.3)

    # Re-lecture finale
    current = field.get_attribute("value") or field.get_attribute("textContent") or ""

    # Dernier filet (numérique) : setter natif + événements
    if is_numeric_field(field) and (current.strip() != re.sub(r"\D", "", value)):
        try:
            react_set_value_and_fire(driver, field, re.sub(r"\D", "", value))
            time.sleep(0.15)
            current = field.get_attribute("value") or field.get_attribute("textContent") or ""
        except Exception:
            pass

    # PATCH spécifique Swagbucks : champ postal profilerNumericInput
    if current.strip() != value:
        try:
            print("[SWAG] tentative patch JS spécifique Swagbucks")
            special = driver.find_element("id", "profilerNumericInput")
            driver.evaluate("""([el, v]) => { if (el) { el.value = v; el.setAttribute("value", v); el.dispatchEvent(new Event("input",{bubbles:true})); el.dispatchEvent(new Event("change",{bubbles:true})); el.dispatchEvent(new Event("blur",{bubbles:true})); } }""", [special, value])
            time.sleep(0.3)
            current = special.get_attribute("value") or ""
            if current.strip() == value:
                print("✓ Champ postal Swagbucks rempli via patch JS direct.")
                return True
        except Exception:
            pass

    return current.strip() == value