"""
input_radio.py - Gestion des radio buttons pour input_handler

Ce module contient:
- Clic sur radio buttons par label
- Support Decipher grid radio (tables)
- Support cartes radio (Confirmit/Dynata)
- Support GridClick (échelles sans <input>)
- Fallbacks JS génériques

Dépendances:
- input_utils pour les fonctions utilitaires
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



import unicodedata
import re
import time

# Import depuis input_utils
from Survey.input_utils import (
    norms_txt,
    normt_txt,
    xpath_literal,
    safe_click,
    find_question_container_by_ctx,
    find_questions_container,
    find_context_container,
    split_typed_instruction,
    pause_here,
)
from Survey.log_utils import log_debug
from Survey.input_checkbox import click_qarts_widget_by_label


# =============================================================================
# HELPERS DE NORMALISATION RADIO
# =============================================================================

def _norm_radio(s: str) -> str:
    """Normalisation spécifique pour comparaison de labels radio."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).replace("\u00a0", " ").lower().strip()
    s = re.sub(r"[»«""\"'›→·•:]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


# =============================================================================
# DECIPHER GRID RADIO
# =============================================================================

def click_decipher_grid_radio(driver, label: str, context_hint: str = "") -> bool:
    """
    Decipher (table.grid) robuste :
    - repère la ligne par le 'context_hint' (texte de la ligne),
    - repère la colonne par 'label' (texte d'en-tête ou libellé dans la cellule),
    - clique le <label>/<td> cible, puis vérifie l'<input> exact.
    """
    def _n(s):
        if not s:
            return ""
        s = s.replace("\u00A0", " ").replace("'", "'").replace("`", "'")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", s, flags=re.S).strip().lower()

    rowneedle = _n(context_hint)
    colneedle = _n(label)

    try:
        scope = find_questions_container(driver, context_hint)
    except Exception:
        scope = None
    scope = scope or driver

    # table .grid
    try:
        table = scope.query_selector("xpath=" + ".//table[contains(@class,'grid')]")
    except Exception:
        return False

    # index de colonne à partir des <th>
    col_idx = None
    col_head_id = None
    heads = table.query_selector_all("xpath=" + ".//tr[1]//th[normalize-space(.)!='']")
    for i, th in enumerate(heads):
        t = _n(th.inner_text())
        if t and (t == colneedle or colneedle in t or t in colneedle):
            col_idx = i
            col_head_id = (th.get_attribute("id") or "").strip() or None
            break

    # toutes les lignes de réponses
    rows = table.query_selector_all("xpath=" + ".//tr[contains(@class,'row-elements')]")
    if len(rows) > 1 and not rowneedle:
        log_debug("[TARGET_DEBUG]", "decipher_grid_radio: row context missing on multi-row grid")
        return False

    for tr in rows:
        # texte de ligne
        row_txt = ""
        row_id = None
        for xp in (".//th", "./td[1]", "./td[2]"):
            try:
                node = tr.query_selector("xpath=" + xp)
                raw = node.inner_text()
                if raw and raw.strip():
                    row_txt = _n(raw)
                    row_id = (node.get_attribute("id") or "").strip() or None
                    break
            except Exception:
                continue
        # si un contexte est fourni, matcher la bonne ligne
        if rowneedle and not (rowneedle == row_txt or rowneedle in row_txt or row_txt in rowneedle):
            continue

        # cellule cible
        tds = tr.query_selector_all("xpath=" + "./td")
        cell = None
        if col_idx is not None and len(tds) > col_idx:
            cell = tds[col_idx]
        else:
            for td in tds:
                try:
                    sig = _n(td.inner_text() or td.get_attribute("innerText") or "")
                    if sig and (sig == colneedle or colneedle in sig or sig in colneedle):
                        cell = td
                        break
                except Exception:
                    continue
        if cell is None:
            continue

        # input natif exact (ligne + colonne)
        inp, lab = None, None
        target_by = "cell"
        try:
            row_inputs = tr.query_selector_all("xpath=" + ".//input[@type='radio']")
        except Exception:
            row_inputs = []

        if row_inputs and row_id and col_head_id:
            for cand in row_inputs:
                try:
                    labelled = (cand.get_attribute("aria-labelledby") or "").split()
                except Exception:
                    labelled = []
                if row_id in labelled and col_head_id in labelled:
                    inp = cand
                    target_by = "aria-labelledby"
                    break

        if inp is None:
            try:
                in_cell = cell.query_selector_all("xpath=" + ".//input[@type='radio']")
                if len(in_cell) == 1:
                    inp = in_cell[0]
            except Exception:
                pass

        if inp is None:
            log_debug("[TARGET_DEBUG]", f"decipher_grid_radio: native input not found row={context_hint!r} col={label!r}")
            if rowneedle:
                return False
            continue

        try:
            inp_id = (inp.get_attribute("id") or "").strip()
            if inp_id:
                lab = table.query_selector("xpath=" + f".//label[@for={xpath_literal(inp_id)}]")
        except Exception:
            pass
        if lab is None:
            try:
                lab = cell.query_selector("xpath=" + ".//label")
            except Exception:
                pass

        # clic
        log_debug("[TARGET_DEBUG]", f"decipher_grid_radio: target_found row={context_hint!r} col={label!r} by={target_by}")
        try:
            target = lab or inp or cell
            _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(target))
            try:
                target.click()
            except Exception:
                _handle(target).hover(); _handle(target).click()
        except Exception:
            try:
                _pw_page(driver).evaluate("(el) => el.click()", _handle(target))
            except Exception:
                log_debug("[TARGET_DEBUG]", f"decipher_grid_radio: click_failed row={context_hint!r} col={label!r}")
                if rowneedle:
                    return False
                continue
        log_debug("[TARGET_DEBUG]", f"decipher_grid_radio: click_attempted row={context_hint!r} col={label!r}")

        time.sleep(0.12)

        # vérification stricte
        if inp is None:
            try:
                inp = cell.query_selector("xpath=" + ".//input[@type='radio']")
            except Exception:
                inp = None

        def _is_checked(i):
            if i is None:
                return False
            try:
                return i.is_selected()
            except Exception:
                v = (i.get_attribute("checked") or i.get_attribute("aria-checked") or "").lower()
                return v in ("true", "checked", "1")

        if _is_checked(inp):
            log_debug("[TARGET_DEBUG]", f"decipher_grid_radio: native_verify=ok row={context_hint!r} col={label!r}")
            return True

        log_debug("[TARGET_DEBUG]", f"decipher_grid_radio: native_verify=ko row={context_hint!r} col={label!r}")
        if rowneedle:
            return False

    return False


def click_decipher_grid_radio_strict(driver, label: str, context_hint: str = "") -> bool:
    """
    Decipher (table.grid) strict :
    - scope par question (ctx),
    - trouve la <tr> dont le <th> contient le libellé,
    - clique le <label>, force checked + events sur l'<input>,
    - clique la cellule .clickableCell en secours.
    """
    def _n(s):
        if not s:
            return ""
        s = s.replace("\u00A0", " ")
        s = s.replace("'", "'").replace("'", "'").replace("´", "'").replace("`", "'")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", s).strip().lower()

    needle = _n(label)
    if not needle:
        return False

    try:
        scope = find_questions_container(driver, context_hint)
    except Exception:
        scope = None
    scope = scope or driver

    rows = scope.query_selector_all("xpath=" + ".//table[contains(@class,'grid')]//tr[contains(@class,'row-elements')]")
    if not rows:
        return False
    if len(rows) > 1 and not _n(context_hint or ""):
        log_debug("[TARGET_DEBUG]", "decipher_grid_radio_strict: row context missing on multi-row grid")
        return False

    for tr in rows:
        try:
            th_text = ""
            try:
                th_text = tr.query_selector("xpath=" + ".//th").text
            except Exception:
                pass
            thn = _n(th_text)
            if not (needle == thn or needle in thn or thn in needle):
                try:
                    ltxt = tr.query_selector("xpath=" + ".//td//label").text
                    if not (needle in _n(ltxt) or _n(ltxt) in needle):
                        continue
                except Exception:
                    continue

            # 1) clique le <label>
            lab = None
            inp = None
            verified = False
            try:
                lab = tr.query_selector("xpath=" + ".//td[contains(@class,'clickableCell')]//label")
                try:
                    fid = (lab.get_attribute("for") or "").strip()
                    if fid:
                        inp = tr.query_selector("xpath=" + f".//input[@type='radio' and @id={xpath_literal(fid)}]")
                except Exception:
                    inp = None
                _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(lab))
                try:
                    lab.click()
                except Exception:
                    _pw_page(driver).evaluate("(el) => el.click()", _handle(lab))
                time.sleep(0.08)
            except Exception:
                pass

            # 2) vérification native stricte
            try:
                chk = inp or tr.query_selector("xpath=" + ".//input[@type='radio']")
                if chk.is_selected() or (chk.get_attribute("checked") or "").lower() in ("true", "checked"):
                    verified = True
            except Exception:
                verified = False

            if verified:
                log_debug("[TARGET_DEBUG]", f"decipher_grid_radio_strict: native_verify=ok row={context_hint!r} col={label!r}")
                return True

            log_debug("[TARGET_DEBUG]", f"decipher_grid_radio_strict: native_verify=ko row={context_hint!r} col={label!r}")
            if _n(context_hint or ""):
                return False

            # 3) sans contexte de ligne, tenter une autre ligne
            continue
        except Exception:
            continue

    return False


def click_decipher_mx_carousel_radio(driver, label: str, context_hint: str = "") -> bool:
    """
    Decipher + MX Carousel overlay:
    - détecte un carousel dans le scope de la question,
    - sélectionne la carte de ligne via context_hint (si fourni),
    - clique la scale `.mx-carouselapp-scale[data-code=...]` correspondant au label,
    - valide la mise à jour de l'input radio natif ciblé.
    """

    def _n(s: str) -> str:
        if not s:
            return ""
        s = s.replace("\u00A0", " ")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", s).strip().lower()

    colneedle = _n(label)
    if not colneedle:
        return False

    rowneedle = _n(context_hint or "")

    try:
        scope = find_questions_container(driver, context_hint)
    except Exception:
        scope = None
    scope = scope or driver

    stage = None
    try:
        stage = driver.query_selector("xpath=" + ".//div[(contains(concat(' ',normalize-space(@class),' '),' mx-stage ') or starts-with(@id,'mx-stage-')) and .//*[contains(concat(' ',normalize-space(@class),' '),' mx-carouselapp-container ')]]")
    except Exception:
        stage = None

    if stage is None:
        return False

    js_click = r'''
        const stage = arguments[0];
        const rowNeedle = arguments[1] || "";
        const colNeedle = arguments[2] || "";

        const norm = (s) => (s || "")
            .toLowerCase()
            .normalize('NFKD')
            .replace(/[\u0300-\u036f]/g, '')
            .replace(/\u00a0/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();

        const activeCode = () => {
            const active = stage.querySelector('.mx-carouselapp-item.swiper-slide-active[data-code]');
            return active ? (active.getAttribute('data-code') || null) : null;
        };

        const items = Array.from(stage.querySelectorAll('.mx-carouselapp-item[data-code]'));
        const scales = Array.from(stage.querySelectorAll('.mx-carouselapp-scale[data-code]'));
        if (!scales.length) {
            return { ok: false, reason: 'scale_not_found' };
        }

        let targetScale = null;
        for (const el of scales) {
            const txt = norm(el.innerText || el.textContent || '');
            if (txt && (txt === colNeedle || txt.includes(colNeedle) || colNeedle.includes(txt))) {
                targetScale = el;
                break;
            }
        }
        if (!targetScale) {
            return { ok: false, reason: 'scale_label_not_matched' };
        }

        if (rowNeedle) {
            let targetRow = null;
            for (const item of items) {
                const txt = norm(item.innerText || item.textContent || '');
                if (txt && (txt === rowNeedle || txt.includes(rowNeedle) || rowNeedle.includes(txt))) {
                    targetRow = item;
                    break;
                }
            }
            if (!targetRow) {
                return { ok: false, reason: 'row_not_found' };
            }
            try { targetRow.scrollIntoView({block: 'center', inline: 'center'}); } catch(e) {}
            try { targetRow.click(); } catch(e) {
                try { targetRow.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); } catch(e2) {}
            }
        }

        const before = activeCode();
        const rowCode = targetRow ? (targetRow.getAttribute('data-code') || null) : before;
        const colCode = targetScale.getAttribute('data-code') || null;
        const stageId = stage.getAttribute('id') || '';
        const qLabel = stageId.startsWith('mx-stage-') ? stageId.slice('mx-stage-'.length) : '';

        try { targetScale.scrollIntoView({block: 'center', inline: 'center'}); } catch(e) {}
        try { targetScale.click(); } catch(e) {
            try { targetScale.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); } catch(e2) {
                return { ok: false, reason: 'scale_click_failed' };
            }
        }

        return {
            ok: true,
            activeBefore: before,
            itemCount: items.length,
            rowCode,
            colCode,
            qLabel,
        };
    '''

    try:
        click_result = _pw_page(driver).evaluate("(arg) => {" + js_click + "}", stage, rowneedle, colneedle) or {}
    except Exception:
        return False

    if not click_result.get("ok"):
        log_debug("[TARGET_DEBUG]", f"mx carousel click skipped: reason={click_result.get('reason')!r}")
        return False

    row_code = click_result.get("rowCode")
    col_code = click_result.get("colCode")
    q_label = click_result.get("qLabel")
    if not (row_code and col_code and q_label):
        log_debug(
            "[TARGET_DEBUG]",
            f"mx carousel native verify skipped: row={row_code!r} col={col_code!r} q={q_label!r}",
        )
        return False

    row_token = f"{q_label}_{row_code}_left"
    col_token = f"{q_label}_{col_code}"

    # Verify carousel selection via polling (was WebDriverWait)
    try:
        deadline = __import__('time').time() + 1.5
        verified = False
        while __import__('time').time() < deadline:
            try:
                verified = _pw_page(driver).evaluate(
                    """([rowToken, colToken]) => {
                        const inputs = Array.from(document.querySelectorAll('input[type="radio"]'));
                        for (const inp of inputs) {
                            const labelled = (inp.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
                            if (!labelled.includes(rowToken) || !labelled.includes(colToken)) continue;
                            if (inp.checked) return true;
                            const checkedAttr = (inp.getAttribute('checked') || '').toLowerCase();
                            if (checkedAttr === 'checked' || checkedAttr === 'true' || checkedAttr === '1') return true;
                        }
                        return false;
                    }""",
                    [row_token, col_token]
                )
                if verified:
                    break
            except Exception:
                pass
            __import__('time').sleep(0.1)
    except Exception:
        log_debug(
            "[TARGET_DEBUG]",
            f"mx carousel native verify timeout: row={row_token!r} col={col_token!r}",
        )
        return False

    return True


def click_radio_label_in_scope(driver, scope, label_text: str) -> bool:
    """Decipher/Confirmit : coche une radio via <label for=...> **dans le scope**."""
    def _n(s):
        if not s:
            return ""
        s = unicodedata.normalize("NFKC", s).replace("\u00A0", " ").lower()
        s = re.sub(r"[»«""\"'›→·•:]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    needle = _n(label_text)
    if not needle:
        return False

    labels = []
    try:
        labels = scope.query_selector_all("xpath=" + ".//label[@for and normalize-space()!='']")
    except Exception:
        labels = []

    best, sc = None, -1.0
    for lab in labels:
        try:
            txt = _n(lab.inner_text() or lab.get_attribute("innerText") or "")
            if not txt:
                continue
            score = 1.0 if (needle == txt or needle in txt or txt in needle) else 0.0
            if score > sc:
                best, sc = lab, score
        except Exception:
            continue

    if not best:
        return False

    fid = best.get_attribute("for")
    if not fid:
        return False
    try:
        inp = scope.query_selector("xpath=" + f".//*[@id={repr(fid)} and @type='radio']")
    except Exception:
        return False

    try:
        _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(best))
        try:
            best.click()
        except Exception:
            _handle(best).hover(); _handle(best).click()
        time.sleep(0.05)
        if not getattr(inp, "is_selected", lambda: False)():
            _handle(inp).evaluate("""(_el) => {
                const r=_el;
                try{ r.click(); }catch(e){}
                try{ r.checked=true; }catch(e){}
                try{ r.dispatchEvent(new Event('input',{bubbles:true})); }catch(e){}
                try{ r.dispatchEvent(new Event('change',{bubbles:true})); }catch(e){}
}""")
        return True
    except Exception:
        return False


# =============================================================================
# FALLBACK JS GÉNÉRIQUE
# =============================================================================

def fallback_click_radio_js_generic(driver, target_text: str) -> bool:
    """
    Fallback JS générique (Decipher/Confirmit & co) pour cocher une radio quand
    les clics classiques échouent. Stratégie :
      1) label[for] -> input#id
      2) match [role=radio]/input[type=radio] via aria/texte voisin
      3) proximité spatiale entre le texte et la radio la plus proche
    Force checked=true + dispatch 'input' & 'change'.
    """
    js = r"""
    const norm = s => (s||'').toLowerCase()
      .normalize('NFKC').replace(/\u00A0/g,' ')
      .replace(/[»«""\"\\'›→·•:]/g,'').replace(/\s+/g,' ').trim();
    const needle = norm(arguments[0]);

    // 1) label[for] -> input#id
    for (const lab of document.querySelectorAll('label')){
      const txt = norm(lab.innerText || lab.textContent || '');
      if (!txt) continue;
      if (txt===needle || txt.includes(needle) || needle.includes(txt)) {
        const fid = lab.getAttribute('for');
        if (fid) {
          const inp = document.getElementById(fid);
          if (inp && (inp.type||'').toLowerCase()==='radio') {
            try { lab.click(); } catch(e){}
            try { inp.click(); } catch(e){}
            try { inp.checked = true; } catch(e){}
            try { inp.dispatchEvent(new Event('input',{bubbles:true})); } catch(e){}
            try { inp.dispatchEvent(new Event('change',{bubbles:true})); } catch(e){}
            return !!inp.checked;
          }
        }
      }
    }

    // 2) [role=radio]/input[type=radio] avec aria/texte voisins
    const radios = Array.from(document.querySelectorAll('input[type=radio], [role=radio]'))
      .filter(r => r.offsetParent !== null);
    for (const r of radios){
      const aria = norm(r.getAttribute('aria-label')||'');
      const lab  = norm((r.closest('label')||{}).innerText||'');
      const sib  = norm((r.parentElement||{}).innerText||'');
      if ((aria && (aria.includes(needle)||needle.includes(aria))) ||
          (lab  && (lab.includes(needle)||needle.includes(lab))) ||
          (sib  && (sib.includes(needle)||needle.includes(sib)))) {
        try { r.click(); } catch(e){}
        try { r.checked = true; } catch(e){}
        try { r.dispatchEvent(new Event('input',{bubbles:true})); } catch(e){}
        try { r.dispatchEvent(new Event('change',{bubbles:true})); } catch(e){}
        return !!(r.checked || (r.getAttribute('aria-checked')||'').toLowerCase()==='true');
      }
    }

    // 3) Proximité : texte -> radio la plus proche verticalement
    function center(el){ const b=el.getBoundingClientRect(); return {x:b.left+b.width/2, y:b.top+b.height/2}; }
    const texts = Array.from(document.querySelectorAll('label, span, div, li'))
      .filter(e => norm(e.innerText||e.textContent||'').includes(needle));
    let bestRadio=null, bestD=1e9;
    for (const t of texts){
      const ct = center(t);
      for (const r of radios){
        const cr = center(r);
        const d = Math.abs(ct.y - cr.y) + Math.abs(ct.x - cr.x)*0.3;
        if (d < bestD){ bestD=d; bestRadio=r; }
      }
    }
    if (bestRadio){
      try { bestRadio.click(); } catch(e){}
      try { bestRadio.checked = true; } catch(e){}
      try { bestRadio.dispatchEvent(new Event('input',{bubbles:true})); } catch(e){}
      try { bestRadio.dispatchEvent(new Event('change',{bubbles:true})); } catch(e){}
      return !!(bestRadio.checked || (bestRadio.getAttribute('aria-checked')||'').toLowerCase()==='true');
    }
    return false;
    """
    try:
        return bool(_pw_page(driver).evaluate("(arg) => {" + js + "}", target_text))
    except Exception:
        return False


# =============================================================================
# KANTAR / mrIWeb ROWPICKER RADIO
# =============================================================================

def click_kantar_rowpicker_radio(driver, label: str) -> bool:
    """
    Kantar/mrIWeb _rowpicker (React overlay, div[id^='container_']).

    Guard DOM strict: div[id^='container_'] [data-test='main-contain']._rowpicker
    Cible le div[tabindex='0'][style*='inset'] overlay de la carte correspondant au label.
    Vérifie l'input[type=radio] natif de la couche classique (label[for]).

    Non-régression: guard exclu pour div[id^='sq-QARTS-container-'] (Decipher/LifePoints).
    """
    _JS_FIND = r"""
    const norm = s => (s || '')
      .toLowerCase().normalize('NFKC')
      .replace(/ /g, ' ')
      .replace(/[»«“”"'‘’›→·•:]/g, '')
      .replace(/\s+/g, ' ').trim();
    const needle = norm(arguments[0]);
    if (!needle) return null;

    const pickers = Array.from(document.querySelectorAll(
      "div[id^='container_'] [data-test='main-contain']._rowpicker"
    ));
    if (!pickers.length) return null;

    for (const picker of pickers) {
      for (const lab of Array.from(picker.querySelectorAll('label'))) {
        const txt = norm(lab.innerText || lab.textContent || '');
        if (!txt || !(txt === needle || txt.includes(needle) || needle.includes(txt))) continue;

        // Remonter au conteneur flex direct parent de l'overlay (div[dir="ltr"] le plus proche)
        const cardContainer = lab.closest('div[dir="ltr"]');
        if (!cardContainer) continue;

        const overlay = cardContainer.querySelector('div[tabindex="0"]');
        if (!overlay) continue;

        // Vérifier que c'est bien l'overlay interactif (cursor dans le style inline)
        const st = overlay.getAttribute('style') || '';
        if (!st.includes('cursor')) continue;

        try { overlay.scrollIntoView({block: 'center', inline: 'center'}); } catch(e) {}
        return overlay;
      }
    }
    return null;
    """

    _JS_VERIFY = r"""
    const norm = s => (s || '')
      .toLowerCase().normalize('NFKC')
      .replace(/ /g, ' ')
      .replace(/[»«“”‘’›→·•:]/g, '')
      .replace(/\s+/g, ' ').trim();
    const needle = norm(arguments[0]);
    const pickers = Array.from(document.querySelectorAll(
      "div[id^='container_'] [data-test='main-contain']._rowpicker"
    ));
    for (const picker of pickers) {
      for (const lab of Array.from(picker.querySelectorAll('label'))) {
        const txt = norm(lab.innerText || lab.textContent || '');
        if (!txt || !(txt === needle || txt.includes(needle) || needle.includes(txt))) continue;
        const card = lab.closest('div[dir="ltr"]');
        if (!card) continue;
        const transDiv = card.querySelector('div[style*="transition: background-color"]');
        if (!transDiv) continue;
        const bg = transDiv.style.backgroundColor || window.getComputedStyle(transDiv).backgroundColor;
        return !!(bg && bg !== 'rgb(255, 255, 255)' && bg !== 'rgba(255, 255, 255, 1)' && bg !== '');
      }
    }
    return false;
    """

    try:
        overlay = _pw_page(driver).evaluate("(arg) => {" + _JS_FIND + "}", label)
    except Exception:
        return False

    if overlay is None:
        return False

    try:
        overlay.click()
    except Exception:
        try:
            _handle(overlay).hover(); _handle(overlay).click()
        except Exception:
            log_debug("[TARGET_DEBUG]", f"kantar_rowpicker: overlay_click_failed label={label!r}")
            return False

    time.sleep(0.15)

    try:
        ok = bool(_pw_page(driver).evaluate("(arg) => {" + _JS_VERIFY + "}", label))
    except Exception:
        ok = False

    log_debug("[TARGET_DEBUG]", f"kantar_rowpicker: native_verify={'ok' if ok else 'ko'} label={label!r}")
    return ok


# =============================================================================
# FONCTION PRINCIPALE CLICK_RADIO_BY_LABEL
# =============================================================================

def click_radio_by_label(driver, label: str, context_hint: str | None = None) -> bool:
    """
    Coche le bouton radio correspondant à `label`.
    Accepte aussi 'Libellé //// radio'. Couvre :
    - <label for="..."> + <input type=radio id="...">
    - input radio voisin de <label>
    - conteneurs ARIA role="radio"
    - blocs stylés (answer/option/choice)
    - Decipher grid radio
    - Confirmit cards/GridClick
    
    Args:
        driver: WebDriver
        label: texte du label radio à sélectionner
        context_hint: contexte de question pour scoping
    
    Returns:
        True si radio cochée avec succès
    """
    # QARTS widget (Decipher/LifePoints) : double structure visuelle + grille cachée.
    # Guard DOM strict : div[id^="sq-QARTS-container-"] + div._rowpicker
    # ET div.hidden.answers avec inputs natifs (size=0, non-interactables directement).
    try:
        if click_qarts_widget_by_label(driver, label):
            return True
    except Exception:
        pass

    # Kantar/mrIWeb _rowpicker (React overlay, div[id^="container_"]).
    # Guard DOM strict : div[id^='container_'] [data-test='main-contain']._rowpicker
    try:
        if click_kantar_rowpicker_radio(driver, label):
            return True
    except Exception:
        pass

    # Confirmit GridClick (échelle à droite, pas de <input>)
    try:
        # Import dynamique pour éviter circular import
        from input_handler import click_confirmit_gridclick
        if click_confirmit_gridclick(driver, label=label, context_hint=context_hint):
            print(f"✓ Radio(GridClick) « {label} ». source: input_radio.py")
            return True
    except Exception:
        pass

    try:
        if click_decipher_mx_carousel_radio(driver, label, context_hint or ""):
            return True
    except Exception:
        pass

    try:
        if click_decipher_grid_radio(driver, label, context_hint or ""):
            return True
    except Exception:
        pass

    try:
        if click_decipher_grid_radio_strict(driver, label, context_hint or ""):
            return True
    except Exception:
        pass

    lbl = norms_txt(label)
    container = find_question_container_by_ctx(driver, context_hint)
    scope = container if container is not None else driver

    
    # Cartes Confirmit/Dynata (pas d'<input> visible)
    try:
        from input_handler import click_radio_cardlike_js
        if click_radio_cardlike_js(driver, label=label, context_hint=context_hint):
            print(f"✓ Radio(card) « {label} ». source: input_radio.py")
            return True
    except Exception:
        pass

    # Confirmit/Dynata ImageSelector
    try:
        from input_handler import click_confirmit_image_selector
        if click_confirmit_image_selector(driver, label=label, context_hint=context_hint):
            print(f"✓ Radio(ImageSelector) « {label} ». source: input_radio.py")
            return True
    except Exception:
        pass

    # 1) Cas table (decipherinc)
    try:
        tr = scope.query_selector("xpath=" + f".//tr[.//th[normalize-space()=\"{label.strip()}\"]]")
        lab = tr.query_selector("xpath=" + ".//td[contains(@class,'clickableCell')]//label")
        _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(lab))
        _pw_page(driver).wait_for_function("el => !el.disabled && el.getBoundingClientRect().width > 0", _handle(lab), timeout=5000)
        _pw_page(driver).evaluate("(el) => el.click()", _handle(lab))
        return True
    except Exception:
        pass

    # 2) Cas label direct
    try:
        lab = scope.query_selector("xpath=" + f".//label[normalize-space()=\"{label.strip()}\"]")
        _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(lab))
        _pw_page(driver).wait_for_function("el => !el.disabled && el.getBoundingClientRect().width > 0", _handle(lab), timeout=5000)
        _pw_page(driver).evaluate("(el) => el.click()", _handle(lab))
        return True
    except Exception:
        pass

    # 3) Cas input radio voisin d'un texte
    try:
        inp = scope.query_selector("xpath=" + f".//input[@type='radio' and not(contains(@class,'disabled'))][ancestor::div[contains(@class,'question')]][following::text()[normalize-space()=\"{label.strip()}\"]]")
        _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(inp))
        _pw_page(driver).evaluate("(el) => el.click()", _handle(inp))
        return True
    except Exception:
        pass

    target = (label or "").strip()
    if not target:
        return False

        label, _itype = split_typed_instruction(label)
    needle = _norm_radio(label)
    if not needle:
        return False

    # Essai prioritaire dans le conteneur de la question
    scope = find_context_container(driver, context_hint)
    if scope is not None:
        if click_radio_label_in_scope(driver, scope, label):
            print(f"✓ Radio cochée (Decipher/table via scope) : {label}")
            try:
                setattr(driver, "last_action_success", True)
                setattr(driver, "_post_action_t0", time.time())
            except Exception:
                pass
            return True

    anchor_y = None
    if scope is not None:
        try:
            hdr = scope.query_selector("xpath=" + ".//legend|.//h1|.//h2|.//h3|.//*[contains(@class,'question-text')][1]")
            anchor_y = hdr.bounding_box() or {}.get("y", None)
        except Exception:
            try:
                anchor_y = scope.bounding_box() or {}.get("y", None)
            except Exception:
                pass

    root = scope if scope is not None else driver

    # Pattern Angular/cc-radio ancré
    try:
        xp = ("(.//div[contains(@class,'fr-option') or contains(@class,'cc-radio') or contains(@class,'radio')])"
              f"//label[.//span[contains(@class,'cc-radio__label')] and contains(translate(normalize-space(string(.)),"
              f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), {xpath_literal(needle)})]")
        cands = root.query_selector_all("xpath=" + xp)
        best = None
        best_dy = 1e9
        for lbl in cands:
            y = lbl.bounding_box() or {}.get("y", 0)
            if anchor_y is not None and y + 1 < anchor_y:
                continue
            dy = abs((anchor_y or y) - y)
            if dy < best_dy:
                best, best_dy = lbl, dy
        if best is not None:
            _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(best))
            try:
                _pw_page(driver).wait_for_function("el => !el.disabled && el.getBoundingClientRect().width > 0", _handle(best), timeout=5000).click()
            except Exception:
                _pw_page(driver).evaluate("(el) => el.click()", _handle(best))
            try:
                fid = best.get_attribute("for")
                if fid:
                    inp = driver.query_selector(f"#{fid}")
                    if not getattr(inp, "is_selected", lambda: False)():
                        _handle(inp).evaluate(
                            "(_el) => { _el.checked=true;"
                            "try{_el.dispatchEvent(new Event('input',{bubbles:true}));}catch(e){}"
                            "try{_el.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){} }"
                        )
            except Exception:
                pass
            return True
    except Exception:
        pass

    # Recherche générale ancrée dans le scope
    if scope is not None:
        cands = []
        cands += root.query_selector_all("xpath=" + ".//label[normalize-space()!='']")
        cands += root.query_selector_all("xpath=" + ".//*[@role='radio']")
        cands += root.query_selector_all("xpath=" + ".//*[contains(@class,'answer') or contains(@class,'option') or contains(@class,'choice') or self::li]")
        best, best_dy = None, 1e9
        for el in cands:
            try:
                txt = _norm_radio(el.inner_text() or el.get_attribute("innerText") or "")
                if not (needle == txt or needle in txt or txt in needle):
                    continue
                y = el.bounding_box() or {}.get("y", 0)
                if anchor_y is not None and y + 1 < anchor_y:
                    continue
                dy = abs((anchor_y or y) - y)
                if dy < best_dy:
                    best, best_dy = el, dy
            except Exception:
                continue
        if best is not None:
            _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(best))
            try:
                best.click()
            except Exception:
                _handle(best).hover(); _handle(best).click()
            return True

        # si on avait un scope mais rien de valide dedans, on STOP
        return False

    # Fallback JS générique
    if fallback_click_radio_js_generic(driver, label):
        print(f"✓ Radio cochée (fallback JS) : {label}")
        return True

    return False
