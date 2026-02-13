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

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import unicodedata
import re
import time

# Import depuis input_utils
from input_utils import (
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
        table = scope.find_element(By.XPATH, ".//table[contains(@class,'grid')]")
    except Exception:
        return False

    # index de colonne à partir des <th>
    col_idx = None
    heads = table.find_elements(By.XPATH, ".//tr[1]//th[normalize-space(.)!='']")
    for i, th in enumerate(heads):
        t = _n(th.text)
        if t and (t == colneedle or colneedle in t or t in colneedle):
            col_idx = i
            break

    # toutes les lignes de réponses
    rows = table.find_elements(By.XPATH, ".//tr[contains(@class,'row-elements')]")
    for tr in rows:
        # texte de ligne
        row_txt = ""
        for xp in (".//th", "./td[1]", "./td[2]"):
            try:
                raw = tr.find_element(By.XPATH, xp).text
                if raw and raw.strip():
                    row_txt = _n(raw)
                    break
            except Exception:
                continue
        # si un contexte est fourni, matcher la bonne ligne
        if rowneedle and not (rowneedle == row_txt or rowneedle in row_txt or row_txt in rowneedle):
            continue

        # cellule cible
        tds = tr.find_elements(By.XPATH, "./td")
        cell = None
        if col_idx is not None and len(tds) > col_idx:
            cell = tds[col_idx]
        else:
            for td in tds:
                try:
                    sig = _n(td.text or td.get_attribute("innerText") or "")
                    if sig and (sig == colneedle or colneedle in sig or sig in colneedle):
                        cell = td
                        break
                except Exception:
                    continue
        if cell is None:
            continue

        # éléments cliquables dans la cellule
        inp, lab = None, None
        try:
            inp = cell.find_element(By.XPATH, ".//input[@type='radio']")
        except Exception:
            pass
        try:
            lab = cell.find_element(By.XPATH, ".//label")
        except Exception:
            pass

        # clic
        try:
            target = lab or inp or cell
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
            try:
                target.click()
            except Exception:
                ActionChains(driver).move_to_element(target).click().perform()
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", target)
            except Exception:
                continue

        time.sleep(0.12)

        # vérification stricte
        if inp is None:
            try:
                inp = cell.find_element(By.XPATH, ".//input[@type='radio']")
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
            print(f"✓ Radio (Decipher) cochée: row='{context_hint}' → col='{label}' — source: input_radio.py")
            return True

        # secours: clic direct sur la cellule
        try:
            td = cell if "clickableCell" in (cell.get_attribute("class") or "") else cell.find_element(By.XPATH, "ancestor::td[1]")
            driver.execute_script("arguments[0].click();", td)
            time.sleep(0.12)
            if _is_checked(inp):
                print(f"✓ Radio (Decipher) cochée via <td>: row='{context_hint}' → col='{label}'")
                return True
        except Exception:
            pass

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

    rows = scope.find_elements(By.XPATH, ".//table[contains(@class,'grid')]//tr[contains(@class,'row-elements')]")
    if not rows:
        return False

    for tr in rows:
        try:
            th_text = ""
            try:
                th_text = tr.find_element(By.XPATH, ".//th").text
            except Exception:
                pass
            thn = _n(th_text)
            if not (needle == thn or needle in thn or thn in needle):
                try:
                    ltxt = tr.find_element(By.XPATH, ".//td//label").text
                    if not (needle in _n(ltxt) or _n(ltxt) in needle):
                        continue
                except Exception:
                    continue

            # 1) clique le <label>
            lab = None
            try:
                lab = tr.find_element(By.XPATH, ".//td[contains(@class,'clickableCell')]//label")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", lab)
                try:
                    lab.click()
                    return True
                except Exception:
                    driver.execute_script("arguments[0].click();", lab)
                    return True
            except Exception:
                pass

            # 2) force l'état sur l'input + events
            try:
                inp = tr.find_element(By.XPATH, ".//input[@type='radio']")
                driver.execute_script("""
                    const i = arguments[0];
                    i.checked = true;
                    try { i.dispatchEvent(new Event('input',  {bubbles:true})); } catch(e) {}
                    try { i.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
                    try { i.dispatchEvent(new Event('blur',   {bubbles:true})); } catch(e) {}
                """, inp)
            except Exception:
                pass

            # 3) secours : clique la cellule cliquable
            try:
                td = tr.find_element(By.XPATH, ".//td[contains(@class,'clickableCell')]")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", td)
                driver.execute_script("arguments[0].click();", td)
                return True
            except Exception:
                pass

            # 4) vérification
            try:
                chk = tr.find_element(By.XPATH, ".//input[@type='radio']")
                if chk.is_selected() or (chk.get_attribute("checked") or "").lower() in ("true", "checked"):
                    print(f"✓ Radio (Decipher strict) cochée : {label}")
                    return True
            except Exception:
                pass
        except Exception:
            continue

    return False


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
        labels = scope.find_elements(By.XPATH, ".//label[@for and normalize-space()!='']")
    except Exception:
        labels = []

    best, sc = None, -1.0
    for lab in labels:
        try:
            txt = _n(lab.text or lab.get_attribute("innerText") or "")
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
        inp = scope.find_element(By.XPATH, f".//*[@id={repr(fid)} and @type='radio']")
    except Exception:
        return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
        try:
            best.click()
        except Exception:
            ActionChains(driver).move_to_element(best).click().perform()
        time.sleep(0.05)
        if not getattr(inp, "is_selected", lambda: False)():
            driver.execute_script("""
                const r=arguments[0];
                try{ r.click(); }catch(e){}
                try{ r.checked=true; }catch(e){}
                try{ r.dispatchEvent(new Event('input',{bubbles:true})); }catch(e){}
                try{ r.dispatchEvent(new Event('change',{bubbles:true})); }catch(e){}
            """, inp)
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
        return bool(driver.execute_script(js, target_text))
    except Exception:
        return False


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

    wait = WebDriverWait(driver, 5)

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
        tr = scope.find_element(
            By.XPATH,
            f".//tr[.//th[normalize-space()=\"{label.strip()}\"]]"
        )
        lab = tr.find_element(By.XPATH, ".//td[contains(@class,'clickableCell')]//label")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", lab)
        wait.until(EC.element_to_be_clickable(lab))
        driver.execute_script("arguments[0].click();", lab)
        return True
    except Exception:
        pass

    # 2) Cas label direct
    try:
        lab = scope.find_element(
            By.XPATH,
            f".//label[normalize-space()=\"{label.strip()}\"]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", lab)
        wait.until(EC.element_to_be_clickable(lab))
        driver.execute_script("arguments[0].click();", lab)
        return True
    except Exception:
        pass

    # 3) Cas input radio voisin d'un texte
    try:
        inp = scope.find_element(
            By.XPATH,
            f".//input[@type='radio' and not(contains(@class,'disabled'))][ancestor::div[contains(@class,'question')]][following::text()[normalize-space()=\"{label.strip()}\"]]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
        driver.execute_script("arguments[0].click();", inp)
        return True
    except Exception:
        pass

    target = (label or "").strip()
    if not target:
        return False

    wait = WebDriverWait(driver, 4)
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
            hdr = scope.find_element(By.XPATH, ".//legend|.//h1|.//h2|.//h3|.//*[contains(@class,'question-text')][1]")
            anchor_y = hdr.rect.get("y", None)
        except Exception:
            try:
                anchor_y = scope.rect.get("y", None)
            except Exception:
                pass

    root = scope if scope is not None else driver

    # Pattern Angular/cc-radio ancré
    try:
        xp = ("(.//div[contains(@class,'fr-option') or contains(@class,'cc-radio') or contains(@class,'radio')])"
              f"//label[.//span[contains(@class,'cc-radio__label')] and contains(translate(normalize-space(string(.)),"
              f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), {xpath_literal(needle)})]")
        cands = root.find_elements(By.XPATH, xp)
        best = None
        best_dy = 1e9
        for lbl in cands:
            y = lbl.rect.get("y", 0)
            if anchor_y is not None and y + 1 < anchor_y:
                continue
            dy = abs((anchor_y or y) - y)
            if dy < best_dy:
                best, best_dy = lbl, dy
        if best is not None:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
            try:
                wait.until(EC.element_to_be_clickable(best)).click()
            except Exception:
                driver.execute_script("arguments[0].click();", best)
            try:
                fid = best.get_attribute("for")
                if fid:
                    inp = driver.find_element(By.ID, fid)
                    if not getattr(inp, "is_selected", lambda: False)():
                        driver.execute_script(
                            "arguments[0].checked=true;"
                            "try{arguments[0].dispatchEvent(new Event('input',{bubbles:true}));}catch(e){}"
                            "try{arguments[0].dispatchEvent(new Event('change',{bubbles:true}));}catch(e){}",
                            inp
                        )
            except Exception:
                pass
            return True
    except Exception:
        pass

    # Recherche générale ancrée dans le scope
    if scope is not None:
        cands = []
        cands += root.find_elements(By.XPATH, ".//label[normalize-space()!='']")
        cands += root.find_elements(By.XPATH, ".//*[@role='radio']")
        cands += root.find_elements(By.XPATH, ".//*[contains(@class,'answer') or contains(@class,'option') or contains(@class,'choice') or self::li]")
        best, best_dy = None, 1e9
        for el in cands:
            try:
                txt = _norm_radio(el.text or el.get_attribute("innerText") or "")
                if not (needle == txt or needle in txt or txt in needle):
                    continue
                y = el.rect.get("y", 0)
                if anchor_y is not None and y + 1 < anchor_y:
                    continue
                dy = abs((anchor_y or y) - y)
                if dy < best_dy:
                    best, best_dy = el, dy
            except Exception:
                continue
        if best is not None:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
            try:
                best.click()
            except Exception:
                ActionChains(driver).move_to_element(best).click().perform()
            return True

        # si on avait un scope mais rien de valide dedans, on STOP
        return False

    # Fallback JS générique
    if fallback_click_radio_js_generic(driver, label):
        print(f"✓ Radio cochée (fallback JS) : {label}")
        return True

    return False