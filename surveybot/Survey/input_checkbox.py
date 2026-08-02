"""
input_checkbox.py - Gestion des checkboxes pour input_handler

Ce module contient:
- Clic sur checkbox par label
- Support checkbox ARIA/custom (role=checkbox)
- Support checkbox button-like (jQuery Mobile, etc.)
- Support Confirmit checktable
- Fallbacks JS génériques (Alchemer, générique)

Dépendances:
- input_utils pour les fonctions utilitaires
"""






import unicodedata
import re
import time

# Import depuis input_utils
from Survey.input_utils import (
    norm_txt,
    norm_soft,
    norm_lc_soft,
    xpath_literal,
    scroll_into_view,
    js_click,
    is_checked,
    find_context_container,
    find_question_container_by_ctx,
)
from Survey.log_utils import log_debug


# =============================================================================
# HELPERS CHECKBOX
# =============================================================================

def force_checkbox_events(driver, checkbox_el):
    """
    Force les events JS sur un checkbox pour s'assurer que les frameworks
    (React, Angular, Vue, jQuery) détectent le changement.
    """
    driver.evaluate("""([_el, _arg1]) => {
        const cb = _el;
        cb.checked = true;
        cb.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        cb.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
        // IMPORTANT: pas de click synthétique sur checkbox.
        // Sur certains widgets (ex: Decipher/FocusVision), cela retoggle
        // la valeur et annule une instruction juste appliquée.
        cb.dispatchEvent(new Event('change', { bubbles: true }));
        cb.dispatchEvent(new Event('input', { bubbles: true }));
        """,
        checkbox_el
    )


def privacy_checkbox_is_accepted(driver) -> bool:
    """Vérifie si la checkbox de politique de confidentialité est acceptée."""
    try:
        warn = driver.query_selector("#privacyPolicyFeedback7")
        return not warn.is_visible()
    except Exception:
        return True


# =============================================================================
# FALLBACKS JS
# =============================================================================

def force_label_for_checkbox_js(driver, label_text: str) -> bool:
    """
    Force via JS: trouve un <label for=...>, clique, et synchronise l'input + classes visuelles.
    """
    js = r"""
    const norm = s => (s||'').toLowerCase()
        .normalize('NFKC').replace(/\u00A0/g,' ')
        .replace(/[»«""\"'›→·•:]/g,'').replace(/\s+/g,' ').trim();
    const needle = norm(_el);

    const labs = Array.from(document.querySelectorAll('label'));
    for (const lab of labs) {
      const txt = norm(lab.innerText || lab.textContent || '');
      if (!txt) continue;
      if (!(txt.includes(needle) || needle.includes(txt))) continue;

      const fid = lab.getAttribute('for');
      if (!fid) continue;
      const inp = document.getElementById(fid);
      if (!inp) continue;

      try { lab.click(); } catch(e){}
      try { inp.checked = true; } catch(e){}
      try {
        inp.dispatchEvent(new Event('input',{bubbles:true}));
        inp.dispatchEvent(new Event('change',{bubbles:true}));
      } catch(e){}

      // jQuery-Mobile : synchroniser la classe visuelle
      try {
        if (lab.classList.contains('ui-checkbox-off')) {
          lab.classList.remove('ui-checkbox-off');
          lab.classList.add('ui-checkbox-on');
        }
      } catch(e){}

      return !!(inp.checked || (inp.getAttribute('aria-checked')||'').toLowerCase()==='true');
    }
    return false;
    """
    try:
        return bool(driver.evaluate("(arg) => {" + js + "}", label_text))
    except Exception:
        return False


def fallback_click_checkbox_js_alchemer(driver, target_text: str) -> bool:
    """
    Fallback ciblé Alchemer (classes 'sg-*'):
    - Matche le texte dans la liste .sg-type-checkbox
    - Préfère <label for="..."> puis coche l'<input id="..."> lié
    - Dispatch 'input' + 'change' pour frameworks
    """
    js = r"""
    const norm = s => (s||'').toLowerCase()
      .normalize('NFKC').replace(/\u00A0/g,' ')
      .replace(/[»«""\"'›→·•:]/g,'').replace(/\s+/g,' ').trim();
    const needle = norm(_el);

    const roots = Array.from(document.querySelectorAll(
      '.sg-type-checkbox, .sg-question-options, ul.sg-list'
    ));
    if (!roots.length) return false;

    let items = [];
    for (const r of roots){
      const labels = r.querySelectorAll('label');
      for (const lab of labels){
        const txt = norm(lab.innerText || lab.textContent || '');
        if (!txt) continue;
        if (txt === needle || txt.includes(needle) || needle.includes(txt)){
          items.push(lab);
        }
      }
    }
    if (!items.length) return false;

    items.sort((a,b)=>(b.innerText||'').length-(a.innerText||'').length);
    const lab = items[0];
    lab.scrollIntoView({block:'center'});

    let inp = null;
    const fid = lab.getAttribute('for');
    if (fid) inp = document.getElementById(fid);
    if (!inp){
      const li = lab.closest('li') || lab.parentElement;
      if (li) inp = li.querySelector('input[type=checkbox], input[type=radio]');
    }
    if (!inp) return false;

    try { lab.click(); } catch(e){}

    try {
      if (!inp.checked) inp.checked = true;
      inp.dispatchEvent(new Event('input', {bubbles:true}));
      inp.dispatchEvent(new Event('change', {bubbles:true}));
    } catch(e){}

    return !!(inp.checked || (inp.getAttribute('aria-checked')||'').toLowerCase()==='true');
    """
    try:
        return bool(driver.evaluate("(arg) => {" + js + "}", target_text))
    except Exception:
        return False


def fallback_click_checkbox_js_generic(driver, target_text: str) -> bool:
    """
    Fallback générique multi-sites :
    - Matche par innerText sur <label>/<span> proches
    - Clique label/wrapper, ou force checked=true + events
    """
    js = r"""
    const norm = s => (s||'').toLowerCase()
      .normalize('NFKC').replace(/\u00A0/g,' ')
      .replace(/[»«""\"'›→·•:]/g,'').replace(/\s+/g,' ').trim();
    const needle = norm(_el);

    const candidates = [];
    candidates.push(...document.querySelectorAll('label, .checkbox, [role=checkbox]'));
    candidates.push(...document.querySelectorAll('span, div, li'));

    const scored = [];
    for (const el of candidates){
      const txt = norm(el.innerText || el.textContent || '');
      if (!txt) continue;
      const exact = (txt === needle) ? 2 : 0;
      const contains = (txt.includes(needle) || needle.includes(txt)) ? 1 : 0;
      if (!exact && !contains) continue;
      const r = el.getBoundingClientRect();
      const area = Math.max(1, r.width * r.height);
      let clickable = el.closest('label') || el.closest('[role=checkbox]') || el;
      scored.push({clickable, area, exact, contains});
    }
    if (!scored.length) return false;
    scored.sort((a,b)=> (b.exact-a.exact) || (b.contains-a.contains) || (b.area-a.area));
    const best = scored[0].clickable;
    best.scrollIntoView({block:'center'});

    try { best.click(); } catch(e){}

    let inp = null;
    const lab = best.closest('label');
    if (lab && lab.htmlFor) inp = document.getElementById(lab.htmlFor);
    if (!inp) inp = best.querySelector('input[type=checkbox], input[type=radio]');
    if (!inp){
      const host = best.closest('li, div, section') || document;
      inp = host.querySelector('input[type=checkbox], input[type=radio]');
    }
    if (inp){
      try{
        if (!inp.checked) inp.checked = true;
        inp.dispatchEvent(new Event('input',{bubbles:true}));
        inp.dispatchEvent(new Event('change',{bubbles:true}));
      }catch(e){}
      return !!(inp.checked || (inp.getAttribute('aria-checked')||'').toLowerCase()==='true');
    }
    return false;
    """
    try:
        return bool(driver.evaluate("(arg) => {" + js + "}", target_text))
    except Exception:
        return False


# =============================================================================
# CHECKBOX BUTTON-LIKE (jQuery Mobile, etc.)
# =============================================================================

def click_checkbox_buttonish_by_label(driver, label: str, context_hint: str | None = None) -> bool:
    """
    Coche une 'checkbox' rendue comme un bouton (label role='button' / classes ui-btn, ui-checkbox-*…).
    1) on cible le meilleur <label> (scope par contexte si fourni),
    2) click label (+ variantes),
    3) si pas d'effet, click JS sur l'input lié (for=...),
    4) si toujours rien : force_label_for_checkbox_js().
    """
    def _norm(s: str) -> str:
        return " ".join((s or "").split()).strip().lower()

    scope = None
    try:
        scope = find_context_container(driver, context_hint) if context_hint else None
    except Exception:
        scope = None
    root = scope if scope is not None else driver

    needle = _norm(label)

    # candidats labels "button-like"
    try:
        labels = root.query_selector_all("xpath=" + ".//label[@role='button' or contains(@class,'ui-btn') or contains(@class,'checkbox') or contains(@class,'ui-checkbox') or .//span]")
    except Exception:
        labels = []

    best, best_score = None, -1.0
    for lab in labels:
        try:
            txt = _norm(lab.inner_text() or lab.get_attribute("innerText") or "")
            if not txt:
                continue
            sc = 1.0 if (needle in txt or txt in needle) else 0.0
            if sc == 0.0:
                continue
            if scope is not None:
                sc += 0.25
            if sc > best_score:
                best, best_score = lab, sc
        except Exception:
            continue

    if not best:
        return False

    # input associé via for=...
    linked = None
    try:
        fid = best.get_attribute("for")
        if fid:
            linked = driver.query_selector(f"#{fid}")
    except Exception:
        linked = None

    # 1) scroll + clicks sur le label
    try:
        driver.evaluate("(el) => el.scrollIntoView({block:\'center\'})", best)
    except Exception:
        pass

    for how in ("native", "ac", "js"):
        try:
            if how == "native":
                best.click()
            elif how == "ac":
                best.hover(); best.click()
            else:
                driver.evaluate("(el) => el.click()", best)
            time.sleep(0.15)
            if linked is not None:
                try:
                    if linked.is_selected():
                        return True
                except Exception:
                    pass
            cls = (best.get_attribute("class") or "").lower()
            if "ui-checkbox-on" in cls:
                return True
        except Exception:
            continue

    # 2) clic JS direct sur l'input lié + events
    if linked is not None:
        try:
            driver.evaluate("(el) => el.click()", linked)
            time.sleep(0.1)
            if linked.is_selected():
                return True
            linked.evaluate("""(_el) => {
                const el = _el;
                if (!el.checked) el.checked = true;
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
}""")
            time.sleep(0.1)
            try:
                if linked.is_selected():
                    return True
            except Exception:
                pass
        except Exception:
            pass

    # 3) Dernier recours
    if force_label_for_checkbox_js(driver, label):
        return True

    return False


# =============================================================================
# CONFIRMIT CHECKTABLE
# =============================================================================

def click_confirmit_checktable(driver, label: str, context_hint: str | None = None, max_retries: int = 2, return_element: bool = False):
    """
    Coche une case (ou radio exclusive) dans une table Confirmit :
    <tr class="cRow/rsRow...">
      <td><input type="checkbox|radio" id="..."></td>
      <td><label for="..."><div><p>Texte ...</p></div></label></td>
    Post-vérifie via is_selected()/@checked.
    """
    def _n(s: str) -> str:
        if not s:
            return ""
        s = s.replace("\u00A0", " ").replace("'", "'").replace("´", "'").replace("`", "'")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        s = re.sub(r"\s+", " ", s, flags=re.S).strip()
        return s.lower()

    want = _n(label)
    if not want:
        return None if return_element else False

    try:
        scope = find_question_container_by_ctx(driver, context_hint) or driver
    except Exception:
        scope = driver

    # Candidats: lignes de réponses
    rows = []
    try:
        rows = scope.query_selector_all("xpath=" + ".//tr[contains(@class,'Row') or contains(@class,'row')]")
    except Exception:
        rows = []

    for tr in rows:
        try:
            # Texte de la ligne
            txt = ""
            try:
                lab_el = tr.query_selector("xpath=" + ".//label")
                txt = _n(lab_el.inner_text() or lab_el.get_attribute("innerText") or "")
            except Exception:
                try:
                    txt = _n(tr.inner_text() or tr.get_attribute("innerText") or "")
                except Exception:
                    continue

            if not (want == txt or want in txt or txt in want):
                continue

            # Trouver l'input
            inp = None
            try:
                inp = tr.query_selector("xpath=" + ".//input[@type='checkbox' or @type='radio']")
            except Exception:
                continue

            # Clic
            for attempt in range(max_retries):
                try:
                    driver.evaluate("(el) => el.scrollIntoView({block:\'center\'})", inp)
                    try:
                        inp.click()
                    except Exception:
                        driver.evaluate("(el) => el.click()", inp)
                    time.sleep(0.1)
                    if inp.is_selected():
                        if return_element:
                            return inp
                        return True
                except Exception:
                    continue

            # Force events
            try:
                inp.evaluate("""(_el) => {
                    const el = _el;
                    el.checked = true;
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
}""")
                if inp.is_selected():
                    if return_element:
                        return inp
                    return True
            except Exception:
                pass

        except Exception:
            continue

    return None if return_element else False


# =============================================================================
# IPSOS/mrIWeb SHARKY "GridProgressive" — VARIANTE CHECKBOX (MULTI-RÉPONSES)
# =============================================================================

def click_ipsos_sharky_grid_progressive_checkbox(driver, label: str) -> bool:
    """
    Ipsos/mrIWeb Sharky "GridProgressive", variante multi-réponses (conteneur
    question-container QSubType-MA / prog-type-checkbox) : chaque option est un
    div.prog-the-answer-container[role='checkbox'] (libellé dans un
    span.mrQuestionText imbriqué), enfant direct de div.clearfix.prog-answers-row
    (pas de div.the-radiogroup, contrairement à la variante radio couverte par
    click_ipsos_sharky_grid_progressive_radio dans Survey/input_radio.py, non
    modifiée par cette fonction). Les <input type="checkbox" class="mrMultiple">
    natifs existent dans une table séparée (.no-display-answers, masquée) — ce ne
    sont pas les éléments cliqués ni vérifiés ici.

    Voir BOT_EVOLUTION_MEMORY.md ("ipsos_mriweb_grid_progressive", dom_analyzer.py)
    pour la résolution de la question associée à ce même widget.

    Guard DOM strict : div.clearfix.prog-answers-row présent, contenant des
    div.prog-the-answer-container[role='checkbox'] enfants directs.

    Cible du clic : le div.prog-the-answer-container[role='checkbox'] lui-même.
    Vérification : aria-checked="true" reflété sur ce même div après clic.
    """
    _JS_FIND = r"""
    const norm = s => (s || '')
      .toLowerCase().normalize('NFKC')
      .replace(/\s+/g, ' ').trim();
    const needle = norm(arg);
    if (!needle) return null;

    const rows = Array.from(document.querySelectorAll("div.clearfix.prog-answers-row"));
    if (!rows.length) return null;

    for (const row of rows) {
      const opts = Array.from(row.querySelectorAll(":scope > div.prog-the-answer-container[role='checkbox']"));
      for (const opt of opts) {
        const span = opt.querySelector('span.mrQuestionText');
        const txt = norm(span ? (span.innerText || span.textContent || '') : '');
        if (!txt || !(txt === needle || txt.includes(needle) || needle.includes(txt))) continue;
        return opt;
      }
    }
    return null;
    """

    _JS_VERIFY = r"""
    const norm = s => (s || '')
      .toLowerCase().normalize('NFKC')
      .replace(/\s+/g, ' ').trim();
    const needle = norm(arg);
    const rows = Array.from(document.querySelectorAll("div.clearfix.prog-answers-row"));
    for (const row of rows) {
      const opts = Array.from(row.querySelectorAll(":scope > div.prog-the-answer-container[role='checkbox']"));
      for (const opt of opts) {
        const span = opt.querySelector('span.mrQuestionText');
        const txt = norm(span ? (span.innerText || span.textContent || '') : '');
        if (!txt || !(txt === needle || txt.includes(needle) || needle.includes(txt))) continue;
        return (opt.getAttribute('aria-checked') || '').toLowerCase() === 'true';
      }
    }
    return false;
    """

    # Résout le frame actif, même convention que click_ipsos_sharky_grid_progressive_radio
    # (driver.evaluate_handle/evaluate opèrent sur le document racine sinon).
    _ctx = getattr(driver, "_current_frame", driver)

    try:
        opt = _ctx.evaluate_handle("(arg) => {" + _JS_FIND + "}", label).as_element()
    except Exception as exc:
        log_debug("[TARGET_DEBUG]", f"ipsos_sharky_grid_progressive_checkbox: js_find_exception label={label!r} error={type(exc).__name__}: {exc}")
        return False

    if opt is None:
        log_debug("[TARGET_DEBUG]", f"ipsos_sharky_grid_progressive_checkbox: option_not_found label={label!r}")
        return False

    try:
        opt.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        opt.click()
    except Exception as exc_click:
        try:
            opt.hover()
            opt.click()
        except Exception as exc_hover:
            log_debug(
                "[TARGET_DEBUG]",
                f"ipsos_sharky_grid_progressive_checkbox: click_failed label={label!r} "
                f"click_error={type(exc_click).__name__}: {exc_click} "
                f"hover_click_error={type(exc_hover).__name__}: {exc_hover}",
            )
            return False

    time.sleep(0.15)

    try:
        ok = bool(_ctx.evaluate("(arg) => {" + _JS_VERIFY + "}", label))
    except Exception:
        ok = False

    log_debug("[TARGET_DEBUG]", f"ipsos_sharky_grid_progressive_checkbox: native_verify={'ok' if ok else 'ko'} label={label!r}")
    return ok


# =============================================================================
# QARTS WIDGET HANDLER (Decipher / LifePoints)
# =============================================================================

def click_qarts_widget_by_label(driver, target_text: str) -> bool:
    """
    Handler pour le widget QARTS (Decipher/LifePoints) : checkbox et radio.

    Guard DOM (les deux conditions requises) :
    - div[id^="sq-QARTS-container-"] contenant div._rowpicker   (interface visuelle)
    - div.hidden.answers avec inputs natifs cachés size=0       (grille native cachée)

    Clique le div[tabindex="0"][cursor:pointer] du widget visuel.
    Vérifie la sélection via opacity=1 du premier SVG dans le wrapper.
    """
    _JS_FIND = r"""
    const norm = s => (s || '')
      .toLowerCase().normalize('NFKC')
      .replace(/\u00A0/g, ' ')
      .replace(/[»«\u201c\u201d"'›→·•:]/g, '')
      .replace(/\s+/g, ' ').trim();
    const needle = norm(_el);
    if (!needle) return {ok: false, reason: 'empty_needle'};

    // Guard 1 : conteneur QARTS avec _rowpicker
    const containers = Array.from(
      document.querySelectorAll('div[id^="sq-QARTS-container-"]')
    ).filter(c => c.querySelector('div._rowpicker'));
    if (!containers.length) return {ok: false, reason: 'no_qarts_container'};

    // Guard 2 : grille cachée avec inputs natifs
    if (!document.querySelector(
      'div.hidden.answers input[type="checkbox"], div.hidden.answers input[type="radio"]'
    )) return {ok: false, reason: 'no_hidden_answers'};

    for (const container of containers) {
      const grid = container.querySelector('div.__flexgrid_row');
      if (!grid) continue;

      for (const wrapper of Array.from(grid.children)) {
        // Texte de l'option : premier <span> non-vide dans le wrapper
        let labelText = '';
        for (const sp of Array.from(wrapper.querySelectorAll('span'))) {
          const txt = norm(sp.innerText || sp.textContent || '');
          if (txt) { labelText = txt; break; }
        }
        if (!labelText) continue;
        if (!(labelText === needle || labelText.includes(needle) || needle.includes(labelText))) continue;

        // Zone interactive : div[tabindex="0"] avec cursor:pointer et inset:0
        const clickable = wrapper.querySelector(
          'div[tabindex="0"][style*="cursor: pointer"][style*="inset: 0"]'
        );
        if (!clickable) continue;

        try { clickable.scrollIntoView({block: 'center', inline: 'center'}); } catch(e) {}
        return clickable;  // retourne le WebElement pour clic natif Python (isTrusted=true)
      }
    }
    return null;
    """

    _JS_VERIFY = r"""
    const norm = s => (s || '')
      .toLowerCase().normalize('NFKC')
      .replace(/\u00A0/g, ' ')
      .replace(/[»«\u201c\u201d"'›→·•:]/g, '')
      .replace(/\s+/g, ' ').trim();
    const needle = norm(_el);
    if (!needle) return false;

    for (const container of Array.from(
      document.querySelectorAll('div[id^="sq-QARTS-container-"]')
    ).filter(c => c.querySelector('div._rowpicker'))) {
      const grid = container.querySelector('div.__flexgrid_row');
      if (!grid) continue;
      for (const wrapper of Array.from(grid.children)) {
        let labelText = '';
        for (const sp of Array.from(wrapper.querySelectorAll('span'))) {
          const txt = norm(sp.innerText || sp.textContent || '');
          if (txt) { labelText = txt; break; }
        }
        if (!labelText) continue;
        if (!(labelText === needle || labelText.includes(needle) || needle.includes(labelText))) continue;
        // Premier SVG du div d'icône (margin-left: -25px)
        const iconDiv = wrapper.querySelector('div[style*="margin-left: -25px"]');
        if (!iconDiv) continue;
        const firstSvg = iconDiv.querySelector('svg');
        if (!firstSvg) continue;
        return parseFloat(firstSvg.style.opacity || '0') >= 0.9;
      }
    }
    return false;
    """

    try:
        clickable_el = driver.evaluate("(arg) => {" + _JS_FIND + "}", target_text)
    except Exception:
        return False

    if clickable_el is None:
        log_debug("[TARGET_DEBUG]", f"qarts_widget: element not found label={target_text!r}")
        return False
    if isinstance(clickable_el, dict):
        # _JS_FIND retourne un dict uniquement pour les erreurs de guards
        reason = clickable_el.get('reason', 'unknown')
        log_debug("[TARGET_DEBUG]", f"qarts_widget: skip reason={reason!r} label={target_text!r}")
        return False

    # Clic natif via ActionChains : produit isTrusted=true, reconnu par React.
    try:
        clickable_el.hover(); clickable_el.click()
    except Exception as _ce:
        log_debug("[TARGET_DEBUG]", f"qarts_widget: ActionChains failed label={target_text!r} err={_ce}")
        return False

    log_debug("[TARGET_DEBUG]", f"qarts_widget: click sent label={target_text!r}")
    try:
        time.sleep(0.15)
        verified = bool(driver.evaluate("(arg) => {" + _JS_VERIFY + "}", target_text))
        log_debug("[TARGET_DEBUG]", f"qarts_widget: svg_verify={'ok' if verified else 'ko'} label={target_text!r}")
    except Exception:
        pass

    return True


# =============================================================================
# KANTAR / NFIELD SWATCHES ROWPICKER
# =============================================================================

def click_nfield_swatches_by_label(driver, target_text: str, scope=None) -> bool:
    """
    Handler pour le widget Kantar/Nfield "swatches" rowpicker.

    Guard DOM (les deux conditions requises simultanément):
    - input.mrMultiple.styled dans le même contexte (checkboxes natives, non-interactables)
    - div[tabindex][style*="inset: 0"][style*="cursor: pointer"] (overlay cliquable)

    Identifie la carte par img[alt] ou label texte, clique l'overlay via ActionChains.
    Vérifie via backgroundColor "228, 231, 248" (sélectionné) après la transition CSS.
    """
    _JS_FIND = r"""
    const root = _el || document;
    const norm = s => (s || '')
      .toLowerCase().normalize('NFKC')
      .replace(/\u00A0/g, ' ')
      .replace(/[»«\u201c\u201d"'›→·•:]/g, '')
      .replace(/\s+/g, ' ').trim();
    const needle = norm(_arg1);
    if (!needle) return {ok: false, reason: 'empty_needle'};

    // Guard 1: input.mrMultiple.styled cherché au niveau document (peut être hors scope visuel)
    if (!document.querySelector('input[class*="mrMultiple"][class*="styled"]'))
      return {ok: false, reason: 'no_mrmultiple'};

    // Guard 2: overlay div[tabindex][style*="inset: 0"][style*="cursor: pointer"] présent dans scope
    const allOverlays = Array.from(root.querySelectorAll(
      'div[tabindex][style*="cursor: pointer"][style*="inset: 0"]'
    ));
    if (!allOverlays.length) return {ok: false, reason: 'no_overlay'};

    for (const overlay of allOverlays) {
      const container = overlay.parentElement;
      if (!container) continue;

      // Texte via span.style-0 (fiable) puis label — img[alt] ignoré (texte non fiable: "IB M", "H 3 C")
      const span0 = container.querySelector('span.style-0');
      const span0Txt = norm((span0 && (span0.innerText || span0.textContent)) || '');
      const lab = container.querySelector('label');
      const labTxt = norm((lab && (lab.innerText || lab.textContent)) || '');
      const cardText = span0Txt || labTxt;
      if (!cardText) continue;

      if (!(cardText === needle || cardText.includes(needle) || needle.includes(cardText)))
        continue;

      try { overlay.scrollIntoView({block: 'center', inline: 'center'}); } catch(e) {}
      return overlay;
    }
    return null;
    """

    _JS_VERIFY = r"""
    const root = _el || document;
    const norm = s => (s || '')
      .toLowerCase().normalize('NFKC')
      .replace(/\u00A0/g, ' ')
      .replace(/[»«\u201c\u201d"'›→·•:]/g, '')
      .replace(/\s+/g, ' ').trim();
    const needle = norm(_arg1);
    if (!needle) return false;

    const allOverlays = Array.from(root.querySelectorAll(
      'div[tabindex][style*="cursor: pointer"][style*="inset: 0"]'
    ));
    for (const overlay of allOverlays) {
      const container = overlay.parentElement;
      if (!container) continue;
      const span0 = container.querySelector('span.style-0');
      const span0Txt = norm((span0 && (span0.innerText || span0.textContent)) || '');
      const lab = container.querySelector('label');
      const labTxt = norm((lab && (lab.innerText || lab.textContent)) || '');
      const cardText = span0Txt || labTxt;
      if (!cardText) continue;
      if (!(cardText === needle || cardText.includes(needle) || needle.includes(cardText)))
        continue;
      // Signal de sélection: backgroundColor du div[transition:background-color 250ms] dans la carte
      const transDiv = container.querySelector('[style*="transition: background-color"]');
      const bg = (transDiv ? transDiv.style.backgroundColor : container.style.backgroundColor) || '';
      return bg.indexOf('228') !== -1 && bg.indexOf('231') !== -1;
    }
    // Fallback: un input natif mrMultiple.styled est coché
    return !!document.querySelector('input[class*="mrMultiple"][class*="styled"]:checked');
    """

    try:
        clickable_el = driver.evaluate("([a,b]) => {" + _JS_FIND + "}", [scope, target_text])
    except Exception:
        return False

    if clickable_el is None:
        log_debug("[TARGET_DEBUG]", f"nfield_swatches: element not found label={target_text!r}")
        return False
    if isinstance(clickable_el, dict):
        reason = clickable_el.get("reason", "unknown")
        log_debug("[TARGET_DEBUG]", f"nfield_swatches: skip reason={reason!r} label={target_text!r}")
        return False

    try:
        clickable_el.hover(); clickable_el.click()
    except Exception as _ce:
        log_debug("[TARGET_DEBUG]", f"nfield_swatches: ActionChains failed label={target_text!r} err={_ce}")
        return False

    log_debug("[TARGET_DEBUG]", f"nfield_swatches: click sent label={target_text!r}")
    try:
        time.sleep(0.3)  # CSS transition 250ms
        verified = bool(driver.evaluate("([a,b]) => {" + _JS_VERIFY + "}", [scope, target_text]))
        log_debug("[TARGET_DEBUG]", f"nfield_swatches: verify={'ok' if verified else 'ko'} label={target_text!r}")
    except Exception:
        pass

    return True


# =============================================================================
# FONCTION PRINCIPALE CLICK_CHECKBOX_BY_LABEL
# =============================================================================

def click_checkbox_by_label(driver, target_text: str, context_hint: str | None = None):
    """
    Clique un checkbox identifié par son label visible.
    
    Args:
        driver: WebDriver
        target_text: texte du label checkbox
        context_hint: contexte de question pour scoping
    
    Returns:
        WebElement <input type="checkbox"> si succès, sinon None
    """
    needle = norm_lc_soft(target_text)
    if not needle:
        return None

    # Guard Toluna Runtime : si l'option est déjà cochée, ne pas cliquer (évite de la décocher).
    # Activé uniquement si la structure Runtime_AnswerRow est détectée dans le DOM.
    try:
        _toluna_checked = target_text.evaluate("""(_el) => {
            const norm = s => (s || '').toLowerCase().normalize('NFKC')
                .replace(/\u00A0/g,' ').replace(/[»«\u201c\u201d"'›→·•:]/g,'')
                .replace(/\s+/g,' ').trim();
            const needle = norm(_el);
            const allRows = Array.from(document.querySelectorAll("[data-aut='Runtime_AnswerRow']"));
            if (allRows.length < 2) return false;
            const targetRow = allRows.find(r => {
                const txt = norm(r.innerText || r.textContent || '');
                return txt === needle || txt.includes(needle) || needle.includes(txt);
            });
            if (!targetRow) return false;
            const wrapper = targetRow.querySelector("[data-aut='Runtime_Wrapper']");
            if (!wrapper) return false;
            const inner = wrapper.querySelector("[data-aut='Runtime_IconBox'], [data-aut='Runtime_InnerFill']");
            if (!inner) return false;
            const allInners = allRows.map(r => {
                const w = r.querySelector("[data-aut='Runtime_Wrapper']");
                return w ? w.querySelector("[data-aut='Runtime_IconBox'], [data-aut='Runtime_InnerFill']") : null;
            }).filter(Boolean);
            if (allInners.length < 2) return false;
            const counts = {};
            for (const i of allInners) { const c = i.className || ''; counts[c] = (counts[c] || 0) + 1; }
            const uncheckedCls = Object.keys(counts).reduce((a, b) => counts[b] > counts[a] ? b : a);
            return (inner.className || '') !== uncheckedCls;
}""")
        if _toluna_checked:
            log_debug("[TARGET_DEBUG]", f"click_checkbox_by_label: toluna already_checked skip label={target_text!r}")
            return True
    except Exception:
        pass

    # Toluna Runtime : cliquer une option non encore cochée (div custom, sans <input> natif).
    # Guard DOM : [data-aut='Runtime_AnswerRow'] présent (≥2 rows) + pas d'input natif dans la cible.
    # Clic via Selenium (hors JS) : le listener React est attaché sur le conteneur targetRow.
    # Vérification post-clic après time.sleep : React re-rend de façon asynchrone, la vérification
    # JS synchrone (clsBefore vs clsAfter dans le même tick) retourne toujours false avant ce patch.
    try:
        _toluna_row = target_text.evaluate("""(_el) => {
            const norm = s => (s || '').toLowerCase().normalize('NFKC')
                .replace(/\u00A0/g,' ').replace(/[»«\u201c\u201d"'›→·•:]/g,'')
                .replace(/\s+/g,' ').trim();
            const needle = norm(_el);
            const allRows = Array.from(document.querySelectorAll("[data-aut='Runtime_AnswerRow']"));
            if (allRows.length < 2) return null;
            const targetRow = allRows.find(r => {
                const txt = norm(r.innerText || r.textContent || '');
                return txt === needle || txt.includes(needle) || needle.includes(txt);
            });
            if (!targetRow) return null;
            if (targetRow.querySelector("input[type='checkbox'], input[type='radio']")) return null;
            const inner = targetRow.querySelector("[data-aut='Runtime_IconBox'], [data-aut='Runtime_InnerFill']");
            if (!inner) return null;
            return { row: targetRow, inner, clsBefore: inner.className || '' };
}""")
        if _toluna_row and isinstance(_toluna_row, dict):
            _row_el = _toluna_row.get("row")
            _inner_el = _toluna_row.get("inner")
            _cls_before = _toluna_row.get("clsBefore", "")
            if _row_el is not None and _inner_el is not None:
                scroll_into_view(driver, _row_el)
                try:
                    _row_el.click()
                except Exception:
                    _row_el.hover(); _row_el.click()
                # Laisser React re-rendre avant la vérification
                time.sleep(0.15)
                _cls_after = driver.evaluate("(el) => el.className || \'\'", _inner_el)
                if _cls_after != _cls_before:
                    log_debug("[TARGET_DEBUG]", f"click_checkbox_by_label: toluna_runtime click ok label={target_text!r}")
                    return True
    except Exception:
        pass

    scope = find_context_container(driver, context_hint)

    # 0a) QARTS widget (Decipher/LifePoints) : double structure visuelle + grille cachée.
    #     Guard DOM strict : div[id^="sq-QARTS-container-"] + div._rowpicker
    #     ET div.hidden.answers avec inputs natifs (size=0, non-interactables directement).
    try:
        if click_qarts_widget_by_label(driver, target_text):
            return True
    except Exception:
        pass

    # 0b) Kantar/Nfield swatches rowpicker : overlay div[tabindex][inset:0] non-interactable
    #     via input natif ou label. Guard DOM strict : input.mrMultiple.styled
    #     ET div[tabindex][style*="cursor: pointer"][style*="inset: 0"] dans la même portée.
    try:
        if click_nfield_swatches_by_label(driver, target_text, scope=scope):
            return True
    except Exception:
        pass

    # 0c) Ipsos/mrIWeb Sharky "GridProgressive" variante checkbox (multi-réponses,
    #     widget div-based sans input natif exploitable). Guard DOM strict :
    #     div.clearfix.prog-answers-row > div.prog-the-answer-container[role='checkbox'].
    try:
        if click_ipsos_sharky_grid_progressive_checkbox(driver, target_text):
            return True
    except Exception:
        pass

    # 0) DOM ciblé: Decipher + Dynata MX Carousel superposé à une grille checkbox.
    #    Guard DOM strict: même question contient à la fois la grille .answers-table/.clickableCell
    #    et un stage #mx-stage-{qid}. Le clic doit cibler le <td.clickableCell> natif.
    try:
        mx_targets = cb.evaluate("""(_el) => {
            const scope = _el;
            const norm = s => (s || '')
              .toLowerCase()
              .normalize('NFKC')
              .replace(/\u00A0/g, ' ')
              .replace(/\s+/g, ' ')
              .trim();
            const needle = norm(_arg1);
            if (!needle) return null;

            const hostCandidates = [];
            if (scope && scope.nodeType === 1) {
              const nearHost = scope.closest('.question, [id^="question_"]');
              if (nearHost) hostCandidates.push(nearHost);
            }
            for (const host of Array.from(document.querySelectorAll('[id^="question_"]'))) {
              if (!hostCandidates.includes(host)) hostCandidates.push(host);
            }

            const candidates = [];
            for (const host of hostCandidates) {
              if (!host || !host.id || !host.id.startsWith('question_')) continue;

              const qid = host.id.slice('question_'.length);
              if (!qid) continue;

              const hasCheckboxGrid = !!host.querySelector('.answers.answers-table td.clickableCell input[type="checkbox"]');
              if (!hasCheckboxGrid) continue;

              const stage = document.getElementById('mx-stage-' + qid);
              if (!stage) continue;

              const labels = Array.from(host.querySelectorAll('.answers.answers-table td.clickableCell label[for]'));
              for (const label of labels) {
                const txt = norm(label.innerText || label.textContent || '');
                if (!txt) continue;
                if (!(txt === needle || txt.includes(needle) || needle.includes(txt))) continue;

                const td = label.closest('td.clickableCell');
                if (!td) continue;

                const inputId = label.getAttribute('for');
                if (!inputId) continue;
                const input = host.querySelector('#' + CSS.escape(inputId));
                if (!input || input.type !== 'checkbox') continue;

                candidates.push({
                  td,
                  input,
                  score: txt === needle ? 2 : 1,
                  len: txt.length,
                });
              }
            }
            if (!candidates.length) return null;

            candidates.sort((a, b) => (b.score - a.score) || (b.len - a.len));
            const best = candidates[0];

            best.td.scrollIntoView({ block: 'center', inline: 'center' });
            return [best.td, best.input];
}""", [scope, target_text])
        if isinstance(mx_targets, list) and len(mx_targets) == 2 and mx_targets[0] is not None:
            td_clickable, matched_input = mx_targets

            scroll_into_view(driver, td_clickable)
            try:
                td_clickable.click()
            except Exception:
                td_clickable.hover(); td_clickable.click()

            # Vérification post-clic: l'input natif lié doit être checked.
            mx_checked = driver.evaluate("""([_el, _arg1]) => {
                const scope = _el;
                const norm = s => (s || '')
                  .toLowerCase()
                  .normalize('NFKC')
                  .replace(/\u00A0/g, ' ')
                  .replace(/\s+/g, ' ')
                  .trim();
                const needle = norm(_arg1);
                if (!needle) return false;

                const hostCandidates = [];
                if (scope && scope.nodeType === 1) {
                  const nearHost = scope.closest('.question, [id^="question_"]');
                  if (nearHost) hostCandidates.push(nearHost);
                }
                for (const host of Array.from(document.querySelectorAll('[id^="question_"]'))) {
                  if (!hostCandidates.includes(host)) hostCandidates.push(host);
                }

                for (const host of hostCandidates) {
                  if (!host || !host.id || !host.id.startsWith('question_')) continue;
                  const qid = host.id.slice('question_'.length);
                  if (!qid) continue;

                  const hasCheckboxGrid = !!host.querySelector('.answers.answers-table td.clickableCell input[type="checkbox"]');
                  if (!hasCheckboxGrid) continue;

                  const stage = document.getElementById('mx-stage-' + qid);
                  if (!stage) continue;

                  const labels = Array.from(host.querySelectorAll('.answers.answers-table td.clickableCell label[for]'));
                  const match = labels.find(label => {
                    const txt = norm(label.innerText || label.textContent || '');
                    return txt && (txt === needle || txt.includes(needle) || needle.includes(txt));
                  });
                  if (!match) continue;

                  const inputId = match.getAttribute('for');
                  if (!inputId) continue;
                  const input = host.querySelector('#' + CSS.escape(inputId));
                  if (input && input.checked === true) return true;
                }

                return false;
}""", [scope, target_text])
            if mx_checked:
                return matched_input
    except Exception:
        pass

    # 1) DOM ciblé: wrappers .answer_options avec input[class*="QT"] (checkboxQT ou radioQT)
    #    (MetrixLab/Toluna SPA). L'état visuel coché est porté par div.option_checkbox.input_on
    #    et/ou div.option_label.input_label_on, pas de manière fiable par input.checked.
    #
    #    Point important:
    #    - on NE clique PAS via JS sur le wrapper, car certains handlers UI exigent un évènement
    #      utilisateur "trusted".
    #    - on trouve le meilleur wrapper en JS, puis on clique en Selenium natif / ActionChains.
    #    - la vérification est refaite via une nouvelle recherche DOM après un léger délai.
    try:
        metrixlab_target = driver.evaluate("""([_el, _arg1]) => {
            const root = _el || document;
            const norm = s => (s || '')
              .toLowerCase()
              .normalize('NFKC')
              .replace(/\u00A0/g, ' ')
              .replace(/[’']/g, "'")
              .replace(/\s+/g, ' ')
              .trim();
            const needle = norm(_arg1);
            if (!needle) return null;

            const wrappers = Array.from(root.querySelectorAll('div.answer_options'));
            if (!wrappers.length) return null;

            // Guard DOM strict:
            // - input QT avec name partagé entre plusieurs options
            const qtInputs = wrappers
              .map(w => w.querySelector('input[name]'))
              .filter(inp =>
                inp &&
                /^(checkbox|radio)$/i.test(inp.type || '') &&
                (inp.className || '').includes('QT')
              );
            if (qtInputs.length < 2) return null;

            const nameCounts = new Map();
            for (const inp of qtInputs) {
              const name = (inp.getAttribute('name') || '').trim();
              if (!name) continue;
              nameCounts.set(name, (nameCounts.get(name) || 0) + 1);
            }
            const hasSharedName = Array.from(nameCounts.values()).some(v => v >= 2);
            if (!hasSharedName) return null;

            const candidates = [];
            for (const wrap of wrappers) {
              const inp = wrap.querySelector('input[name]');
              if (!inp) continue;
              if (!/^(checkbox|radio)$/i.test(inp.type || '')) continue;
              if (!(inp.className || '').includes('QT')) continue;

              const labelNode = wrap.querySelector('.option_label, .option_label span, span');
              const txt = norm(
                (labelNode && (labelNode.innerText || labelNode.textContent)) ||
                wrap.innerText ||
                wrap.textContent ||
                ''
              );
              if (!txt) continue;
              if (!(txt === needle || txt.includes(needle) || needle.includes(txt))) continue;

              const checkboxDiv = wrap.querySelector('.option_checkbox');
              const labelDiv = wrap.querySelector('.option_label');
              candidates.push({
                wrap,
                inp,
                checkboxDiv,
                labelDiv,
                score: txt === needle ? 2 : 1,
                len: txt.length,
              });
            }
            if (!candidates.length) return null;

            candidates.sort((a, b) => (b.score - a.score) || (b.len - a.len));
            const best = candidates[0];
            try { best.wrap.scrollIntoView({block: 'center', inline: 'center'}); } catch(e) {}
            return [best.wrap, best.inp, best.checkboxDiv, best.labelDiv];
}""", [scope, target_text])
        if isinstance(metrixlab_target, list) and len(metrixlab_target) >= 2 and metrixlab_target[0] is not None:
            clicked_wrap = metrixlab_target[0]
            clicked_input = metrixlab_target[1]
            clicked_checkbox_div = metrixlab_target[2] if len(metrixlab_target) > 2 else None
            clicked_label_div = metrixlab_target[3] if len(metrixlab_target) > 3 else None

            try:
                scroll_into_view(driver, clicked_wrap)
            except Exception:
                pass

            clicked = False
            for candidate in (clicked_checkbox_div, clicked_label_div, clicked_wrap, clicked_input):
                if candidate is None:
                    continue
                try:
                    candidate.click()
                    clicked = True
                    break
                except Exception:
                    try:
                        candidate.hover(); candidate.click()
                        clicked = True
                        break
                    except Exception:
                        continue

            if clicked:
                time.sleep(0.20)
                verify_ok = driver.evaluate("""([_el, _arg1]) => {
                    const root = _el || document;
                    const norm = s => (s || '')
                      .toLowerCase()
                      .normalize('NFKC')
                      .replace(/\u00A0/g, ' ')
                      .replace(/[’']/g, "'")
                      .replace(/\s+/g, ' ')
                      .trim();
                    const needle = norm(_arg1);
                    if (!needle) return false;

                    const wrappers = Array.from(root.querySelectorAll('div.answer_options'));
                    for (const wrap of wrappers) {
                      const labelNode = wrap.querySelector('.option_label, .option_label span, span');
                      const txt = norm(
                        (labelNode && (labelNode.innerText || labelNode.textContent)) ||
                        wrap.innerText ||
                        wrap.textContent ||
                        ''
                      );
                      if (!txt) continue;
                      if (!(txt === needle || txt.includes(needle) || needle.includes(txt))) continue;

                      const checkboxDiv = wrap.querySelector('.option_checkbox');
                      const labelDiv = wrap.querySelector('.option_label');
                      const checkboxOn = !!(checkboxDiv && checkboxDiv.classList.contains('input_on'));
                      const labelOn = !!(labelDiv && labelDiv.classList.contains('input_label_on'));
                      if (checkboxOn || labelOn) return true;
                    }
                    return false;
}""", [scope, target_text])
                if verify_ok:
                    log_debug("[TARGET_DEBUG]", f"click_checkbox_by_label: metrixlab QT ok label={target_text!r}")
                    return clicked_input
    except Exception:
        pass

    # 2) Cas standard : <label for="id"> → <input id="id" type="checkbox">
    try:
        labels = (scope or driver).query_selector_all("xpath=" + ".//label[normalize-space()!='' and contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
            f"{xpath_literal(needle)})]")

        for label in labels:
            fid = label.get_attribute("for")
            if not fid:
                continue

            try:
                cb = driver.query_selector(f"#{fid}")
            except Exception:
                continue

            if cb.get_attribute("type") != "checkbox":
                continue

            scroll_into_view(driver, cb)

            # GfK/AngularJS: l'input porte ng-model mais l'état est géré par
            # pCheckBox() via ng-click sur div.prettycheckbox — is_selected() sur
            # l'input natif retourne toujours False (le $scope Angular contrôle la
            # valeur, pas .checked). On clique le div.prettycheckbox[ng-click] et
            # on vérifie via <a class="checked"> (ng-class binding Angular).
            # Guard DOM strict: ng-model non vide + prettycheckbox[ng-click] dans muCT.
            _ng_model = cb.get_attribute("ng-model") or ""
            if _ng_model:
                try:
                    _pretty = cb.query_selector_all("xpath=" + "ancestor::*[contains(@class,'muCT')][1]//div[contains(@class,'prettycheckbox') and @ng-click]")
                    if _pretty:
                        scroll_into_view(driver, _pretty[0])
                        try:
                            _pretty[0].click()
                        except Exception:
                            _pretty[0].hover(); _pretty[0].click()
                        _a_checked = bool(
                            _pretty[0].query_selector_all("xpath=" + ".//a[contains(@class,'checked')]")
                        )
                        if _a_checked or is_checked(cb):
                            return cb
                        continue
                except Exception:
                    pass

            # Guard DOM minimal: si le label contient un lien (<a>), on évite tout
            # clic sur le label/texte pour ne jamais déclencher de navigation parasite.
            # On agit uniquement sur l'input checkbox lié.
            label_has_link = False
            try:
                label_has_link = bool(label.query_selector_all("xpath=" + ".//a[@href]"))
            except Exception:
                label_has_link = False

            if label_has_link:
                # Guard Angular: ng-model+ng-checked signalent un binding AngularJS.
                # JS cb.checked=true+dispatchEvent ne propage pas dans le $scope Angular.
                # Un click Selenium natif sur l'<input> déclenche le cycle $digest normalement.
                is_angular_input = bool(
                    cb.get_attribute("ng-model")
                    and cb.get_attribute("ng-checked") is not None
                )
                if is_angular_input:
                    try:
                        cb.click()
                    except Exception:
                        js_click(driver, cb)
                    if is_checked(cb):
                        return cb
                    # fallthrough si le click n'a pas suffi
                else:
                    try:
                        driver.evaluate("""() => {
                            const cb = _el;
                            if (!cb.checked) cb.checked = true;
                            cb.dispatchEvent(new Event('input', { bubbles: true }));
                            cb.dispatchEvent(new Event('change', { bubbles: true }));
}
}""")
                    except Exception:
                        pass

            if not is_checked(cb):
                try:
                    cb.click()
                except Exception:
                    js_click(driver, cb)

            # Ne dispatch les events que si le click n'a pas suffi.
            # Sur AngularJS (ng-model), le click natif déclenche déjà le $digest;
            # un second `change` bubbling retogglerait la valeur.
            if not is_checked(cb):
                force_checkbox_events(driver, cb)

            if is_checked(cb):
                return cb

    except Exception:
        pass

    # 3) Cas checkbox ARIA / custom (role="checkbox")
    try:
        boxes = (scope or driver).query_selector_all("xpath=" + ".//*[@role='checkbox' or @aria-checked]")

        for box in boxes:
            txt = norm_soft(box.inner_text() or box.get_attribute("aria-label") or "")
            if needle not in txt.lower():
                continue

            scroll_into_view(driver, box)

            try:
                box.click()
            except Exception:
                js_click(driver, box)

            if box.get_attribute("aria-checked") == "true":
                return box

    except Exception:
        pass

    # 4) Cas fallback Confirmit / tables
    try:
        cb = click_confirmit_checktable(
            driver,
            label=target_text,
            context_hint=context_hint,
            return_element=True,
        )
        if cb:
            force_checkbox_events(driver, cb)
            return cb
    except Exception:
        pass

    # 5) Fallbacks JS
    if fallback_click_checkbox_js_alchemer(driver, target_text):
        return True  # Pas d'élément retourné mais succès
    
    if fallback_click_checkbox_js_generic(driver, target_text):
        return True

    return None