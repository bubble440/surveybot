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
from Survey.log_utils import log_debug


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
# CHAMP DATE NATIF (input[type="date"], ex: Confirmit cf-question--date)
# =============================================================================

_NATIVE_DATE_VERIFY_MAX_ATTEMPTS = 3
_NATIVE_DATE_VERIFY_RETRY_DELAY_S = 0.1

_NATIVE_DATE_SET_JS = """(args) => {
    const [el, v] = args;
    el.value = v;
    try { el.dispatchEvent(new Event('input', {bubbles:true})); } catch(e) {}
    try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
}"""


def fill_native_date_input(driver, value: str, element_id: str, frame_chain=None) -> bool:
    """
    Saisie dédiée pour <input type="date"> natif (ex: Confirmit/Forsta cf-question--date).

    N'appelle PAS fill_text_input : son selector générique (ligne `selector = ...` ci-dessous)
    n'inclut pas input[type='date'], et son fallback driver.wait_for_selector() (non scopé)
    peut alors retourner un tout autre champ texte de la page (ex: code postal) en cas
    d'échec de scope -- cause racine du bug d'écrasement croisé zipcode/DOB. Ce champ est
    donc résolu STRICTEMENT par id (jamais par contexte/texte de question), voir
    BOT_EVOLUTION_MEMORY.md : "CHAMP DATE NATIF (input type=date)".

    N'appelle pas non plus react_set_value_and_fire (helper générique React) : l'assignation
    JS directe `el.value = v` + dispatch input/change ci-dessous suit le même pattern déjà
    validé en production pour select_native_option_by_target (input_dropdown.py) sur des
    inputs natifs non-React.

    Support iframe : si frame_chain (context.frame_chain du registry DOM) est renseigné,
    la résolution + saisie + vérification s'exécutent dans ce contexte, même convention
    que _apply_by_target_id / _apply_toluna_runtime_answerrow_cached (action_dispatcher.py).
    Avant ce patch, ce chemin ignorait frame_chain alors que le registry le porte déjà.

    Args:
        driver: WebDriver
        value: date au format AAAA-MM-JJ (ISO) ou JJ/MM/AAAA, telle que produite par le
            selection_rule dédié de build_batch_prompt / _normalize_native_date_single.
        element_id: id DOM natif du champ (context.id du bloc), obligatoire.
        frame_chain: liste d'indices d'iframes imbriquées (registry DOM_REGISTRY), ou None/[].

    Returns:
        True si la valeur ISO a été appliquée et vérifiée sur ce champ précis.
    """
    if not element_id:
        return False

    raw = (value or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if m:
        yyyy, mm, dd = m.group(1), m.group(2), m.group(3)
    else:
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", raw)
        if not m:
            log_debug("[NATIVE_DATE]", f"id={element_id!r} format non reconnu value={value!r}")
            return False
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
    iso = f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"

    def _apply(ctx_driver) -> bool:
        # query_selector (Playwright natif), pas find_element (API Selenium absente de
        # l'objet page au runtime -> AttributeError). Même méthode que
        # select_native_option_by_target (input_dropdown.py) : driver.query_selector(f"#{id}").
        try:
            field = ctx_driver.query_selector(f"#{element_id}")
            if field is None:
                raise LookupError(f"no element with id={element_id!r}")
        except Exception as exc:
            log_debug("[NATIVE_DATE]", f"id={element_id!r} element introuvable: {type(exc).__name__}: {exc}")
            return False

        try:
            tag = (field.evaluate("e => e.tagName.toLowerCase()") or "").strip().lower()
            native_type = (field.get_attribute("type") or "").strip().lower()
        except Exception as exc:
            log_debug("[NATIVE_DATE]", f"id={element_id!r} lecture tag/type échouée: {type(exc).__name__}: {exc}")
            return False

        if tag != "input" or native_type != "date":
            log_debug("[NATIVE_DATE]", f"id={element_id!r} guard non satisfait tag={tag!r} type={native_type!r}")
            return False

        before = field.get_attribute("value") or ""
        try:
            ctx_driver.evaluate(_NATIVE_DATE_SET_JS, [field, iso])
        except Exception as exc:
            log_debug("[NATIVE_DATE]", f"id={element_id!r} assignation JS échouée: {type(exc).__name__}: {exc}")
            return False

        current = ""
        attempt = 0
        for attempt in range(_NATIVE_DATE_VERIFY_MAX_ATTEMPTS):
            try:
                current = ctx_driver.evaluate("(e) => e.value", field) or ""
            except Exception:
                current = field.get_attribute("value") or ""
            if current.strip() == iso:
                break
            time.sleep(_NATIVE_DATE_VERIFY_RETRY_DELAY_S)

        log_debug(
            "[NATIVE_DATE]",
            f"id={element_id!r} before={before!r} target={iso!r} after={current!r} "
            f"verify_attempts={attempt + 1} frame_chain={frame_chain!r}",
        )
        return current.strip() == iso

    if frame_chain:
        try:
            from Survey.frame_utils import switch_to_frame_chain  # type: ignore
        except Exception:
            switch_to_frame_chain = None  # type: ignore
        if switch_to_frame_chain is not None:
            with switch_to_frame_chain(driver, frame_chain) as ok:
                if not ok:
                    log_debug("[NATIVE_DATE]", f"id={element_id!r} switch_to_frame_chain échoué chain={frame_chain!r}")
                    return False
                return _apply(driver)

    return _apply(driver)


# =============================================================================
# WIDGET ZIP2CITY IFOP (input[type="search"].jz2c-input, s2.ifoponline.com)
# =============================================================================

_IFOP_Z2C_DROPDOWN_POLL_DELAY_S = 0.3
_IFOP_Z2C_DROPDOWN_MAX_POLLS = 15  # budget ~4.5s d'attente AJAX/rendu du dropdown

_IFOP_Z2C_FIND_SUGGESTION_JS = """(box) => {
    const nodes = Array.from(box.querySelectorAll('*'));
    const leaf = nodes.find(n =>
        n.children.length === 0 &&
        n.textContent && n.textContent.trim().length > 0 &&
        n.offsetParent !== null
    );
    return leaf || null;
}"""


def fill_ifop_zip2city_widget(driver, value: str, xpath: str, frame_chain=None) -> bool:
    """
    Saisie dédiée pour le widget tiers zip2city (Ifop/SSI, s2.ifoponline.com,
    script zip2city.ifop.com/z2c.js). Voir dom_analyzer.py::_is_ifop_zip2city_input
    et BOT_EVOLUTION_MEMORY.md : "IFOP ZIP2CITY".

    Interaction en 2 temps, PAS une simple saisie de texte : la saisie du code
    postal dans <input type="search" class="jz2c-input"> déclenche une résolution
    AJAX qui peuple un dropdown de villes dans le <div class="jz2c-box"> adjacent
    (vide au chargement). Les champs satellites cachés (ex: {prefix}cp,
    {prefix}insee) ne sont peuplés qu'après clic sur une ville du dropdown.

    Résolution STRICTE par xpath (registry DOM_REGISTRY, _best_xpath_for_element) :
    cet input n'a ni id ni name, fill_text_input générique n'est donc pas
    applicable ici. Une seule stratégie, pas de fallback empilé : en cas d'échec
    (dropdown absent après budget, aucune suggestion), retourne False sans
    retomber sur fill_text_input.

    Args:
        driver: WebDriver (Page Playwright ou frame résolu)
        value: code postal (5 chiffres), produit par le selection_rule dédié
            (prompt_builder.py, context.ifop_zip2city_widget).
        xpath: xpath de l'input jz2c-input (registry DOM_REGISTRY["xpath"]).
        frame_chain: liste d'indices d'iframes imbriquées, ou None/[].

    Returns:
        True si le code postal a été saisi ET une ville sélectionnée dans le
        dropdown (vérifié via le champ caché satellite "{prefix}cp" peuplé).
    """
    digits = re.sub(r"\D", "", value or "")[:5]
    if len(digits) != 5:
        log_debug("[IFOP_Z2C]", f"value invalide (attendu 5 chiffres): value={value!r}")
        return False

    def _apply(ctx_driver) -> bool:
        try:
            field = ctx_driver.query_selector(f"xpath={xpath}")
            if field is None:
                raise LookupError(f"no element for xpath={xpath!r}")
        except Exception as exc:
            log_debug("[IFOP_Z2C]", f"input introuvable xpath={xpath!r}: {type(exc).__name__}: {exc}")
            return False

        try:
            cls = (field.get_attribute("class") or "").lower()
            native_type = (field.get_attribute("type") or "").strip().lower()
            prefix = (field.get_attribute("data-prefix") or "").strip()
        except Exception as exc:
            log_debug("[IFOP_Z2C]", f"lecture attributs échouée: {type(exc).__name__}: {exc}")
            return False

        if "jz2c-input" not in cls.split() or native_type != "search" or not prefix:
            log_debug("[IFOP_Z2C]", f"guard non satisfait class={cls!r} type={native_type!r} prefix={prefix!r}")
            return False

        try:
            set_input_value_with_events(ctx_driver, field, digits)
        except Exception as exc:
            log_debug("[IFOP_Z2C]", f"saisie code postal échouée: {type(exc).__name__}: {exc}")
            return False

        try:
            box_nodes = field.query_selector_all(
                "xpath=following-sibling::div[contains(concat(' ',normalize-space(@class),' '),' jz2c-box ')]"
            )
            box = box_nodes[0] if box_nodes else None
        except Exception:
            box = None
        if box is None:
            log_debug("[IFOP_Z2C]", "div.jz2c-box introuvable (sibling)")
            return False

        candidate = None
        attempt = 0
        for attempt in range(_IFOP_Z2C_DROPDOWN_MAX_POLLS):
            try:
                handle = box.evaluate_handle(_IFOP_Z2C_FIND_SUGGESTION_JS)
                candidate = handle.as_element() if handle else None
            except Exception:
                candidate = None
            if candidate is not None:
                break
            time.sleep(_IFOP_Z2C_DROPDOWN_POLL_DELAY_S)

        if candidate is None:
            log_debug("[IFOP_Z2C]", f"aucune ville proposée après {attempt + 1} polls, cp={digits!r}")
            return False

        try:
            candidate_text = (candidate.inner_text() or "").strip()
            candidate.click()
        except Exception as exc:
            log_debug("[IFOP_Z2C]", f"clic ville échoué: {type(exc).__name__}: {exc}")
            return False

        cp_val = ""
        for attempt in range(_IFOP_Z2C_DROPDOWN_MAX_POLLS):
            try:
                cp_field = ctx_driver.query_selector(f"#{prefix}cp")
                cp_val = (cp_field.get_attribute("value") or "") if cp_field else ""
            except Exception:
                cp_val = ""
            if cp_val.strip():
                break
            time.sleep(_IFOP_Z2C_DROPDOWN_POLL_DELAY_S)

        log_debug(
            "[IFOP_Z2C]",
            f"cp={digits!r} ville={candidate_text!r} {prefix}cp_after={cp_val!r} frame_chain={frame_chain!r}",
        )
        return bool(cp_val.strip())

    if frame_chain:
        try:
            from Survey.frame_utils import switch_to_frame_chain  # type: ignore
        except Exception:
            switch_to_frame_chain = None  # type: ignore
        if switch_to_frame_chain is not None:
            with switch_to_frame_chain(driver, frame_chain) as ok:
                if not ok:
                    log_debug("[IFOP_Z2C]", f"switch_to_frame_chain échoué chain={frame_chain!r}")
                    return False
                return _apply(driver)

    return _apply(driver)


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