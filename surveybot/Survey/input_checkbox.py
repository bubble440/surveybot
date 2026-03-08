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

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
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


# =============================================================================
# HELPERS CHECKBOX
# =============================================================================

def force_checkbox_events(driver, checkbox_el):
    """
    Force les events JS sur un checkbox pour s'assurer que les frameworks
    (React, Angular, Vue, jQuery) détectent le changement.
    """
    driver.execute_script(
        """
        const cb = arguments[0];
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
        warn = driver.find_element(By.ID, "privacyPolicyFeedback7")
        return not warn.is_displayed()
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
    const needle = norm(arguments[0]);

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
        return bool(driver.execute_script(js, label_text))
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
    const needle = norm(arguments[0]);

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
        return bool(driver.execute_script(js, target_text))
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
    const needle = norm(arguments[0]);

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
        return bool(driver.execute_script(js, target_text))
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
        labels = root.find_elements(
            By.XPATH,
            ".//label[@role='button' or contains(@class,'ui-btn') or contains(@class,'checkbox') or contains(@class,'ui-checkbox') or .//span]"
        )
    except Exception:
        labels = []

    best, best_score = None, -1.0
    for lab in labels:
        try:
            txt = _norm(lab.text or lab.get_attribute("innerText") or "")
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
            linked = driver.find_element(By.ID, fid)
    except Exception:
        linked = None

    # 1) scroll + clicks sur le label
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
    except Exception:
        pass

    for how in ("native", "ac", "js"):
        try:
            if how == "native":
                best.click()
            elif how == "ac":
                ActionChains(driver).move_to_element(best).click().perform()
            else:
                driver.execute_script("arguments[0].click();", best)
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
            driver.execute_script("arguments[0].click();", linked)
            time.sleep(0.1)
            if linked.is_selected():
                return True
            driver.execute_script("""
                const el = arguments[0];
                if (!el.checked) el.checked = true;
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
            """, linked)
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
        rows = scope.find_elements(By.XPATH, ".//tr[contains(@class,'Row') or contains(@class,'row')]")
    except Exception:
        rows = []

    for tr in rows:
        try:
            # Texte de la ligne
            txt = ""
            try:
                lab_el = tr.find_element(By.XPATH, ".//label")
                txt = _n(lab_el.text or lab_el.get_attribute("innerText") or "")
            except Exception:
                try:
                    txt = _n(tr.text or tr.get_attribute("innerText") or "")
                except Exception:
                    continue

            if not (want == txt or want in txt or txt in want):
                continue

            # Trouver l'input
            inp = None
            try:
                inp = tr.find_element(By.XPATH, ".//input[@type='checkbox' or @type='radio']")
            except Exception:
                continue

            # Clic
            for attempt in range(max_retries):
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
                    try:
                        inp.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", inp)
                    time.sleep(0.1)
                    if inp.is_selected():
                        if return_element:
                            return inp
                        return True
                except Exception:
                    continue

            # Force events
            try:
                driver.execute_script("""
                    const el = arguments[0];
                    el.checked = true;
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                """, inp)
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

    scope = find_context_container(driver, context_hint)

    # 0) DOM ciblé: Decipher + Dynata MX Carousel superposé à une grille checkbox.
    #    Guard DOM strict: même question contient à la fois la grille .answers-table/.clickableCell
    #    et un stage #mx-stage-{qid} avec des scales .mx-carouselapp-scale[data-code].
    try:
        mx_targets = driver.execute_script(
            r"""
            const root = arguments[0] || document;
            const norm = s => (s || '')
              .toLowerCase()
              .normalize('NFKC')
              .replace(/\u00A0/g, ' ')
              .replace(/\s+/g, ' ')
              .trim();
            const needle = norm(arguments[1]);
            if (!needle) return null;

            const host = root.closest('.question') || root.closest('[id^="question_"]') || root;
            const qNode = host && host.id && host.id.startsWith('question_') ? host : null;
            const qid = qNode ? qNode.id.slice('question_'.length) : null;
            if (!qid) return null;

            const hasCheckboxGrid = !!host.querySelector('.answers.answers-table .clickableCell input[type="checkbox"]');
            if (!hasCheckboxGrid) return null;

            const stage = host.querySelector('#mx-stage-' + qid);
            if (!stage) return null;

            const scales = Array.from(stage.querySelectorAll('.mx-carouselapp-scale[data-code]'));
            if (!scales.length) return null;

            const candidates = [];
            for (const scale of scales) {
              const label = scale.querySelector('.label');
              const txt = norm((label && (label.innerText || label.textContent)) || scale.innerText || scale.textContent || '');
              if (!txt) continue;
              if (!(txt === needle || txt.includes(needle) || needle.includes(txt))) continue;
              candidates.push({
                scale,
                score: txt === needle ? 2 : 1,
                len: txt.length,
              });
            }
            if (!candidates.length) return null;

            candidates.sort((a, b) => (b.score - a.score) || (b.len - a.len));
            const best = candidates[0].scale;

            // Préparer la carte-ligne (requis par Dynata MX pour accepter le clic sur les scales).
            let rowCode = null;
            const scopedRowLegend = root.closest('tr')?.querySelector('[id$="_left"]');
            const rowLegendId = (scopedRowLegend && scopedRowLegend.id) || '';
            const rowLegendMatch = rowLegendId.match(/_r(\d+)_left$/);
            if (rowLegendMatch) {
              rowCode = 'r' + rowLegendMatch[1];
            }

            let rowCard = null;
            if (rowCode) {
              rowCard = stage.querySelector('.mx-carouselapp-item[data-code="' + rowCode + '"]');
            }
            if (!rowCard) {
              rowCard = stage.querySelector('.mx-carouselapp-item[data-code]');
            }

            const rowClickable =
              rowCard && !rowCard.classList.contains('mx-card-selected')
                ? (rowCard.querySelector('.mx-card') || rowCard)
                : null;
            const scaleClickable = best.querySelector('.mx-card') || best;
            if (!scaleClickable) return null;

            scaleClickable.scrollIntoView({ block: 'center', inline: 'center' });
            return [rowClickable, scaleClickable];
            """,
            scope,
            target_text,
        )
        if isinstance(mx_targets, list) and len(mx_targets) == 2 and mx_targets[1] is not None:
            row_clickable, scale_clickable = mx_targets

            if row_clickable is not None:
                try:
                    scroll_into_view(driver, row_clickable)
                    row_clickable.click()
                except Exception:
                    ActionChains(driver).move_to_element(row_clickable).click().perform()

            scroll_into_view(driver, scale_clickable)
            try:
                scale_clickable.click()
            except Exception:
                ActionChains(driver).move_to_element(scale_clickable).click().perform()

            # Vérification post-clic: si l'input natif lié n'est pas checked, laisser le step 2 gérer.
            mx_checked = driver.execute_script(
                r"""
                const root = arguments[0] || document;
                const norm = s => (s || '')
                  .toLowerCase()
                  .normalize('NFKC')
                  .replace(/\u00A0/g, ' ')
                  .replace(/\s+/g, ' ')
                  .trim();
                const needle = norm(arguments[1]);
                if (!needle) return false;

                const host = root.closest('.question') || root.closest('[id^="question_"]') || root;
                const labels = Array.from(host.querySelectorAll('.answers.answers-table td.clickableCell label[for]'));
                const match = labels.find(label => {
                  const txt = norm(label.innerText || label.textContent || '');
                  return txt && (txt === needle || txt.includes(needle) || needle.includes(txt));
                });
                if (!match) return false;

                const inputId = match.getAttribute('for');
                if (!inputId) return false;
                const input = host.querySelector('#' + CSS.escape(inputId));
                return !!(input && input.checked === true);
                """,
                scope,
                target_text,
            )
            if mx_checked:
                return scale_clickable
    except Exception:
        pass

    # 1) DOM ciblé: wrappers .answer_options avec input.checkbox.radioQT (Metrix-like single-select)
    #    Critères strictement DOM (pas de règle provider globale).
    try:
        clicked_input = driver.execute_script(
            r"""
            const root = arguments[0] || document;
            const norm = s => (s || '')
              .toLowerCase()
              .normalize('NFKC')
              .replace(/\u00A0/g, ' ')
              .replace(/\s+/g, ' ')
              .trim();
            const needle = norm(arguments[1]);
            if (!needle) return null;

            const wrappers = Array.from(root.querySelectorAll('div.answer_options'));
            if (!wrappers.length) return null;

            // Guard DOM: on active cette logique uniquement si la structure radioQT est observée.
            const radioQtInputs = wrappers
              .map(w => w.querySelector('input[type="checkbox"].radioQT[name]'))
              .filter(Boolean);
            if (radioQtInputs.length < 2) return null;

            const nameCounts = new Map();
            for (const inp of radioQtInputs) {
              const k = (inp.getAttribute('name') || '').trim();
              if (!k) continue;
              nameCounts.set(k, (nameCounts.get(k) || 0) + 1);
            }
            const hasSharedName = Array.from(nameCounts.values()).some(v => v >= 2);
            if (!hasSharedName) return null;

            const candidates = [];
            for (const wrap of wrappers) {
              const inp = wrap.querySelector('input[type="checkbox"].radioQT[name]');
              if (!inp) continue;

              const labelNode = wrap.querySelector('.option_label, .option_label span, span');
              const txt = norm((labelNode && (labelNode.innerText || labelNode.textContent)) || wrap.innerText || wrap.textContent || '');
              if (!txt) continue;
              if (!(txt === needle || txt.includes(needle) || needle.includes(txt))) continue;

              candidates.push({wrap, inp, score: txt === needle ? 2 : 1, len: txt.length});
            }
            if (!candidates.length) return null;

            candidates.sort((a, b) => (b.score - a.score) || (b.len - a.len));
            const best = candidates[0];

            best.wrap.scrollIntoView({block: 'center', inline: 'center'});
            best.wrap.click();

            const selectedRadio = best.wrap.querySelector('div.option_radio');
            if (!(selectedRadio && selectedRadio.classList.contains('input_on'))) {
              return null;
            }

            return best.inp;
            """,
            scope,
            target_text,
        )
        if clicked_input:
            return clicked_input
    except Exception:
        pass

    # 2) Cas standard : <label for="id"> → <input id="id" type="checkbox">
    try:
        labels = (scope or driver).find_elements(
            By.XPATH,
            ".//label[normalize-space()!='' and contains("
            "translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ',"
            "'abcdefghijklmnopqrstuvwxyzàâäéèêëîïôöùûüç'),"
            f"{xpath_literal(needle)}"
            ")]"
        )

        for label in labels:
            fid = label.get_attribute("for")
            if not fid:
                continue

            try:
                cb = driver.find_element(By.ID, fid)
            except Exception:
                continue

            if cb.get_attribute("type") != "checkbox":
                continue

            scroll_into_view(driver, cb)

            # Guard DOM minimal: si le label contient un lien (<a>), on évite tout
            # clic sur le label/texte pour ne jamais déclencher de navigation parasite.
            # On agit uniquement sur l'input checkbox lié.
            label_has_link = False
            try:
                label_has_link = bool(label.find_elements(By.XPATH, ".//a[@href]"))
            except Exception:
                label_has_link = False

            if label_has_link:
                try:
                    driver.execute_script(
                        """
                        const cb = arguments[0];
                        if (!cb.checked) cb.checked = true;
                        cb.dispatchEvent(new Event('input', { bubbles: true }));
                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                        """,
                        cb,
                    )
                except Exception:
                    pass

            if not is_checked(cb):
                try:
                    cb.click()
                except Exception:
                    js_click(driver, cb)

            force_checkbox_events(driver, cb)

            if is_checked(cb):
                return cb

    except Exception:
        pass

    # 3) Cas checkbox ARIA / custom (role="checkbox")
    try:
        boxes = (scope or driver).find_elements(
            By.XPATH,
            ".//*[@role='checkbox' or @aria-checked]"
        )

        for box in boxes:
            txt = norm_soft(box.text or box.get_attribute("aria-label") or "")
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
