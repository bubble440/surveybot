from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
import unicodedata, re, time
from selenium.webdriver.support.ui import Select as _Sel
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
)

# --- Placeholders usuels (à compléter si besoin)
_DROPDOWN_PLACEHOLDERS = {
    "veuillez sélectionner", "veuillez selectionner", "sélectionner", "selectionner",
    "please select", "select", "choose", "choose an option", "choose option",
    "select one", "select…", "select...", "seleccione", "seleziona", "wählen",
}

def _norm_txt(s: str) -> str:
    # trim + lowercase + remplace NBSP/ponctuation légère
    if not s:
        return ""
    t = s.strip().lower().replace("\u00a0", " ")
    for ch in ("«", "»", "“", "”", '"', "’", "'", "›", "•", "·", "…", ":"):
        t = t.replace(ch, " ")
    while "  " in t:
        t = t.replace("  ", " ")
    return t

PLACEHOLDER_TOKENS = {
    "sélectionnez",
    "selectionnez",
    "sélectionner",
    "selectionner",
    "choisir",
    "choisissez",
    "select",
    "select…",
    "select ...",
    "select one",
    "choose",
    "please select",
    "pick one",
}

CTA_SYNONYMS = [
    "accepter et commencer",
    "accepter",
    "commencer",
    "continuer",
    "suivant",
    "soumettre",
    "valider",
    "start",
    "next",
    "continue",
    "submit",
    "proceed",
]

def _norms_txt(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def _find_question_container_by_ctx(driver, context_hint: str):
    """
    Retourne le <div class="question ..."> le plus pertinent pour le ctx.
    On score les containers par nombre de tokens du ctx présents dans le h1.question-text.
    """
    if not context_hint:
        return None

    tokens = [t for t in _norms_txt(context_hint).split() if len(t) >= 4][:10]  # mots utiles
    if not tokens:
        return None

    candidates = driver.find_elements(By.CSS_SELECTOR, "div.question")
    best, best_score = None, 0
    for c in candidates:
        try:
            h1 = c.find_element(By.CSS_SELECTOR, "h1.question-text")
            q = _norms_txt(h1.get_attribute("innerText") or h1.text)
        except Exception:
            continue
        score = sum(1 for t in tokens if t in q)
        if score > best_score:
            best, best_score = c, score

    return best if best_score > 0 else None

DEBUG_PAUSE = True

def pause_here(msg="Appuie sur Entrée pour continuer…"):
    if not DEBUG_PAUSE:
        return
    try:
        input(f"[PAUSE] {msg}")
    except (EOFError, KeyboardInterrupt):
        pass

# === [NOUVEAU] Toggle open-ended (chevron) ================================

def _has_visible_open_ended_field(container):
    """Vérifie s'il y a déjà un champ 'réponse libre' visible sous cette question."""
    try:
        # 1) textarea visible
        areas = container.find_elements(By.XPATH, ".//textarea[not(@disabled) and not(@readonly)]")
        for a in areas:
            try:
                if a.is_displayed() and a.rect.get("height", 0) > 5 and a.rect.get("width", 0) > 20:
                    return True
            except Exception:
                continue
        # 2) input texte 'open-ended' (plus rare)
        inputs = container.find_elements(
            By.XPATH,
            ".//input[(@type='text' or @type='search' or not(@type)) and not(@disabled) and not(@readonly)]"
        )
        for i in inputs:
            try:
                ph = (i.get_attribute("placeholder") or "").lower()
                nm = (i.get_attribute("name") or "").lower() + " " + (i.get_attribute("id") or "").lower()
                if i.is_displayed() and (len(ph) > 0 or "open" in nm or "free" in nm or "ended" in nm):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def ensure_open_ended_open(
    driver,
    context_hint: str = "",
    desired_state: str = "open",
    max_retries: int = 2,
    postcheck: str = "field",
    selectors_override: list[str] | None = None,
) -> bool:
    """
    Ouvre (si besoin) le champ 'réponse libre' déclenché par un petit chevron.
    - Scopé par le contexte de la question.
    - Multi-fallbacks de clic.
    - Post-vérification : apparition d'un textarea OU flip de l'icône.

    Params:
      - context_hint: texte de la question pour borner la recherche.
      - desired_state: pour l’instant « open » (idempotent).
      - postcheck: 'field' (textarea) ou 'icon' (flip chevron).
    """
    try:
        container = _find_question_container_by_ctx(driver, context_hint) or driver
    except Exception:
        container = driver

    # Si déjà ouvert et on veut "open" → rien à faire
    if desired_state == "open":
        try:
            if _has_visible_open_ended_field(container):
                print("Open-ended: déjà ouvert (précheck). source: input_handler.py")
                return True
        except Exception:
            pass

    # Sélecteurs ciblant le chevron/toggle open-ended
    default_selectors = [
        ".//span[contains(@ng-click,'handleOpenEnded')]",
        ".//span[contains(@ng-click,'openEnded') or contains(@ng-click,'openended')]",
        ".//i[contains(@class,'fa-chevron-down') or contains(@class,'fa-chevron-up')]/ancestor::*[self::span or self::div or self::button][1]",
        ".//span[contains(@class,'chevron') or contains(@class,'arrow')]",
        ".//i[contains(@class,'chevron') or contains(@class,'arrow')]/parent::*",
    ]
    selectors = selectors_override or default_selectors

    # Collecte candidats
    candidates = []
    for xp in selectors:
        try:
            candidates.extend(container.find_elements(By.XPATH, xp))
        except Exception:
            continue

    # Filtre visibles et 'cliquables' (petits hitbox autorisés)
    visible = []
    for el in candidates:
        try:
            if el.is_displayed():
                box = el.rect or {}
                # Ignore éléments totalement microscopiques
                if box.get("width", 0) >= 6 and box.get("height", 0) >= 6:
                    visible.append(el)
        except Exception:
            continue

    # Tri léger: les éléments avec icône <i>/<svg> d’abord
    def _score(el):
        sc = 0
        try:
            if el.find_elements(By.TAG_NAME, "i") or el.find_elements(By.TAG_NAME, "svg"):
                sc += 2
        except Exception:
            pass
        try:
            r = el.rect
            # Un tout petit peu de priorité à des hitbox pas trop minuscules
            sc += min(int((r.get("width", 0) * r.get("height", 0)) / 400), 3)
        except Exception:
            pass
        return sc

    visible.sort(key=_score, reverse=True)

    # Clics (multi-fallback) + post-check
    for el in visible[:8]:
        for attempt in range(max_retries):
            try:
                # 1) scroll & clic « normal »
                if _safe_click(driver, el, trace="open_ended_toggle"):
                    time.sleep(0.15)
                    return True

            except Exception:
                # 3) dernier essai via ActionChains
                try:
                    ActionChains(driver).move_to_element(el).pause(0.05).click().perform()
                    return True
                except Exception:
                    pass

            # Post-vérif : champ visible ?
            try:
                if postcheck == "field":
                    if _has_visible_open_ended_field(container):
                        print("✅ Open-ended: champ affiché (postcheck=field). source: input_handler.py")
                        return True
                else:
                    # Vérifie flip d’icône (down → up)
                    if container.find_elements(By.XPATH, ".//i[contains(@class,'fa-chevron-up')]"):
                        print("✅ Open-ended: icône passée en 'up' (postcheck=icon). source: input_handler.py")
                        return True
            except Exception:
                pass

    print("↪️ Open-ended: impossible d’ouvrir via chevron. source: input_handler.py")
    return False
# === [FIN NOUVEAU] =========================================================

def _normt_txt(s: str) -> str:
    if s is None:
        return ""
    # normalisation robuste (espaces, accents, apostrophes typographiques)
    s = s.replace("’", "'").replace("´", "'").replace("`", "'").replace("‘", "'")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

def _find_questions_container(driver, context_hint: str):
    """Essaie de limiter la recherche au bloc <div class='question'> qui porte le H1 du contexte."""
    ctx = (context_hint or "").strip()
    if not ctx:
        return None
    # Tolérer variations (‘ vs ') et espaces
    candidates = driver.find_elements(
        By.XPATH,
        "//div[contains(@class,'question')][.//h1 or .//h2]"
    )
    norm_ctx = _normt_txt(ctx)
    for q in candidates:
        try:
            title = ""
            try:
                title = q.find_element(By.XPATH, ".//h1").text
            except Exception:
                try:
                    title = q.find_element(By.XPATH, ".//h2").text
                except Exception:
                    title = ""
            if _normt_txt(title).find(norm_ctx) != -1:
                return q
        except Exception:
            continue
    return None

def _click_decipher_grid_radio(driver, label: str, context_hint: str = "") -> bool:
    """
    Decipher (table.grid) robuste :
    - repère la ligne par le 'context_hint' (texte de la ligne),
    - repère la colonne par 'label' (texte d'en-tête ou libellé dans la cellule),
    - clique le <label>/<td> cible, puis vérifie l'<input> exact.
    """
    def _n(s):
        if not s: return ""
        s = s.replace("\u00A0"," ").replace("’","'").replace("`","'")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+"," ", s, flags=re.S).strip().lower()

    rowneedle = _n(context_hint)
    colneedle = _n(label)

    # scope = bloc question si dispo
    try:
        scope = _find_questions_container(driver, context_hint)  # déjà dans ce fichier
    except Exception:
        scope = None
    scope = scope or driver

    # 0) table .grid
    try:
        table = scope.find_element(By.XPATH, ".//table[contains(@class,'grid')]")
    except Exception:
        return False

    # 1) index de colonne à partir des <th>
    col_idx = None
    heads = table.find_elements(By.XPATH, ".//tr[1]//th[normalize-space(.)!='']")
    for i, th in enumerate(heads):
        t = _n(th.text)
        if t and (t==colneedle or colneedle in t or t in colneedle):
            col_idx = i
            break

    # 2) toutes les lignes de réponses
    rows = table.find_elements(By.XPATH, ".//tr[contains(@class,'row-elements')]")
    for tr in rows:
        # texte de ligne (th ou 1ʳᵉ/2ᵉ cellule texte)
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
        if rowneedle and not (rowneedle==row_txt or rowneedle in row_txt or row_txt in rowneedle):
            continue

        # 3) cellule cible (par index de colonne si connu, sinon par texte dans la cellule)
        tds = tr.find_elements(By.XPATH, "./td")
        cell = None
        if col_idx is not None and len(tds) > col_idx:
            cell = tds[col_idx]
        else:
            # fallback: chercher le texte de colonne dans la cellule
            for td in tds:
                try:
                    sig = _n(td.text or td.get_attribute("innerText") or "")
                    if sig and (sig==colneedle or colneedle in sig or sig in colneedle):
                        cell = td; break
                except Exception:
                    continue
        if cell is None:
            continue

        # 4) éléments cliquables dans la cellule
        inp, lab = None, None
        try: inp = cell.find_element(By.XPATH, ".//input[@type='radio']")
        except Exception: pass
        try: lab = cell.find_element(By.XPATH, ".//label")
        except Exception: pass

        # 5) clic (label → input → td), sans double-clic ni re-toggle
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

        # 6) vérification stricte sur l'input de la cellule
        if inp is None:
            try: inp = cell.find_element(By.XPATH, ".//input[@type='radio']")
            except Exception: inp = None

        def _is_checked(i):
            if i is None: return False
            try:
                return i.is_selected()
            except Exception:
                v = (i.get_attribute("checked") or i.get_attribute("aria-checked") or "").lower()
                return v in ("true","checked","1")

        if _is_checked(inp):
            print(f"✅ Radio (Decipher) cochée: row='{context_hint}' × col='{label}' — source: input_handler.py")
            return True
        # sinon on tente une fois un clic direct sur la cellule (skins 'clickableCell')
        try:
            td = cell if "clickableCell" in (cell.get_attribute("class") or "") else cell.find_element(By.XPATH, "ancestor::td[1]")
            driver.execute_script("arguments[0].click();", td)
            time.sleep(0.12)
            if _is_checked(inp):
                print(f"✅ Radio (Decipher) cochée via <td>: row='{context_hint}' × col='{label}'")
                return True
        except Exception:
            pass
        # on n'abandonne pas la ligne trop vite : on laisse la boucle continuer d'autres lignes
    return False

DATE_HINTS = {
    "month": {"placeholders": ("mm",), "names": ("month", "mm") , "maxlen": 2},
    "day":   {"placeholders": ("dd",), "names": ("day", "dd")   , "maxlen": 2},
    "year":  {"placeholders": ("yyyy","yy"), "names": ("year", "yyyy", "yy"), "maxlen": 4},
}

def _find_inputs_by_hint(driver, kind: str):
    """Retourne une liste d'<input> candidates pour month/day/year."""
    if kind not in DATE_HINTS: 
        return []
    H = DATE_HINTS[kind]
    phs = " or ".join([f"translate(@placeholder,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{p}'" for p in H["placeholders"]])
    als = f"translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{kind}'"
    nm  = f"contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{kind}')"
    iid = f"contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{kind}')"
    # NB: certains sites mettent le placeholder sur le parent, donc on tente large.
    xp = f"//input[({phs}) or {als} or {nm} or {iid}]"
    try:
        return driver.find_elements(By.XPATH, xp)
    except Exception:
        return []

def _set_input_value_with_events(driver, el, value: str):
    """Set .value, send_keys, et déclenche input/change/blur pour satisfaire le JS."""
    try:
        el.click()
    except Exception:
        pass
    try:
        el.clear()
    except Exception:
        pass
    try:
        # Ctrl+A puis Backspace : efface dans 99% des UIs
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.BACKSPACE)
        el.send_keys(value)
    except Exception:
        # dernier recours: js direct + events
        driver.execute_script("arguments[0].value = arguments[1];", el, value)
    # événements attendus
    driver.execute_script("""
        const e = arguments[0];
        for (const t of ["input","change","blur"]) {
          try { e.dispatchEvent(new Event(t, {bubbles:true})); } catch(_){}
        }
    """, el)

def set_sliderpoints(driver, choice_text: str, context_hint: str | None = None) -> bool:
    """Behaviorally/Decipher 'sq-sliderpoints' :
       mappe le libellé visible vers l'index, sélectionne le <select> frère
       (dispatch input/change/blur), sinon clique la piste jQuery-UI.
    """
    def _n(s):  # normaliseur doux
        s = (s or "").lower().replace("\u00a0", " ")
        s = re.sub(r"[»«“”\"'›→·•:]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    needle = _n(choice_text)
    # scope optionnel (question courante), sinon page entière
    try:
        scope = _find_context_container(driver, context_hint) if context_hint else None
    except Exception:
        scope = None
    root = scope if scope is not None else driver

    # blocs sliderpoints
    blocks = []
    try:
        blocks += root.find_elements(By.CSS_SELECTOR, ".sq-sliderpoints-element, .sq-sliderpoints-container")
    except Exception:
        pass
    if not blocks:
        return False

    for b in blocks:
        try:
            # 1) lire la légende visible
            legends = b.find_elements(By.CSS_SELECTOR, ".sliderpoints_legend .sliderpoints-legenditem")
            items = [_n(x.text) for x in legends if (x.text or "").strip()]
            idx = next((i for i, t in enumerate(items) if t and (needle == t or needle in t or t in needle)), -1)

            # 2) sélectionner via le <select> frère si possible
            sel = None
            S = _Sel(sel)
            # calcule un offset si placeholder en tête
            def _is_placeholder_opt(opt):
                t = _n(opt.text)
                v = (opt.get_attribute("value") or "").strip()
                return (v in ("", "-1")) or any(k in t for k in ("sélection", "selection", "select", "choose"))

            offset = 1 if S.options and _is_placeholder_opt(S.options[0]) else 0
            real_idx = min(len(S.options) - 1, idx + offset)

            # si les values sont numériques, mappe directement l’index
            vals = [ (o.get_attribute("value") or "").strip() for o in S.options ]
            try:
                if offset == 1 and all(v.isdigit() for v in vals[1:]):   # pattern Decipher/Behaviorally
                    S.select_by_value(str(idx))  
                else:
                    S.select_by_index(real_idx)
            except Exception:
                # dernier recours: clic direct sur l’option cible
                try: 
                    S.options[real_idx].click()
                except Exception: pass

            # événements attendus par la page
            driver.execute_script("""
              const s = arguments[0];
              try{s.dispatchEvent(new Event('input',{bubbles:true}));}catch(e){}
              try{s.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){}
              try{s.dispatchEvent(new Event('blur',  {bubbles:true}));}catch(e){}
            """, sel)

            # vérification rapide ; sinon fallback sur la piste jQuery-UI
            try:
                cur = _n(_Sel(sel).first_selected_option.text or "")
                if not cur or cur in ("sélectionnez", "selectionnez", "select", "choose"):
                    raise Exception("still placeholder")
            except Exception:
                # fallback piste (déjà présent dans ton code)
                # -> on clique .ui-slider-horizontal au pourcentage correspondant
                pass

            try:
                sel = b.find_element(By.TAG_NAME, "select")
            except Exception:
                sel = None

            if sel is not None and idx >= 0:
                S = _Sel(sel)
                try:
                    # valeur par index (ou value si présente)
                    val = None
                    try: 
                        val = S.options[idx].get_attribute("value")
                    except Exception: pass
                    if val is not None:
                        S.select_by_value(val)
                    else:
                        S.select_by_index(idx)
                except Exception:
                    # dernier recours : clic direct option
                    try: 
                        S.options[idx].click()
                    except Exception: pass

                # events attendus par la page
                driver.execute_script("""
                    const s = arguments[0];
                    try{s.dispatchEvent(new Event('input',{bubbles:true}));}catch(e){}
                    try{s.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){}
                    try{s.dispatchEvent(new Event('blur',  {bubbles:true}));}catch(e){}
                """, sel)
                print(f"✅ Sliderpoints sélectionné via <select> : {choice_text}. source: input_handler.py")
                return True

            # 3) fallback : cliquer la piste jQuery-UI au bon pourcentage
            if idx >= 0 and legends:
                try:
                    track = b.find_element(By.CSS_SELECTOR, ".ui-slider-horizontal")
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", track)
                    r = track.rect
                    steps = max(1, len(legends) - 1)   # 0..steps
                    x = int((idx / steps) * max(1, r["width"] - 4)) + 2
                    pause_here("Sliderpoints: clic sur la piste")
                    ActionChains(driver).move_to_element_with_offset(track, x, max(1, r["height"]//2)).click().perform()
                    print(f"✅ Sliderpoints cliqué sur la piste : {choice_text}. source: input_handler.py")
                    return True
                except Exception:
                    pass
        except Exception:
            continue

    return False

def norm(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    s = s.lower().strip()
    s = re.sub(r"[»«“”\"'›→·•:]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s

def click_cta_strong_any_context(driver, label_hint: str = "", allow_generic: bool = True) -> bool:
    """
    Clique un CTA 'Next/Suivant/Continuer/Start/Valider' même si c'est une image,
    un <a> JavaScript sans texte, ou un div role=button.
    label_hint est optionnel et ne bloque pas la détection générique.
    """
    BASE_HINTS = {
    "suivant",
    "page suivante",
    "continuer",
    "valider",
    "commencer",
    "start",
    "next",
    "continue",
    "submit",
    "proceed",
    "ok",
    "go",
    }

    lh = (label_hint or "").strip().lower()

    # NEW: si allow_generic=False, on N'UTILISE PAS les hints génériques.
    if allow_generic:
        HINTS = set(BASE_HINTS)
    else:
        HINTS = set()

    if lh:
        HINTS.add(lh)

    # Cas limite: si aucun hint à chercher (allow_generic=False et label_hint vide)
    if not HINTS:
        return False


    # 1) Boutons & liens avec texte
    text_xpath = " | ".join(
        [
            f"//button[normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ', 'abcdefghijklmnopqrstuvwxyzàâäçéèêëîïôöùûüÿ'))='{t}']"
            for t in HINTS
        ]
        + [
            f"//a[normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ', 'abcdefghijklmnopqrstuvwxyzàâäçéèêëîïôöùûüÿ'))='{t}']"
            for t in HINTS
        ]
    )
    candidates = []
    if text_xpath:
        try:
            candidates += driver.find_elements(By.XPATH, text_xpath)
        except Exception:
            pass

    # 2) Id / name / data-* / class “next/continue/suivant/btn_next…”
    css_patterns = [
        "[id*='next' i]",
        "[name*='next' i]",
        "[class*='next' i]",
        "[id*='suivant' i]",
        "[name*='suivant' i]",
        "[class*='suivant' i]",
        "[id*='continue' i]",
        "[name*='continue' i]",
        "[class*='continue' i]",
        "#btn_next, img#btn_next, a#btn_next, input#btn_next",
        "input[type='submit'], input[type='button']",
        "div[role='button']",
    ]
    for sel in css_patterns:
        try:
            candidates += driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            pass

    # 3) Images de bouton “next”
    img_sel = "img[src*='button_next' i], img[src*='btn_next' i], img[alt*='suivant' i], img[alt*='next' i]"
    try:
        candidates += driver.find_elements(By.CSS_SELECTOR, img_sel)
    except Exception:
        pass

    # 4) Liens JavaScript de type submit()
    try:
        candidates += driver.find_elements(
            By.XPATH,
            "//a[starts-with(@href,'javascript:')][contains(translate(@href,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'submit')]",
        )
    except Exception:
        pass

    # Dédupliquer en conservant l’ordre
    seen = set()
    uniq = []
    for el in candidates:
        try:
            key = el._id
        except Exception:
            key = id(el)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(el)

    # Essais de clic
    for el in uniq:
        try:
            if not el.is_displayed():
                continue
            if _safe_click(driver, el):
                # Attente courte d’une navigation ou d’un changement DOM
                try:
                    WebDriverWait(driver, 2).until(
                        lambda d: d.execute_script("return document.readyState")
                        == "complete"
                    )
                except TimeoutException:
                    pass
                return True
        except (StaleElementReferenceException, ElementClickInterceptedException):
            continue
        except Exception:
            continue
    return False

def _find_context_container(driver, context_hint: str | None):
    if not context_hint:
        print("❌ Contexte None")
        return None
    ctx = _norm_txt(context_hint)
    if not ctx:
        print("❌ Contexte vide")
        return None

    # 1) localiser un titre/entête de la question correspondant
    head_xp = (
        "//*[self::legend or self::h1 or self::h2 or self::h3 or self::h4 or self::h5 "
        "   or contains(@class,'question-text') or contains(@class,'QuestionText') "
        "   or contains(@class,'question__title') or contains(@class,'covered-if')]"
        "[normalize-space()!='']"
    )
    heads = []
    try:
        heads = driver.find_elements(By.XPATH, head_xp)
    except Exception:
        pass

    best, best_sc = None, -1.0
    for h in heads:
        try:
            t = _norm_txt(h.text or h.get_attribute("innerText") or "")
            # similarité souple
            sc = 1.0 if (ctx == t or ctx in t or t in ctx) else 0.0
            if sc == 0.0:
                # petit score si chevauchement de mots
                aw, bw = set(ctx.split()), set(t.split())
                if aw and bw:
                    sc = len(aw & bw) / len(aw | bw)
            if sc > best_sc:
                # conteneur "question" le plus proche de l'entête
                try:
                    q = h.find_element(
                        By.XPATH,
                        "ancestor::*[self::fieldset or contains(@class,'question') or contains(@class,'Question')][1]"
                    )
                except Exception:
                    q = h.find_element(By.XPATH, "ancestor::*[self::div or self::section][1]")
                # ne garder que si le conteneur a des inputs de réponse
                # ne garder que si le conteneur a des inputs de réponse (inclure texte/textarea)
                has_answers = q.find_elements(
                    By.XPATH,
                    ".//input[@type='radio' or @type='checkbox' or @type='text' or @type='search' or @type='number' or @type='textarea' or not(@type)]"
                    " | .//textarea"
                    " | .//*[@contenteditable='true']"
                    " | .//select"
                )
                if has_answers:
                    best, best_sc = q, sc

        except Exception:
            continue

    if best:
        return best

    # 2) fallback: bloc plus générique MAIS qui contient des inputs
    try:
        return driver.find_element(
            By.XPATH,
            "//*[self::fieldset or self::section or self::div or self::li]"
            "[.//input[@type='radio' or @type='checkbox'] or .//select]"
            f"[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), {_xpath_literal(ctx)})]"
        )
    except Exception:
        pass
    
        # 🔎 Bonus : conteneur Decipher <div id="question_*" class="question ...">
    try:
        q = driver.find_element(
            By.XPATH,
            "//*[starts-with(@id,'question_') and contains(@class,'question')]"
            f"[.//legend|.//h1|.//h2|.//h3|.//*[contains(@class,'question-text')]]"
            f"[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), {_xpath_literal(ctx)})]"
        )
        if q.find_elements(By.XPATH, ".//input[@type='radio' or @type='checkbox'] | .//select"):
            return q
    except Exception:
        return None

def _type_via_cdp(driver, text: str):
    """
    Frappe clavier via Chrome DevTools Protocol (Input.dispatchKeyEvent).
    Nécessite Chrome/Chromium + Selenium 4+.
    """
    if not hasattr(driver, "execute_cdp_cmd"):
        raise RuntimeError("CDP indisponible sur ce driver")

    for ch in text:
        try:
            driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "text": ch,
                "unmodifiedText": ch
            })
            driver.execute_cdp_cmd("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "text": ch,
                "unmodifiedText": ch
            })
            time.sleep(0.05)  # micro-pause humaine
        except Exception:
            # On continue même si un caractère échoue
            pass

def _has_native_selects(driver) -> bool:
    return bool(driver.find_elements(By.TAG_NAME, "select"))

def _open_first_dropdown(driver) -> bool:
    """
    Ouvre un dropdown visible (natif <select> ou custom rôle=combobox / bouton).
    Ne sélectionne pas d'option ici ; juste « abaisser » le menu.
    """
    # 1) <select> natif : on clique pour l’ouvrir (souvent inutile mais safe)
    selects = driver.find_elements(By.TAG_NAME, "select")
    for s in selects:
        try:
            if s.is_displayed():
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", s
                )
                time.sleep(0.1)
                s.click()
                print("📂 Dropdown (natif) ouvert... source: input_handler.py")
                return True
        except Exception:
            continue

    # 2) Dropdowns customs : role=combobox ou éléments avec “select” dans la classe
    togglers = []
    togglers += driver.find_elements(By.CSS_SELECTOR, "[role='combobox']")
    togglers += driver.find_elements(By.CSS_SELECTOR, "[aria-haspopup='listbox']")
    togglers += driver.find_elements(
        By.XPATH,
        "//*[contains(@class,'select') and (self::div or self::button or self::span)]",
    )
    for t in togglers:
        try:
            if (
                t.is_displayed()
                and t.rect.get("width", 0) > 20
                and t.rect.get("height", 0) > 15
            ):
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", t
                )
                time.sleep(0.1)
                try:
                    t.click()
                except Exception:
                    ActionChains(driver).move_to_element(t).click().perform()
                time.sleep(0.2)
                print("📂 Dropdown (custom) ouvert. source: input_handler.py")
                return True
        except Exception:
            continue

    print("❌ Aucun dropdown à ouvrir. source: input_handler.py")
    return False

def _try_select_option_any(driver, option_text: str) -> bool:
    """
    Tente de sélectionner 'option_text' si un <select> est présent
    ou si un menu custom est ouvert (ul/li, role=option...).
    """

    target = _norm_txt(option_text)

    # A) <select> natif
    selects = driver.find_elements(By.TAG_NAME, "select")
    for s in selects:
        try:
            sel = Select(s)
            # match texte visible
            for opt in sel.options:
                ot = _norm_txt(opt.text)
                if target and (target == ot or target in ot):
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", s
                    )
                    time.sleep(0.1)
                    try:
                        sel.select_by_visible_text(opt.text)
                    except Exception:
                        # fallback: value ou clic direct
                        if opt.get_attribute("value"):
                            sel.select_by_value(opt.get_attribute("value"))
                        else:
                            opt.click()

                    driver.execute_script("""
                      const s = arguments[0];
                      try { s.dispatchEvent(new Event('input', {bubbles:true})); } catch(e){}
                      try { s.dispatchEvent(new Event('change',{bubbles:true})); } catch(e){}
                      try { s.dispatchEvent(new Event('blur',  {bubbles:true})); } catch(e){}
                    """, s)
                    print(
                        f"✅ Option sélectionnée (natif) : {opt.text}. source: input_handler.py"
                    )
                    try:
                        driver._ui_overlay_opened = None
                    except:
                        pass
                    return True
            # match value
            for opt in sel.options:
                ov = _norm_txt(opt.get_attribute("value") or "")
                if target and target == ov:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", s
                    )
                    time.sleep(0.1)
                    sel.select_by_value(opt.get_attribute("value"))
                    print(
                        f"✅ Option sélectionnée (natif/value) : {opt.get_attribute('value')}. source: input_handler.py"
                    )
                    try:
                        # On signale à l'orchestrateur que l'action a réussi
                        setattr(
                            driver, "last_action_success", True
                        )  # si tu stockes sur driver
                    except:
                        pass
                    return True
        except Exception:
            continue

    # B) Dropdown custom : suppose menu déjà ouvert
    candidates = []
    candidates += driver.find_elements(By.XPATH, "//li[normalize-space(.)!='']")
    candidates += driver.find_elements(By.CSS_SELECTOR, "[role='option']")
    candidates += driver.find_elements(
        By.XPATH, "//*[contains(@class,'option') and normalize-space(text())!='']"
    )
    for c in candidates:
        try:
            txt = _norm_txt(c.get_attribute("innerText") or c.text)
            if not txt:
                continue
            if target and (target == txt or target in txt):
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", c
                )
                time.sleep(0.1)
                c.click()
                time.sleep(0.2)
                print(
                    f"✅ Option sélectionnée (custom) : {option_text}. source: input_handler.py"
                )
                try:
                    # On signale à l'orchestrateur que l'action a réussi
                    setattr(
                        driver, "last_action_success", True
                    )  # si tu stockes sur driver
                except:
                    pass
                return True
        except Exception:
            continue

    print(
        f"❌ Option '{option_text}' introuvable dans dropdown. source: input_handler.py"
    )
    return False

def _norm_hint(s: str) -> str:
    return _norm_txt(s)

def _select_like_elements(driver):
    els = []
    els += driver.find_elements(By.TAG_NAME, "select")
    els += driver.find_elements(
        By.CSS_SELECTOR, "[role='combobox'], [aria-haspopup='listbox']"
    )
    # togglers fréquents (custom selects)
    els += driver.find_elements(
        By.XPATH,
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

def _element_signature_text(driver, el) -> str:
    # concatène tout ce qui décrit ce champ (labels/aria/placeholder/contenu question)
    bits = []
    try:
        # label for=…
        eid = el.get_attribute("id")
        if eid:
            try:
                lbl = driver.find_element(By.XPATH, f"//label[@for='{eid}']")
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
                    n = driver.find_element(By.ID, ref)
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
                By.XPATH,
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
    # nettoyage léger
    return _norm_txt(sig)

def _viewport_penalty(driver, el) -> float:
    try:
        r = el.rect
        y = r.get("y", 0)
        htot = (
            driver.execute_script(
                "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, window.innerHeight);"
            )
            or 2000
        )
        # pénalise header/footer (sélecteur de langue, navbar…)
        if y < 120:
            return -0.75
        if y > htot - 220:
            return -0.5
    except Exception:
        pass
    return 0.0

def _similarity(a: str, b: str) -> float:
    # simple: sous-chaîne / chevauchement de mots
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    aw = set(a.split())
    bw = set(b.split())
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / len(aw | bw)

def _best_dropdown_for_hint(driver, hint: str | None, context_hint: str | None = None):
    cands = _select_like_elements(driver)
    if not cands:
        return None
    if not hint:
        # garder comportement par défaut si pas de hint
        return cands[0]
    h = _norm_hint(hint)
    c = _norm_hint(context_hint) if context_hint else ""
    best, best_score = None, -1e9
    for el in cands:
        try:
            sig = _element_signature_text(driver, el)
            sim = _similarity(h, sig)
            # petit boost si le hint ressemble à des champs typiques (année/mois/pays…)
            if any(
                k in h
                for k in (
                    "an",
                    "année",
                    "year",
                    "mois",
                    "month",
                    "pays",
                    "country",
                    "ville",
                    "city",
                    "state",
                    "province",
                )
            ):
                if any(
                    k in sig
                    for k in (
                        "an",
                        "année",
                        "year",
                        "mois",
                        "month",
                        "pays",
                        "country",
                        "ville",
                        "city",
                        "state",
                        "province",
                    )
                ):
                    if c:
                        sim += 0.35 * _similarity(c, sig)
            score = sim + _viewport_penalty(driver, el)
            if score > best_score:
                best, best_score = el, score
        except Exception:
            continue
    return best

def open_dropdown_generic(driver, hint: str | None = None, context_hint: str | None = None) -> bool:
    el = _best_dropdown_for_hint(driver, hint, context_hint=context_hint)
    if not el:
        print("❌ Aucun dropdown à ouvrir. source: input_handler.py")
        return False
    
    # [PATCH] Ouvrir réellement les <select> natifs pour rendre le menu visible
    if el.tag_name.lower() == "select":
        try:
            try:
                already_filled = is_dropdown_filled(driver, el)
            except Exception:
                already_filled = False

            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.click()  # ouverture native
            except Exception:
                ActionChains(driver).move_to_element(el).click().perform()
            # petit nudge pour éviter un blur immédiat selon les thèmes
            try:
                el.send_keys(Keys.ARROW_DOWN)
            except Exception:
                pass
            # marquer un overlay ouvert (utilisé dans la boucle solve_full_survey)
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
            print("📂 Dropdown (natif) ouvert. source: input_handler.py")
            return True
        except Exception:
            print(
                "ℹ️ Select natif ciblé: ouverture impossible → on continuera par sélection directe. source: input_handler.py"
            )
            return True

    try:
        # Évaluer l'état AVANT ouverture
        try:
            already_filled = is_dropdown_filled(driver, el)
        except Exception:
            already_filled = False

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        # clic + petite “pression” clavier pour empêcher un blur immédiat
        try:
            el.click()
        except Exception:
            ActionChains(driver).move_to_element(el).click().perform()
        time.sleep(0.05)
        try:
            el.send_keys(Keys.ARROW_DOWN)
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
        print("📂 Dropdown (custom) ouvert. source: input_handler.py")
        return True
    except Exception:
        print("❌ Échec à l’ouverture du dropdown ciblé. source: input_handler.py")
        return False

def _dropdown_visible_value(driver, ctrl) -> str:
    """
    Tente de lire le texte AFFICHÉ par le composant du dropdown (pas l'input caché).
    Retourne "" si rien de fiable trouvé.
    """
    # 1) <select> natif
    try:
        if ctrl.tag_name.lower() == "select":
            try:
                sel = Select(ctrl)
                if sel.first_selected_option:
                    return sel.first_selected_option.text or ""
            except Exception:
                val = ctrl.get_attribute("value") or ""
                # si value non vide, tenter de lire l'option correspondante
                if val:
                    try:
                        opt = ctrl.find_element(By.XPATH, f".//option[@value={repr(val)}]")
                        return opt.text or val
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
            el = ctrl.find_element(By.XPATH, xp)
            txt = (el.text or el.get_attribute("innerText") or "").strip()
            if txt:
                return txt
        except Exception:
            pass

    # 3) MUI (Material-UI / MUI)
    for xp in [
        ".//*[contains(@class,'MuiSelect-select') and not(contains(@class,'MuiSelect-nativeInput'))]",
        ".//*[contains(@class,'MuiSelect-select') and contains(@class,'MuiSelect-select') and @role='button']",
    ]:
        try:
            el = ctrl.find_element(By.XPATH, xp)
            txt = (el.text or el.get_attribute("innerText") or "").strip()
            if txt:
                return txt
        except Exception:
            pass

    # 4) Select2
    for xp in [
        ".//span[contains(@class,'select2-selection__rendered')]",
    ]:
        try:
            el = ctrl.find_element(By.XPATH, xp)
            txt = (el.get_attribute("title") or el.text or "").strip()
            if txt:
                return txt
        except Exception:
            pass

    # 5) jQuery UI / mobiles / ARIA button-like
    try:
        # bouton "fake select" le plus proche
        btn = ctrl
        if btn.get_attribute("role") != "button":
            btn = ctrl.find_element(By.XPATH, ".//*[@role='button' or @aria-haspopup='listbox']")
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
    Gère <select> natif et la plupart des rendus UI (Material, MUI, Select2, jQuery UI...).
    """
    # cas natif <select>
    try:
        if ctrl.tag_name.lower() == "select":
            val = ctrl.get_attribute("value") or ""
            if val and _norm_txt(val) not in _DROPDOWN_PLACEHOLDERS:
                return True
            # teste l'option sélectionnée
            try:
                sel = Select(ctrl)
                txt = _norm_txt(sel.first_selected_option.text or "")
                return bool(txt and txt not in _DROPDOWN_PLACEHOLDERS)
            except Exception:
                return False
    except Exception:
        pass

    # texte rendu par le composant
    txt = _norm_txt(_dropdown_visible_value(driver, ctrl))
    if not txt:
        return False

    # heuristique: placeholders les + fréquents
    if txt in _DROPDOWN_PLACEHOLDERS:
        return False

    # phrases usuelles
    for bad in ("veuillez", "please", "select", "sélectionner", "selectionner", "choose"):
        if txt.startswith(bad):
            return False

    # sinon, on considère que c'est renseigné
    return True

def try_select_option_any(driver, option_text: str, field_hint: str | None = None, context_hint: str | None = None) -> bool:
    """
    Sélectionne option_text dans le dropdown le plus pertinent en un seul enchaînement.
    - <select> natif: sélection directe (pas d’ouverture).
    - dropdown custom: ouvre puis sélectionne tout de suite.
    - 2 tentatives max si le menu se referme.
    """
    target = _norm_txt(option_text)

    # --- NATIF <select>: sélection directe (sans ouvrir)
    selects = driver.find_elements(By.TAG_NAME, "select")
    if selects:
        s = _best_dropdown_for_hint(driver, field_hint or option_text, context_hint=context_hint)
        try_selects = []
        if s is not None:
            try_selects.append(s)
        try_selects += [el for el in selects if (s is None or getattr(el, "_id", id(el)) != getattr(s, "_id", id(s)))]
        for sel_el in try_selects:
            try:
                S = _Sel(sel_el)
                # texte visible
                for opt in S.options:
                    ot = _norm_txt(opt.text)
                    ov = _norm_txt(opt.get_attribute("value") or "")
                    if target and (target == ot or target in ot or target == ov):
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", sel_el
                        )
                        try:
                            S.select_by_visible_text(opt.text)
                        except Exception:
                            if opt.get_attribute("value"):
                                S.select_by_value(opt.get_attribute("value"))
                            else:
                                opt.click()
                        print(
                            f"✅ Option sélectionnée (natif) : {opt.text}. source: input_handler.py"
                        )
                        try:
                            driver._ui_overlay_opened = None
                        except Exception:
                            pass
                        return True
            except Exception:
                continue

    # --- CUSTOM: ouvrir puis sélectionner rapidement (avec retries)
    for attempt in range(2):
        opened = open_dropdown_generic(driver, hint=field_hint or option_text, context_hint=context_hint)
        # Si aucune option à appliquer (option_text vide) et que le champ semblait déjà rempli, on skip
        if not option_text:
            try:
                ov = getattr(driver, "_ui_overlay_opened", None)
                if ov and ov.get("type") == "dropdown" and ov.get("filled") is True:
                    print("✅ Dropdown déjà rempli, on ne modifie pas. source: input_handler.py")
                    # on peut refermer proprement si besoin
                    try:
                        driver._ui_overlay_opened = None
                    except Exception:
                        pass
                    return True
            except Exception:
                pass

        # Rechercher des options visibles tout de suite, attente courte
        deadline = time.time() + 1.0  # ≤ 1s
        while time.time() < deadline:
            candidates = []
            candidates += driver.find_elements(By.CSS_SELECTOR, "[role='option']")
            candidates += driver.find_elements(By.XPATH, "//li[normalize-space(.)!='']")
            candidates += driver.find_elements(
                By.XPATH,
                "//*[contains(@class,'option') and normalize-space(text())!='']",
            )

            found = False
            for c in candidates:
                try:
                    if not c.is_displayed():
                        continue
                    txt = _norm_txt(c.get_attribute("innerText") or c.text)
                    if target and (target == txt or target in txt):
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", c
                        )
                        c.click()
                        print(
                            f"✅ Option sélectionnée (custom) : {option_text}. source: input_handler.py"
                        )
                        try:
                            driver._ui_overlay_opened = None
                        except Exception:
                            pass
                        return True
                except Exception:
                    continue
            time.sleep(0.05)

        # Si on arrive ici, c’est sans doute que le menu s’est refermé -> retry
        print(
            "↻ Menu refermé / option non visible, nouvelle tentative… source: input_handler.py"
        )

    print(
        f"❌ Option '{option_text}' introuvable dans dropdown. source: input_handler.py"
    )
    return False

def _norm_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("’", "'").replace("`", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s, flags=re.S).strip().lower()
    return s

def _scroll_into_view(driver, el):
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center', inline:'center'})", el
    )

def _js_click(driver, el):
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center',inline:'center'});", el
    )
    driver.execute_script("arguments[0].click();", el)

def _safe_click(driver, el, *, trace: str = "") -> bool:
    """
    Clic robuste (UN SEUL "plan" de clic) :
      1) JS click
      2) ActionChains click
      3) el.click()
    IMPORTANT:
      - Les callers NE doivent PAS refaire _js_click() en fallback,
        sinon on duplique inutilement la même stratégie.
    Observabilité:
      - stocke la dernière méthode gagnante sur driver._last_click_method
      - incrémente des compteurs sur driver._click_stats (utile en prod 100 bots)
    """
    def _bump(method: str):
        try:
            setattr(driver, "_last_click_method", method)
            stats = getattr(driver, "_click_stats", None)
            if stats is None:
                stats = {}
                setattr(driver, "_click_stats", stats)
            key = f"{trace}:{method}" if trace else method
            stats[key] = stats.get(key, 0) + 1
        except Exception:
            pass

    try:
        el.location_once_scrolled_into_view  # force scroll
    except Exception:
        pass

    # 1) JS click
    try:
        _js_click(driver, el)
        _bump("js")
        return True
    except Exception:
        pass

    # 2) ActionChains
    try:
        ActionChains(driver).move_to_element(el).pause(0.05).click().perform()
        _bump("actionchains")
        return True
    except Exception:
        pass

    # 3) Click natif
    try:
        el.click()
        _bump("native")
        return True
    except Exception:
        return False

def _is_checked(el):
    # works for both <input type=checkbox> and role=checkbox
    t = (el.get_attribute("type") or "").lower()
    if t in ("checkbox", "radio"):  # ← ajoute radio (certains thèmes l’utilisent)
        try:
            return el.is_selected()
        except Exception:
            pass
    aria = (el.get_attribute("aria-checked") or "").lower()
    if aria in ("true", "false"):
        return aria == "true"
    # some libraries mirror state in class names
    cls = (el.get_attribute("class") or "").lower()
    return "checked" in cls or "is-checked" in cls

def _looks_like_nav_label(s: str) -> bool:
    if not s:
        return False
    s = s.lower().strip()
    nav_kw = {
        "continuer",
        "suivant",
        "start",
        "commencer",
        "démarrer",
        "accepter",
        "accepter et commencer",
        "next",
        "continue",
        "submit",
        "soumettre",
        "valider",
    }
    return any(k in s for k in nav_kw)

# ─────────────────────────────────────────────────────────────
# MATRICES (tableaux Qualtrics/Dynata/SSI…) : détection + actions
# ─────────────────────────────────────────────────────────────

MATRIX_COL_SYNONYMS = {
    # FR
    "oui": "oui",
    "non": "non",
    "d’accord": "daccord",
    "pas d’accord": "pas daccord",
    "plutôt d’accord": "plutot daccord",
    "tout à fait d’accord": "tout a fait daccord",
    "tout a fait d’accord": "tout a fait daccord",
    "tout à fait daccord": "tout a fait daccord",
    "jamais": "jamais",
    "rarement": "rarement",
    "parfois": "parfois",
    "souvent": "souvent",
    "toujours": "toujours",
    "très satisfait": "tres satisfait",
    "satisfait": "satisfait",
    "neutre": "neutre",
    "insatisfait": "insatisfait",
    "très insatisfait": "tres insatisfait",
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    # EN courants
    "yes": "yes",
    "no": "no",
    "strongly agree": "strongly agree",
    "agree": "agree",
    "neutral": "neutral",
    "disagree": "disagree",
    "strongly disagree": "strongly disagree",
    "never": "never",
    "rarely": "rarely",
    "sometimes": "sometimes",
    "often": "often",
    "always": "always",
    "very satisfied": "very satisfied",
    "satisfied": "satisfied",
    "unsatisfied": "unsatisfied",
    "very unsatisfied": "very unsatisfied",
}

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", s)

def split_typed_instruction(s: str):
    """
    Retourne (label, type or None) depuis 'libellé //// type'.
    Le séparateur est 4+ slashes.
    """
    if not s:
        return "", None
    parts = re.split(r"/{4,}", s)
    lbl = _norm(parts[0])
    if len(parts) == 1:
        return lbl, None
    itype = _norm(parts[1]).lower()
    return lbl, itype

def _looks_like_matrix(driver):
    """
    Heuristique simple : présence d'un tableau <table> avec input[radio|checkbox] par cellule
    ou de lignes .q-matrix/.Matrix (Qualtrics).
    """
    # Tables HTML
    tables = driver.find_elements(
        By.XPATH, "//table[.//input[@type='radio' or @type='checkbox'] or .//select]"
    )
    if tables:
        return True
    # Matrices Qualtrics/dynata styles (div grids)
    grids = driver.find_elements(
        By.CSS_SELECTOR, ".q-matrix, .Matrix, .grid, .question-matrix, .matrix"
    )
    for g in grids:
        try:
            if g.find_elements(
                By.CSS_SELECTOR,
                "input[type='radio'], input[type='checkbox'], select, [role='radio'], [role='checkbox']",
            ):
                return True
        except:
            continue
    return False

def _iter_matrix_rows(driver):
    """
    Retourne une liste de tuples (row_element, row_label_text).
    Compatible <tr> et structures <div>.
    """
    rows = []
    # try <tr>
    for tr in driver.find_elements(By.XPATH, "//table//tr"):
        try:
            # label à gauche, souvent dans th/td[1]
            lbl_el = None
            for css in ["th", "td[1]", "td[1]//label", "td[1]//div"]:
                try:
                    lbl_el = tr.find_element(By.XPATH, f".//{css}")
                    if lbl_el.text.strip():
                        break
                except:
                    continue
            lbl = (lbl_el.text if lbl_el else "").strip()
            # ignorer header row
            if tr.find_elements(
                By.XPATH, ".//input[@type='radio' or @type='checkbox'] | .//select"
            ):
                rows.append((tr, lbl))
        except:
            continue

    if rows:
        return rows

    # fallback div‑based
    grids = driver.find_elements(
        By.CSS_SELECTOR, ".q-matrix, .Matrix, .grid, .question-matrix, .matrix"
    )
    for g in grids:
        try:
            candidates = g.find_elements(
                By.XPATH,
                ".//*[self::div or self::li][.//input[@type='radio' or @type='checkbox'] or .//select]",
            )
            for row in candidates:
                lbl = ""
                try:
                    lbl = row.find_element(
                        By.XPATH,
                        ".//label | .//*[self::div or self::span][normalize-space(.)!='']",
                    ).text.strip()
                except:
                    pass
                rows.append((row, lbl))
        except:
            continue
    return rows

def _get_matrix_columns(driver):
    """
    Retourne la liste des libellés d'en‑tête de colonnes (normalisés) pour matching.
    """
    headers = []
    # head <th>
    for th in driver.find_elements(By.XPATH, "//table//th[normalize-space(.)!='']"):
        try:
            headers.append(_norm(th.text))
        except:
            continue
    if headers:
        return headers
    # fallback: première ligne header simulée
    try:
        first_row = driver.find_element(By.XPATH, "(//table//tr)[1]")
        for td in first_row.find_elements(By.XPATH, ".//td[normalize-space(.)!='']"):
            headers.append(_norm(td.text))
    except:
        pass
    # div‑based header
    for h in driver.find_elements(
        By.CSS_SELECTOR, ".matrix thead, .q-matrix thead, .Matrix thead"
    ):
        for th in h.find_elements(By.XPATH, ".//*[normalize-space(.)!='']"):
            headers.append(_norm(th.text))
    return headers

def click_matrix_cell_by_row_and_col(driver, row_label: str, col_label: str) -> bool:
    """
    Cible une cellule de matrice en croisant la ligne (row_label) et la colonne (col_label).
    Compatible <table> et grilles div-based.
    """

    print("lancement de click_matrix_cell_by_row_and_col")
    if not _looks_like_matrix(driver):
        return False
    rneedle = _norm(row_label)
    cneedle = _norm(col_label)

    # 1) Tenter les <table> classiques
    try:
        # a) récupérer index de colonne
        headers = _get_matrix_columns(driver)  # déjà normalisés
        col_idx = None
        for i, h in enumerate(headers):
            if cneedle == h or cneedle in h or h in cneedle:
                col_idx = i
                break
        # b) trouver la ligne par son label (robuste max-diff : [checkbox] | [label] | [checkbox])
        for tr in driver.find_elements(By.XPATH, "//table//tr"):
            try:
                lbl = ""
                # --- NEW: détecter une cellule texte SANS input dans les 2-3 premières colonnes
                tds = tr.find_elements(By.XPATH, "./td")
                if tds:
                    # on parcourt les premières cellules pour trouver du texte sans input
                    for td in tds[:3]:  # couvre la majorité des tableaux max-diff
                        try:
                            has_input = bool(td.find_elements(
                                By.XPATH,
                                ".//input[@type='radio' or @type='checkbox'] | .//*[@role='radio' or @role='checkbox']"
                            ))
                            raw = (td.text or td.get_attribute("innerText") or "").strip()
                            if raw and not has_input:
                                lbl = _norm(raw)
                                break
                        except Exception:
                            continue
                # --- Fallback: heuristiques initiales (+ td[2])
                if not lbl:
                    for xp in ["./th", "./td[1]", "./td[2]", "./td[1]//label", "./td[2]//label", "./td[1]//div", "./td[2]//div"]:
                        try:
                            t = tr.find_element(By.XPATH, xp).text.strip()
                            if t:
                                lbl = _norm(t)
                                break
                        except Exception:
                            continue
                # si on n’a toujours rien, passer à la ligne suivante
                if not lbl:
                    continue
                # match avec l’aiguille (row_label)
                if not (rneedle == lbl or rneedle in lbl or lbl in rneedle):
                    continue

                # pointer la cellule d'intersection (robuste même sans <th>)
                tds = tr.find_elements(By.XPATH, "./td")

                def _is_most(s: str) -> bool:
                    s = _norm(s).lower()
                    return any(k in s for k in ["plus", "most", "best", "max", "right", "droite"])

                def _is_least(s: str) -> bool:
                    s = _norm(s).lower()
                    return any(k in s for k in ["moins", "least", "worst", "min", "left", "gauche"])

                want_most = _is_most(col_label)
                want_least = _is_least(col_label)

                cand_cells = []
                if col_idx is not None and len(tds) > 0:
                    # mapping souple : tente l'index calculé + voisin droit
                    if len(tds) > col_idx:
                        cand_cells.append(tds[col_idx])
                    if len(tds) > col_idx + 1:
                        cand_cells.append(tds[col_idx + 1])
                # Fallback : toutes les cellules si pas d'en-têtes
                if not cand_cells:
                    cand_cells = tds

                # Scoring des cellules en fonction du type attendu (most/least)
                best_cell, best_score = None, -1e9
                for idx, cell in enumerate(cand_cells):
                    try:
                        # y a-t-il un input radio/checkbox dans la cellule ?
                        has_input = False
                        inp = None
                        for xp in [".//input[@type='radio']", ".//input[@type='checkbox']", ".//*[@role='radio']", ".//*[@role='checkbox']"]:
                            try:
                                inp = cell.find_element(By.XPATH, xp)
                                has_input = True
                                break
                            except Exception:
                                continue
                        if not has_input:
                            continue
                        
                        # score par indice (gauche/droite)
                        sc = 0.0
                        # bonus si la cellule ressemble à "most" / "least" via classes/attrs
                        sig = " ".join([
                            _norm(cell.get_attribute("class") or ""),
                            _norm(inp.get_attribute("class") or ""),
                            _norm(inp.get_attribute("name") or ""),
                            _norm(inp.get_attribute("aria-label") or ""),
                        ]).lower()
                        if want_most:
                            if any(k in sig for k in ["most","best","max"]): sc += 3.0
                            sc += idx * 0.5  # plus la cellule est à droite, mieux c’est
                            print(f"score most {sc}")
                        if want_least:
                            if any(k in sig for k in ["least","worst","min"]): sc += 3.0
                            sc += (len(cand_cells) - idx - 1) * 0.5  # plus à gauche, mieux c’est
                            print(f"score least {sc}")

                        # si aucun des deux explicitement demandé, favorise l’input le plus à droite (souvent "most")
                        if not want_most and not want_least:
                            sc += idx * 0.25

                        if sc > best_score:
                            best_cell, best_score = cell, sc
                    except Exception:
                        continue
                    
                # Cliquer la meilleure cellule trouvée
                if best_cell is not None:
                    try:
                        tgt = None
                        for xp in [".//input[@type='radio']", ".//input[@type='checkbox']", ".//*[@role='radio']", ".//*[@role='checkbox']"]:
                            try:
                                tgt = best_cell.find_element(By.XPATH, xp)
                                break
                            except Exception:
                                continue
                        if tgt is not None:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", tgt)
                            time.sleep(0.05)
                            try:
                                tgt.click()
                            except Exception:
                                ActionChains(driver).move_to_element(tgt).click().perform()
                            try:
                                setattr(driver, "last_action_success", True)
                                setattr(driver, "_post_action_t0", time.time())
                            except Exception:
                                pass
                            side = "most/plus" if want_most else ("least/moins" if want_least else "unknown")
                            print(f"✅ Matrice: '{row_label}' × '{col_label}' (table, {side}). source: input_handler.py")
                            #_click_next_any(driver)
                            return True
                    except Exception:
                        pass
                    
            except Exception:
                continue
    except Exception:
        pass
    # 2) Grilles "div-based" (Qualtrics/Dynata/SSI…)
    try:
        # repérer bloc de matrice
        grids = driver.find_elements(By.CSS_SELECTOR, ".q-matrix, .Matrix, .grid, .question-matrix, .matrix")
        for g in grids:
            try:
                # i) trouver la ligne correspondant au row_label
                rows = g.find_elements(By.XPATH, ".//*[self::div or self::li][.//input[@type='radio' or @type='checkbox'] or .//select]")
                row = None
                for r in rows:
                    txt = (_norm(r.text) if r.text else "").strip()
                    if txt and (rneedle == txt or rneedle in txt or txt in rneedle):
                        row = r
                        break
                if row is None:
                    continue

                # ii) dans la ligne, trouver la “colonne” correspondant au col_label
                #     (souvent ce sont des items frères contenant un input)
                cells = row.find_elements(By.XPATH, ".//div|.//li|.//span|.//td")
                best = None
                best_score = -1
                for cell in cells:
                    try:
                        sig = _norm(cell.text or cell.get_attribute("innerText") or "")
                        sc = 1.0 if (cneedle and (cneedle == sig or cneedle in sig or sig in cneedle)) else 0.0
                        if sc > best_score and cell.find_elements(By.XPATH, ".//input[@type='radio' or @type='checkbox'] | .//*[@role='radio' or @role='checkbox']"):
                            best = cell
                            best_score = sc
                    except Exception:
                        continue
                if best is None:
                    continue

                # iii) cliquer l'input dans la cellule choisie
                try:
                    tgt = None
                    for xp in [".//input[@type='radio']", ".//input[@type='checkbox']", ".//*[@role='radio']", ".//*[@role='checkbox']"]:
                        try:
                            tgt = best.find_element(By.XPATH, xp)
                            break
                        except Exception:
                            continue
                    if not tgt:
                        continue
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", tgt)
                    time.sleep(0.05)
                    try:
                        tgt.click()
                    except Exception:
                        ActionChains(driver).move_to_element(tgt).click().perform()
                    try:
                        setattr(driver, "last_action_success", True)
                        setattr(driver, "_post_action_t0", time.time())
                    except Exception:
                        pass
                    print(f"✅ Matrice: '{row_label}' × '{col_label}' (grid). source: input_handler.py")
                    #_click_next_any(driver)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
    except Exception:
        pass
    print("rien n'a fonctionné")
    return False

def _select_cell_action(cell, preferred_col_norm):
    """
    Dans une cellule (row x col), déclenche l'action selon le type :
    - radio : coche si pas déjà sélectionné
    - checkbox : coche si non cochée (idempotent)
    - select : ouvre et choisit l'option correspondant à la colonne
    """
    # radio
    try:
        r = cell.find_element(By.CSS_SELECTOR, "input[type='radio'], [role='radio']")
        if hasattr(r, "is_selected") and r.is_selected():
            return True
        try:
            r.click()
            return True
        except:
            try:
                ActionChains(cell.parent).move_to_element(r).click().perform()
                return True
            except:
                try:
                    cell.parent.execute_script("arguments[0].click();", r)
                    return True
                except:
                    pass
    except:
        pass

    # checkbox
    try:
        cb = cell.find_element(
            By.CSS_SELECTOR, "input[type='checkbox'], [role='checkbox']"
        )
        try:
            checked = False
            try:
                checked = cb.is_selected()
            except:
                aria = (cb.get_attribute("aria-checked") or "").lower()
                checked = aria == "true" or aria == "mixed"
            if checked:
                return True
            cb.click()
            return True
        except:
            try:
                ActionChains(cell.parent).move_to_element(cb).click().perform()
                return True
            except:
                try:
                    cell.parent.execute_script("arguments[0].click();", cb)
                    return True
                except:
                    pass
    except:
        pass

    # select
    try:
        sel = cell.find_element(By.TAG_NAME, "select")
        S = Select(sel)
        # essai par texte proche du nom de colonne
        for opt in S.options:
            if _norm(opt.text) == preferred_col_norm or preferred_col_norm in _norm(
                opt.text
            ):
                S.select_by_visible_text(opt.text)
                return True
        # essai par value
        for opt in S.options:
            if _norm(opt.get_attribute("value") or "") == preferred_col_norm:
                S.select_by_value(opt.get_attribute("value"))
                return True
    except:
        pass

    return False

def apply_matrix_column_to_all_rows(driver, column_label: str) -> bool:
    """
    Si l'IA renvoie uniquement un EN‑TÊTE DE COLONNE (ex: 'Oui', 'Agree', '5'),
    alors on coche/sélectionne cette colonne pour TOUTES LES LIGNES NON RÉPONDUES.
    """
    if not _looks_like_matrix(driver):
        return False

    target = _norm(column_label)
    # map synonymes (oui -> oui, d’accord -> daccord…)
    target = MATRIX_COL_SYNONYMS.get(target, target)

    rows = _iter_matrix_rows(driver)
    if not rows:
        return False

    # déterminer index de colonne désirée (si table)
    headers = _get_matrix_columns(driver)
    col_idx = None
    if headers:
        for i, h in enumerate(headers):
            h_norm = MATRIX_COL_SYNONYMS.get(h, h)
            if target == h_norm or target in h_norm or h_norm in target:
                col_idx = i
                break

    success_any = False
    for row_el, _lbl in rows:
        try:
            # si vraie table : cellules TD alignées sur en‑têtes
            if col_idx is not None and row_el.tag_name.lower() == "tr":
                tds = row_el.find_elements(By.XPATH, ".//td")
                # heuristique: première td est le label; les colonnes réponses commencent ensuite
                # on essaye dans td[col_idx] et td[col_idx+1] pour couvrir décalages
                cell_candidates = []
                if len(tds) > col_idx:
                    cell_candidates.append(tds[col_idx])
                if len(tds) > col_idx + 1:
                    cell_candidates.append(tds[col_idx + 1])

                for cell in cell_candidates:
                    if _select_cell_action(cell, target):
                        success_any = True
                        break
                if success_any:
                    continue

            # fallback div‑based : chercher dans la ligne un input/select "proche" de la colonne par texte
            # 1) chercher un libellé descendant qui match la colonne
            try:
                label_cell = row_el.find_element(
                    By.XPATH,
                    ".//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{}')]".format(
                        target
                    ),
                )
                parent = label_cell.find_element(
                    By.XPATH, "ancestor::*[self::td or self::div or self::li][1]"
                )
                if _select_cell_action(parent, target):
                    success_any = True
                    continue
            except:
                pass

            # 2) sinon, cliquer le premier input/select de la ligne (par défaut sur 'Oui')
            try:
                first_cell = row_el.find_element(
                    By.XPATH,
                    ".//td[.//input[@type='radio' or @type='checkbox'] or .//select] | .//*[.//input[@type='radio' or @type='checkbox'] or .//select]",
                )
                if _select_cell_action(first_cell, target):
                    success_any = True
                    continue
            except:
                pass
        except:
            continue

    return success_any

def handle_generic_input(driver, gpt_answer: str):
    """
    Détecte dynamiquement le type d'input et applique l'action.
    - Si 'gpt_answer' est un placeholder de dropdown → on ouvre un dropdown au lieu d’écrire.
    - Si des <select> existent et que 'gpt_answer' ressemble à une option → on tente de la sélectionner.
    - Si 'gpt_answer' ressemble à un CTA → on laisse la logique bouton.
    """
    try:
        if _looks_like_nav_label(gpt_answer):
            return False  # géré côté CTA

        ans_norm = _norm_txt(gpt_answer)

        # 🔎 Cas MATRICE : si la réponse ressemble à un EN‑TÊTE DE COLONNE,
        # on applique cette colonne à toutes les lignes non répondues.
        try:
            if apply_matrix_column_to_all_rows(driver, gpt_answer):
                print(
                    f"🧮 Matrice détectée : colonne « {gpt_answer} » appliquée à toutes les lignes. source: input_handler.py"
                )
                return True
        except Exception as e:
            print("❌ Erreur matrix handler : source: input_handler.py", e)

        # 0) Gestion dropdowns en priorité quand placeholder
        if ans_norm in PLACEHOLDER_TOKENS:
            # Si un select existe → ouvrir le menu (au lieu d'écrire "Sélectionnez" dans un input)
            if _has_native_selects(driver) or driver.find_elements(
                By.CSS_SELECTOR, "[role='combobox'], [aria-haspopup='listbox']"
            ):
                return _open_first_dropdown(driver)
            # sinon, on ne fait rien (évite d'écrire n'importe où)
            print(
                "ℹ️ Placeholder reçu mais aucun dropdown détecté. source: input_handler.py"
            )
            return False

        # 0-bis) Si on a un select visible et une réponse non-CTA, tenter la sélection directe
        if _has_native_selects(driver):
            if _try_select_option_any(driver, gpt_answer):
                return True

        # 1. Radios
        radio_inputs = driver.find_elements(
            By.CSS_SELECTOR, "input[type='radio'], [role='radio']"
        )
        if radio_inputs:
            print("🔘 Options radio détectées. source: input_handler.py")
            return click_radio_by_label(driver, gpt_answer)

        # 2. Checkboxes
        checkboxes = driver.find_elements(
            By.CSS_SELECTOR, "input[type='checkbox'], [role='checkbox']"
        )
        if checkboxes:
            print("☑️ Checkboxs détectées. source: input_handler.py")
            return click_checkbox_by_label(driver, gpt_answer)

        # 3. Texte (⚠️ ignorer les placeholders)
        text_inputs = driver.find_elements(
            By.CSS_SELECTOR, "input[type='text'], textarea"
        )
        if text_inputs:
            if ans_norm in PLACEHOLDER_TOKENS:
                print(
                    "⛔ Placeholder ignoré pour le champ texte. source: input_handler.py"
                )
                return False
            print("⌨️ Champ texte détecté. source: input_handler.py")
            return fill_text_input(driver, gpt_answer)

        print("❓ Aucun input connu géré. source: input_handler.py")
        return False

    except Exception as e:
        print("💥 Erreur dans handle_generic_input : source: input_handler.py", e)
        return False

def _norm_soft(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s, flags=re.S)
    return s

def _norm_lc_soft(s: str) -> str:
    return _norm_soft(s).lower()

def _find_best_label_text(el):
    """
    Récupère un texte pertinent pour le label (prend le .text, sinon plus long des spans descendants).
    """
    txt = (el.text or el.get_attribute("innerText") or "").strip()
    if txt:
        return txt
    # parfois le texte est au fond d’un span
    try:
        spans = el.find_elements(By.XPATH, ".//span[normalize-space(string())!='']")
        if spans:
            spans = sorted(spans, key=lambda s: len((s.text or "").strip()), reverse=True)
            return (spans[0].text or "").strip()
    except Exception:
        pass
    return ""

def _looks_checked(el, linked_input):
    """
    Heuristique succès : input sélectionné OU classe aria/ui passée à 'on/checked'.
    """
    try:
        if linked_input and linked_input.is_selected():
            return True
    except Exception:
        pass
    try:
        cls = (el.get_attribute("class") or "").lower()
        aria = (el.get_attribute("aria-pressed") or el.get_attribute("aria-checked") or "").lower()
        if "ui-checkbox-on" in cls or aria in ("true", "mixed"):
            return True
    except Exception:
        pass
    return False

def _find_linked_input_for_label(driver, label_el):
    """
    Tente de retrouver l'input[type=checkbox] correspondant au label :
    - via l'attribut 'for'
    - sinon via un sibling/descendant
    """
    linked = None
    # 1) via for/id
    try:
        for_attr = label_el.get_attribute("for")
        if for_attr:
            linked = driver.find_element(By.ID, for_attr)
            t = (linked.get_attribute("type") or "").lower()
            if t != "checkbox":
                linked = None
    except Exception:
        linked = None

    # 2) fallback : descendant/suivant
    if linked is None:
        try:
            linked = label_el.find_element(By.XPATH, ".//input[@type='checkbox']")
        except Exception:
            try:
                linked = label_el.find_element(By.XPATH, "following::input[@type='checkbox'][1]")
            except Exception:
                linked = None
    return linked

# --- helper JS : force l'état checked via label[for] ---
def _force_label_for_checkbox_js(driver, label_text: str) -> bool:
    js = r"""
    const norm = s => (s||'').toLowerCase()
        .normalize('NFKC').replace(/\u00A0/g,' ')
        .replace(/[»«“”"'›→·•:]/g,'').replace(/\s+/g,' ').trim();
    const needle = norm(arguments[0]);

    // trouver un <label> compatible (texte ≈, role=button/ui-btn/checkbox…)
    const labs = Array.from(document.querySelectorAll('label'));
    for (const lab of labs) {
      const txt = norm(lab.innerText || lab.textContent || '');
      if (!txt) continue;
      if (!(txt.includes(needle) || needle.includes(txt))) continue;

      const fid = lab.getAttribute('for');
      if (!fid) continue;
      const inp = document.getElementById(fid);
      if (!inp) continue;

      // clic "naturel" sur label
      try { lab.click(); } catch(e){}

      // sécuriser l'état + events
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

def click_checkbox_buttonish_by_label(driver, label: str, context_hint: str | None = None) -> bool:
    """
    Coche une 'checkbox' rendue comme un bouton (label role='button' / classes ui-btn, ui-checkbox-*…).
    1) on cible le meilleur <label> (scope par contexte si fourni),
    2) click label (+ variantes),
    3) si pas d'effet, click JS sur l'input lié (for=...),
    4) si toujours rien : _force_label_for_checkbox_js().
    """

    def _norm(s: str) -> str:
        return " ".join((s or "").split()).strip().lower()

    # scope optionnel (même logique que _find_context_container)
    scope = None
    try:
        import input_handler
        scope = input_handler._find_context_container(driver, context_hint) if context_hint else None
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
            # match souple
            sc = 1.0 if (needle in txt or txt in needle) else 0.0
            if sc == 0.0:
                continue
            # petit boost si on est dans le bon conteneur de question
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
            # état ?
            if linked is not None:
                try:
                    if linked.is_selected():
                        return True
                except Exception:
                    pass
            # jQuery-Mobile : classe visuelle
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
            # set + events (sécurisation)
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

    # 3) Dernier recours : forcer via JS (label→input + classes ui-checkbox-on)
    if _force_label_for_checkbox_js(driver, label):
        return True

    return False

def _xpath_literal(s: str) -> str:
    # util pour insérer du texte en XPath sans se casser avec les quotes
    if "'" not in s: 
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    # cas rare, on concatène
    parts = s.split("'")
    return "concat(" + ", \"'\", ".join([f"'{p}'" for p in parts]) + ")"

def _click_radio_label_in_scope(driver, scope, label_text: str) -> bool:
    """Decipher/Confirmit : coche une radio via <label for=...> **dans le scope**."""

    def _n(s):
        if not s: return ""
        s = unicodedata.normalize("NFKC", s).replace("\u00A0"," ").lower()
        s = re.sub(r"[»«“”\"'›→·•:]+"," ", s)
        return re.sub(r"\s+"," ", s).strip()

    needle = _n(label_text)
    if not needle:
        return False

    # 1) labels descendants du scope
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

    # 2) l’input ciblé DOIT être **dans le scope**
    fid = best.get_attribute("for")
    if not fid:
        return False
    try:
        inp = scope.find_element(By.XPATH, f".//*[@id={repr(fid)} and @type='radio']")
    except Exception:
        return False

    # 3) click label + sécurisation de l’état (events)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
        try: best.click()
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

def _click_decipher_grid_radio_strict(driver, label: str, context_hint: str = "") -> bool:
    """
    Decipher (table.grid) strict :
    - scope par question (ctx),
    - trouve la <tr> dont le <th> contient le libellé,
    - clique le <label>, force checked + events sur l'<input>,
    - clique la cellule .clickableCell en secours.
    """

    def _n(s):
        if not s: return ""
        s = s.replace("\u00A0", " ")
        s = s.replace("’","'").replace("‘","'").replace("´","'").replace("`","'")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", s).strip().lower()

    needle = _n(label)
    if not needle:
        return False

    # scope = bloc de la question si trouvé, sinon page entière
    try:
        scope = _find_questions_container(driver, context_hint)  # déjà dans ton fichier
    except Exception:
        scope = None
    scope = scope or driver

    # toutes les lignes de la grille
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
            # accepte égalité, inclusion, ou label dans un <label> de la cellule
            if not (needle == thn or needle in thn or thn in needle):
                # certains thèmes n’ont pas de <th> “propre”; on regarde le texte du label visible
                try:
                    ltxt = tr.find_element(By.XPATH, ".//td//label").text
                    if not (needle in _n(ltxt) or _n(ltxt) in needle):
                        continue
                except Exception:
                    continue

            # 1) clique le <label> si présent
            lab = None
            try:
                lab = tr.find_element(By.XPATH, ".//td[contains(@class,'clickableCell')]//label")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", lab)
                try:
                    pause_here("click label 2606")
                    lab.click()
                    return True
                except Exception:
                    pause_here("AC click label 2610")
                    driver.execute_script("arguments[0].click();", lab)
                    return True
            except Exception:
                pass

            # 2) force l’état sur l’input + events
            try:
                inp = tr.find_element(By.XPATH, ".//input[@type='radio']")
                pause_here("force checked 2619")
                driver.execute_script("""
                    const i = arguments[0];
                    i.checked = true;
                    try { i.dispatchEvent(new Event('input',  {bubbles:true})); } catch(e) {}
                    try { i.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
                    try { i.dispatchEvent(new Event('blur',   {bubbles:true})); } catch(e) {}
                """, inp)
            except Exception:
                pass

            # 3) secours : clique la cellule cliquable (thème Decipher)
            try:
                td = tr.find_element(By.XPATH, ".//td[contains(@class,'clickableCell')]")
                pause_here("click clickableCell 2633")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", td)
                driver.execute_script("arguments[0].click();", td)
                return True
            except Exception:
                pass

            # 4) vérification
            try:
                chk = tr.find_element(By.XPATH, ".//input[@type='radio']")
                if chk.is_selected() or (chk.get_attribute("checked") or "").lower() in ("true", "checked"):
                    print(f"✅ Radio (Decipher strict) cochée : {label}")
                    return True
            except Exception:
                pass
        except Exception:
            continue

    return False

# --- Ajout utilitaire (placer juste au-dessus de click_radio_cardlike_js) ---
def _mat_card_selected(el) -> bool:
    """Vérifie l'état 'sélectionné' sur un card Angular (mat-card)."""
    try:
        # 1) icône de validation visible ?
        tick = el.find_elements(By.CSS_SELECTOR, ".selected-icon, .uil-check-circle")
        for t in tick:
            try:
                if t.is_displayed() and t.rect.get("width",0) > 4:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        cls = (el.get_attribute("class") or "").lower()
        if any(k in cls for k in ("selected","active","is-checked","checked")):
            return True
    except Exception:
        pass
    # voisinage : input radio généré dynamiquement ?
    try:
        if el.find_elements(By.XPATH, ".//input[@type='radio' and @checked]"):
            return True
    except Exception:
        pass
    return False

def click_radio_cardlike_js(driver, label: str, context_hint: str | None = None, max_retries: int = 2) -> bool:
    """
    Cible et clique une option "radio" rendue comme carte/bouton (JS) — Confirmit/Dynata/Decipher.
    - Scope par le contexte de question si disponible.
    - Une seule méthode de clic par essai ; on retourne True dès réussite.
    - Validation d’état via aria-checked / input:checked / classes visuelles.
    """
    def _lc(s):  # normalisation simple "lower & squeeze"
        return " ".join((s or "").lower().strip().split())

    want = _lc(label)
    if not want:
        return False

    # 1) Scope = conteneur de la question (sécurise la recherche)
    try:
        container = _find_question_container_by_ctx(driver, context_hint)
    except Exception:
        container = None
    scope = container if container is not None else driver

    # 2) Candidats : éléments contenant le texte + proches wrappers cliquables
    #    - ARIA role="radio" / "radiogroup"
    #    - classes fréquences "answer|option|choice|card|button|tile"
    #    - onclick présent
    UP = "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ"
    LO = "abcdefghijklmnopqrstuvwxyzàâäçéèêëîïôöùûüÿ"
    txt_match = f"contains(translate(normalize-space(.),'{UP}','{LO}'), '{want}')"

    X_CARDS = (
        ".//*[" + txt_match + "]"
        "/ancestor::*[ @role='radio' "
        " or @onclick "
        " or contains(@class,'answer') or contains(@class,'option') or contains(@class,'choice') "
        " or contains(@class,'card') or contains(@class,'button') or contains(@class,'tile') "
        " or self::mat-card or contains(@class,'mat-card') "
        "][1]"
    )

    X_SELF = (
        ".//*[" + txt_match + "]["
        " @role='radio' or @onclick or "
        " contains(@class,'answer') or contains(@class,'option') or contains(@class,'choice') or "
        " contains(@class,'card') or contains(@class,'button') or contains(@class,'tile') or "
        " self::mat-card or contains(@class,'mat-card') ]"
    )

    cand = []
    try:
        cand.extend(scope.find_elements(By.XPATH, X_CARDS))
    except Exception:
        pass
    try:
        cand.extend(scope.find_elements(By.XPATH, X_SELF))
    except Exception:
        pass

    # Filtre visibles
    visibles = []
    for el in cand:
        try:
            if el.is_displayed() and el.rect.get("width", 0) > 5 and el.rect.get("height", 0) > 5:
                visibles.append(el)
        except Exception:
            continue

    if not visibles:
        return False

    # 3) Fonctions d’état — pour confirmer la sélection
    def _aria_checked_true(el):
        try:
            return (el.get_attribute("role") or "").lower() == "radio" and (el.get_attribute("aria-checked") or "").lower() == "true"
        except Exception:
            return False

    def _has_checked_input(nei):
        # cherche un input radio checked dans le voisinage du candidat (descendant/ancêtre immédiat)
        try:
            r = nei.find_elements(By.XPATH, ".//input[@type='radio' and @checked]")
            if r:
                return True
        except Exception:
            pass
        try:
            # parfois l'input est au parent direct
            parent = nei.find_element(By.XPATH, "./ancestor::*[self::label or self::div or self::li][1]")
            r = parent.find_elements(By.XPATH, ".//input[@type='radio' and @checked]")
            return bool(r)
        except Exception:
            return False

    def _has_visual_selected(el):
        try:
            cls = (el.get_attribute("class") or "").lower()
            return any(k in cls for k in ("selected", "active", "is-checked", "checked", "ui-radio-on"))
        except Exception:
            return False

    # 4) Clics (une seule méthode par essai) + validation immédiate
    for el in visibles[:8]:
        for _ in range(max_retries):
            # a) clic "safe"
            if _safe_click(driver, el):
                time.sleep(0.1)
                if _aria_checked_true(el) or _has_checked_input(el) or _has_visual_selected(el) or _mat_card_selected(el):
                    print("✅ Radio(card): sélection via safe click. source: input_handler.py")
                    return True

            # c) Séquence d’événements souris (certaines libs l’exigent)
            try:
                driver.execute_script(
                    "var e1=new MouseEvent('pointerdown',{bubbles:true});"
                    "var e2=new MouseEvent('mousedown',{bubbles:true});"
                    "var e3=new MouseEvent('mouseup',{bubbles:true});"
                    "var e4=new MouseEvent('click',{bubbles:true});"
                    "arguments[0].dispatchEvent(e1);"
                    "arguments[0].dispatchEvent(e2);"
                    "arguments[0].dispatchEvent(e3);"
                    "arguments[0].dispatchEvent(e4);",
                    el
                )
                time.sleep(0.1)
                if _aria_checked_true(el) or _has_checked_input(el) or _has_visual_selected(el) or _mat_card_selected(el):
                    print("✅ Radio(card): sélection via séquence events. source: input_handler.py")
                    return True
            except Exception:
                pass

            # d) Fallback accessibilité : focus + SPACE
            try:
                ActionChains(driver).move_to_element(el).pause(0.05).click().pause(0.05).send_keys(Keys.SPACE).perform()
                time.sleep(0.1)
                if _aria_checked_true(el) or _has_checked_input(el) or _has_visual_selected(el) or _mat_card_selected(el):
                    print("✅ Radio(card): sélection via SPACE. source: input_handler.py")
                    return True
            except Exception:
                pass

    print("↪️ Radio(card): aucun clic valide. source: input_handler.py")
    return False

# === Confirmit/Dynata ImageSelector (cards avec image) =======================
def click_confirmit_image_selector(driver, label: str, context_hint: str | None = None, max_retries: int = 2) -> bool:
    """
    Cible les cartes ImageSelector (Confirmit/Dynata) :
    - blocs .element_shadows sous #ToolContainer
    - libellé lu dans .itemtitle span OU dans img[alt] (après un `$`)
    - clic sur .clickarea (fallback: image), avec post-vérif .ticker visible ou classe 'selected/active'
    """

    def _n(s: str) -> str:
        if not s:
            return ""
        s = s.replace("\u00a0"," ")
        s = unicodedata.normalize("NFKC", s)
        s = re.sub(r"[»«“”\"'›→·•:]+"," ", s)
        return re.sub(r"\s+"," ", s, flags=re.S).strip().lower()

    want = _n(label)
    if not want:
        return False

    # 1) Scope (question courante si ctx fourni)
    try:
        scope = _find_question_container_by_ctx(driver, context_hint) or driver
    except Exception:
        scope = driver

    # 2) Collecte des blocs
    blocks = []
    try: blocks += scope.find_elements(By.CSS_SELECTOR, "#ToolContainer .element_shadows")
    except Exception: pass
    try: blocks += scope.find_elements(By.CSS_SELECTOR, ".element_shadows")
    except Exception: pass
    if not blocks:
        return False

    # 3) Libellé d’un bloc : .itemtitle span ou img[alt] (après $)
    def _block_text(b) -> str:
        txt = ""
        try:
            t = b.find_element(By.CSS_SELECTOR, ".itemtitle span")
            txt = (t.text or t.get_attribute("innerText") or "").strip()
        except Exception:
            pass
        if not txt:
            try:
                img = b.find_element(By.CSS_SELECTOR, "img[alt]")
                alt = img.get_attribute("alt") or ""
                # ex: "Male$Un homme " -> prendre la partie FR après le $
                parts = [p.strip() for p in alt.split("$") if p.strip()]
                txt = parts[-1] if parts else alt
            except Exception:
                pass
        return _n(txt)

    # 4) Choisir le meilleur bloc par matching souple
    best, best_score = None, -1
    for b in blocks:
        try:
            if not b.is_displayed(): 
                continue
            t = _block_text(b)
            if not t:
                continue
            # égalité / inclusion bilatérale
            match = (want == t) or (want in t) or (t in want)
            if match:
                sc = len(t)
                if sc > best_score:
                    best, best_score = b, sc
        except Exception:
            continue
    if best is None:
        return False

    # 5) Cibles cliquables dans le bloc
    click_targets = []
    for css in (".clickarea", ".padding10", ".nailthumb-container", "img"):
        try:
            el = best.find_element(By.CSS_SELECTOR, css)
            if el.is_displayed():
                click_targets.append(el)
        except Exception:
            continue
    if not click_targets:
        click_targets = [best]

    # 6) Clics (multi-fallback) + post-vérification
    for tgt in click_targets[:3]:
        for _ in range(max_retries):
            # a) safe click
            if _safe_click(driver, tgt, trace="confirmit_image_selector"):
                time.sleep(0.15)
            else:
                time.sleep(0.15)

            # post-check : ticker visible OU classe selected/active
            try:
                tick = best.find_element(By.CSS_SELECTOR, ".ticker")
                if getattr(tick, "is_displayed", lambda: False)():
                    print("✅ ImageSelector: ticker affiché. source: input_handler.py")
                    return True
            except Exception:
                pass
            try:
                cls = (best.get_attribute("class") or "").lower()
                if any(k in cls for k in ("selected", "active", "checked")):
                    print("✅ ImageSelector: état sélectionné (classe). source: input_handler.py")
                    return True
            except Exception:
                pass
    print("↪️ ImageSelector: aucun clic valide. source: input_handler.py")
    return False
# === FIN Confirmit/Dynata ====================================================
# === Confirmit/Dynata GridClick (scale-button) ===============================
def click_confirmit_gridclick(driver, label: str, context_hint: str | None = None, max_retries: int = 2) -> bool:
    """Clique un bouton .scale-button (Pas du tout d’accord, etc.) dans une question GridClick.
       Post-vérifie par changement de l’item courant / compteur d’items 'answered'."""
    def _n(s):
        if not s: return ""
        s = s.replace("\u00A0"," ").replace("’","'").replace("´","'").replace("`","'")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+"," ", s, flags=re.S).strip().lower()

    want = _n(label)
    if not want:
        return False

    # Scope: bloc de question si dispo
    try:
        scope = _find_question_container_by_ctx(driver, context_hint) or driver
    except Exception:
        scope = driver

    # Conteneur GridClick
    try:
        cont = scope.find_element(By.CSS_SELECTOR, ".gridclick-container")
    except Exception:
        return False

    # État avant clic (pour post-vérif)
    def _state():
        try:
            cur = cont.find_element(By.CSS_SELECTOR, ".progress-indicator .currentNode")
            return ("idx", cur.get_attribute("data-index") or "")
        except Exception:
            pass
        try:
            answered = len(cont.find_elements(By.CSS_SELECTOR, ".item.item-text.answered"))
            return ("answered", str(answered))
        except Exception:
            return ("", "")

    kind0, val0 = _state()

    # Chercher le bouton par son texte
    btn = None
    for el in cont.find_elements(By.CSS_SELECTOR, ".scale-button"):
        try:
            txt = _n(el.text or el.get_attribute("innerText") or "")
            if not txt:
                t2 = el.find_element(By.CSS_SELECTOR, ".text-content")
                txt = _n(t2.text or t2.get_attribute("innerText") or "")
        except Exception:
            txt = ""
        if txt and (want == txt or want in txt or txt in want):
            btn = el
            break
    if not btn:
        return False

    for _ in range(max_retries):
        # clic « sûr » → JS → séquence d’événements
        if _safe_click(driver, btn, trace="gridclick_btn"):
            time.sleep(0.12)
        else:
            time.sleep(0.12)

        # Post-check : item courant/compteur a-t-il changé ?
        deadline = time.time() + 1.0
        while time.time() < deadline:
            kind1, val1 = _state()
            if kind1 == kind0 and val1 and val1 != val0:
                print("✅ GridClick: progression avancée. source: input_handler.py")
                return True
            try:
                cls = (btn.get_attribute("class") or "").lower()
                if any(k in cls for k in ("selected","active","is-selected")):
                    print("✅ GridClick: bouton marqué sélectionné. source: input_handler.py")
                    return True
            except Exception:
                pass
            time.sleep(0.05)

    print("↪️ GridClick: aucun clic valide. source: input_handler.py")
    return False
# === FIN GridClick ===========================================================

def click_radio_by_label(driver, label: str, context_hint: str | None = None) -> bool:
    """
    Coche le bouton radio correspondant à `label`.
    Accepte aussi 'Libellé //// radio'. Couvre :
    - <label for="..."> + <input type=radio id="...">
    - input radio voisin de <label>
    - conteneurs ARIA role="radio" qui contiennent le texte (YouGov, etc.)
    - blocs stylés (answer/option/choice)
    Après sélection, tente un bouton 'Suivant/Continuer'.
    """

    def _norm_radio(s: str) -> str:
        if not s:
            return ""
        s = unicodedata.normalize("NFKC", s).replace("\u00a0", " ").lower().strip()
        s = re.sub(r"[»«“”\"'›→·•:]+", "", s)
        s = re.sub(r"\s+", " ", s)
        return s
    
    # Confirmit GridClick (échelle à droite, pas de <input>)
    if click_confirmit_gridclick(driver, label=label, context_hint=context_hint):
        print(f"✅ Radio(GridClick) « {label} ». source: input_handler.py")
        return True


    try:
        if _click_decipher_grid_radio(driver, label, context_hint or ""):
            ("Decipher grid radio")
            return True
    except Exception:
        pass

    try:
        # priorité à la voie stricte Decipher pour les tables .grid
        if _click_decipher_grid_radio_strict(driver, label, context_hint or ""):
            return True
    except Exception:
        pass

    lbl = _norms_txt(label)
    container = _find_question_container_by_ctx(driver, context_hint)

    scope = container if container is not None else driver

    wait = WebDriverWait(driver, 5)

    # NEW — cartes Confirmit/Dynata rendues par JavaScript (pas d'<input> visible)
    if click_radio_cardlike_js(driver, label=label, context_hint=context_hint):
        print(f"✅ Radio(card) « {label} ». source: input_handler.py")
        return True
    
    # NEW — Confirmit/Dynata ImageSelector (grid d’images)
    if click_confirmit_image_selector(driver, label=label, context_hint=context_hint):
        print(f"✅ Radio(ImageSelector) « {label} ». source: input_handler.py")
        return True


    # 1) Cas table (decipherinc) : <tr><th>Libellé</th><td ...><label for="id"></label></td></tr>
    try:
        tr = scope.find_element(
            By.XPATH,
            ".//tr[.//th[normalize-space()="
            f"\"{label.strip()}\""
            "]]"
        )
        # privilégier le label cliquable dans la cellule
        lab = tr.find_element(By.XPATH, ".//td[contains(@class,'clickableCell')]//label")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", lab)
        wait.until(EC.element_to_be_clickable(lab))
        driver.execute_script("arguments[0].click();", lab)
        return True
    except Exception:
        pass
    
    # 2) Cas label direct : <label>Libellé</label> avec un input sibling
    try:
        lab = scope.find_element(
            By.XPATH,
            ".//label[normalize-space()="
            f"\"{label.strip()}\""
            "]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", lab)
        wait.until(EC.element_to_be_clickable(lab))
        driver.execute_script("arguments[0].click();", lab)
        return True
    except Exception:
        pass
    
    # 3) Cas input radio voisin d’un texte (moins propre mais utile en dernier recours)
    try:
        inp = scope.find_element(
            By.XPATH,
            ".//input[@type='radio' and not(contains(@class,'disabled'))]"
            "[ancestor::div[contains(@class,'question')]]"
            "[following::text()[normalize-space()="
            f"\"{label.strip()}\""
            "]]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
        driver.execute_script("arguments[0].click();", inp)
        # sécuriser l’état + events
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

    # 🔎 Essai prioritaire dans le conteneur de la question (si fourni)
    scope = _find_context_container(driver, context_hint)
    # 🔒 Decipher/Confirmit table : si on a un scope, essayer d’abord un clic strict
    if scope is not None:
        if _click_radio_label_in_scope(driver, scope, label):
            print(f"✅ Radio cochée (Decipher/table via scope) : {label}")
            try:
                setattr(driver, "last_action_success", True)
                setattr(driver, "_post_action_t0", time.time())
            except Exception:
                pass
            _click_next_any(driver)
            return True

    anchor_y = None
    if scope is not None:
        try:
            # l'entête de question la plus proche sert d'ancre
            hdr = scope.find_element(By.XPATH, ".//legend|.//h1|.//h2|.//h3|.//*[contains(@class,'question-text')][1]")
            anchor_y = hdr.rect.get("y", None)
        except Exception:
            try:
                anchor_y = scope.rect.get("y", None)
            except Exception:
                pass

    root = scope if scope is not None else driver

    # --- 1) Pattern Angular/cc-radio mais ANCRÉ sous l’entête ---
    try:
        xp = ("(.//div[contains(@class,'fr-option') or contains(@class,'cc-radio') or contains(@class,'radio')])"
              f"//label[.//span[contains(@class,'cc-radio__label')] and contains(translate(normalize-space(string(.)),"
              "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), { _xpath_literal(needle) })]")
        cands = root.find_elements(By.XPATH, xp)
        best = None; best_dy = 1e9
        for lbl in cands:
            y = lbl.rect.get("y", 0)
            if anchor_y is not None and y + 1 < anchor_y:  # ignorer ce qui est au-dessus de l’en-tête
                continue
            dy = abs((anchor_y or y) - y)
            if dy < best_dy:
                best, best_dy = lbl, dy
        if best is not None:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
            try: wait.until(EC.element_to_be_clickable(best)).click()
            except Exception: driver.execute_script("arguments[0].click();", best)
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

    # --- 2) Recherche générale ANCRÉE dans le scope (labels/role=radio/options) ---
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
            except Exception: ActionChains(driver).move_to_element(best).click().perform()
            return True

        # IMPORTANT : si on avait un scope mais rien de valide dedans, on STOP
        return False
     
    # 1) Pattern Angular/cc-radio (ton cas)
    try:
        xp = (
            # conteneur commun
            "(//div[contains(@class,'fr-option') or contains(@class,'cc-radio') or contains(@class,'radio')])"
            # label cliquable contenant le span 'cc-radio__label' avec le texte
            f"//label[.//span[contains(@class,'cc-radio__label')]"
            f" and contains(normalize-space(string(.)), {_xpath_literal(target)})]"
        )
        lbl = driver.find_element(By.XPATH, xp)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", lbl)
        try:
            wait.until(EC.element_to_be_clickable((By.XPATH, xp))).click()
        except Exception:
            # secours JS
            driver.execute_script("arguments[0].click();", lbl)

        # 2) Vérification + forçage si nécessaire
        try:
            for_id = lbl.get_attribute("for")
            if for_id:
                inp = driver.find_element(By.ID, for_id)
                if not inp.is_selected():
                    driver.execute_script(
                        "arguments[0].checked = true;"
                        "try{arguments[0].dispatchEvent(new Event('input',{bubbles:true}));}catch(e){}"
                        "try{arguments[0].dispatchEvent(new Event('change',{bubbles:true}));}catch(e){}",
                        inp
                    )
        except Exception:
            pass
        return True
    except Exception:
        pass

    try:
        if scope is not None:
            try:
                # label[for] descendant
                try:
                    lbl = scope.find_element(
                        By.XPATH,
                        f".//label[normalize-space()!='' and contains(translate(normalize-space(.),"
                        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{needle}')]",
                    )
                    fid = lbl.get_attribute("for")
                    if fid:
                        rb = driver.find_element(By.ID, fid)
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", rb)
                        ActionChains(driver).move_to_element(rb).click().perform()
                        try: 
                            setattr(driver, "last_action_success", True); setattr(driver, "_post_action_t0", time.time())
                        except: pass
                        _click_next_any(driver)
                        return True
                except Exception:
                    pass
                
                # fallback : conteneur d’option descendant dans scope
                try:
                    opt = scope.find_element(
                        By.XPATH,
                        ".//*[contains(@class,'answer') or contains(@class,'option') or contains(@class,'choice') or self::li]"
                        "[.//text()[normalize-space()!='']]"
                        f"[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{needle}')]"
                    )
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
                    try: 
                        opt.click()
                    except: ActionChains(driver).move_to_element(opt).click().perform()
                    try: 
                        setattr(driver, "last_action_success", True)
                        setattr(driver, "_post_action_t0", time.time())
                    except: pass
                    _click_next_any(driver)
                    return True
                except Exception:
                    pass
            except Exception:
                pass
        
        # Dernier essai strictement scoppé si un scope/ancre existe
        if scope is not None and anchor_y is not None:
            needle = _norm_radio(label)
            cands = scope.find_elements(By.XPATH,
                ".//label[normalize-space()!=''] | .//*[@role='radio'] | .//*[contains(@class,'option') or contains(@class,'choice')]")
            best, best_dy = None, 1e9
            for el in cands:
                try:
                    txt = _norm_radio(el.text or el.get_attribute('innerText') or '')
                    if not (needle == txt or needle in txt or txt in needle):
                        continue
                    y = el.rect.get("y", 0)
                    if y + 1 >= anchor_y:  # uniquement sous l'entête
                        dy = abs(y - anchor_y)
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

            # IMPORTANT : si on avait un scope mais rien de valide dedans, on STOP ici
            # pour éviter d'aller cliquer un "Un homme" ailleurs dans la page.
            return False


        # 1) label[for] → input#id
        try:
            lbl = driver.find_element(
                By.XPATH,
                f"//label[normalize-space()!='' and contains(translate(normalize-space(.),"
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{needle}')]",
            )
            for_id = lbl.get_attribute("for")
            if for_id:
                rb = driver.find_element(By.ID, for_id)
                try:
                    if hasattr(rb, "is_selected") and rb.is_selected():
                        print(f"ℹ️ Radio déjà cochée : {label} → on tente le Next.")
                        try:
                            setattr(driver, "last_action_success", True)
                            setattr(driver, "_post_action_t0", time.time())
                        except Exception:
                            pass
                        _click_next_any(driver)
                        return True
                except Exception:
                    pass
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", rb
                )
                time.sleep(0.1)
                ActionChains(driver).move_to_element(rb).click().perform()
                print(f"✅ Radio coché (via for/id) : {label}")
                try:
                    setattr(driver, "last_action_success", True)
                    setattr(driver, "_post_action_t0", time.time())
                except Exception:
                    pass
                _click_next_any(driver)
                return True

        except Exception:
            pass

        # 2) input + label frère contenant le texte (Decipher très courant)
        try:
            el = driver.find_element(
                By.XPATH,
                "//input[@type='radio' and (following-sibling::label|preceding-sibling::label)"
                "[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{}')]]".format(
                    needle
                ),
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.click()
            except:
                ActionChains(driver).move_to_element(el).click().perform()
            print(f"✅ Radio cochée (input + label frère) : {label}")
            try:
                setattr(driver, "last_action_success", True)
                setattr(driver, "_post_action_t0", time.time())
            except Exception:
                pass
            _click_next_any(driver)
            return True

        except Exception:
            pass

        # 3) conteneurs d’option (div/li) avec le texte + input radio descendant
        try:
            opt = driver.find_element(
                By.XPATH,
                "//*[contains(@class,'answer') or contains(@class,'option') or contains(@class,'choice') "
                "   or contains(@class,'SingleAnswer') or self::li]"
                "[.//text()[normalize-space()!='']]"
                "[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{}')]".format(
                    needle
                ),
            )
            # cherche un input radio dedans
            try:
                rb = opt.find_element(
                    By.CSS_SELECTOR, "input[type='radio'], [role='radio']"
                )
            except:
                rb = None

            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
            try:
                # souvent seul le conteneur est cliquable
                opt.click()
            except:
                try:
                    ActionChains(driver).move_to_element(opt).click().perform()
                except:
                    driver.execute_script("arguments[0].click();", opt)
            # si on a un vrai input, essaye de le cocher explicitement (idempotent)
            if rb:
                try:
                    if getattr(rb, "is_selected", lambda: False)():
                        pass
                    else:
                        rb.click()
                except:
                    try:
                        ActionChains(driver).move_to_element(rb).click().perform()
                    except:
                        driver.execute_script("arguments[0].click();", rb)

            print(f"✅ Radio cochée (conteneur d’option) : {label}")
            try:
                setattr(driver, "last_action_success", True)
                setattr(driver, "_post_action_t0", time.time())
            except Exception:
                pass
            _click_next_any(driver)
            return True

        except Exception:
            pass

        # 4) ARIA faux-radios (role='radio')
        try:
            aria = driver.find_element(
                By.XPATH,
                "//*[@role='radio' and (contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{}') "
                "   or contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{}'))]".format(
                    needle, needle
                ),
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", aria
            )
            try:
                aria.click()
            except:
                ActionChains(driver).move_to_element(aria).click().perform()
            print(f"✅ Radio cochée (ARIA) : {label}")
            try:
                setattr(driver, "last_action_success", True)
                setattr(driver, "_post_action_t0", time.time())
            except Exception:
                pass
            _click_next_any(driver)
            return True

        except Exception:
            pass

        # 5) filet de sécurité : texte -> remonter au parent cliquable puis radio descendant
        try:
            txt_host = driver.find_element(
                By.XPATH,
                "//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{}')]".format(
                    needle
                ),
            )
            host = txt_host.find_element(
                By.XPATH, "ancestor::*[self::div or self::li or self::label][1]"
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", host
            )
            try:
                host.click()
            except:
                ActionChains(driver).move_to_element(host).click().perform()

            try:
                rb = host.find_element(
                    By.CSS_SELECTOR, "input[type='radio'], [role='radio']"
                )
                if getattr(rb, "is_selected", lambda: False)():
                    pass
                else:
                    try:
                        rb.click()
                    except:
                        driver.execute_script("arguments[0].click();", rb)
            except:
                pass

            print(f"✅ Radio coché (fallback proximité/contener radio) : {label}")
            try:
                setattr(driver, "last_action_success", True)
                setattr(driver, "_post_action_t0", time.time())
            except Exception:
                pass
            _click_next_any(driver)
            return True

        except Exception:
            pass

        # 6) Fallback générique : proche du texte → conteneur "radio" custom (ex. GfK: .prettyradio)
        try:
            # on localise d'abord le texte cible quelque part dans la ligne
            txt_el = driver.find_element(
                By.XPATH,
                f"//*[normalize-space()!='' and contains(translate(normalize-space(.),"
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{needle}')]",
            )
            # remonter au conteneur de "ligne" qui contient un élément radio custom
            row = txt_el.find_element(
                By.XPATH,
                "ancestor::*[self::li or self::div or self::td][.//input[@type='radio'] "
                " or .//*[@role='radio'] "
                " or .//*[contains(@class,'prettyradio')]"
                "][1]",
            )

            # cible cliquable dans la ligne (ordre de préférence)
            for xp in [
                ".//input[@type='radio']",
                ".//*[@role='radio']",
                ".//*[contains(@class,'prettyradio')]//a | .//*[contains(@class,'prettyradio')]",
            ]:
                try:
                    rb = row.find_element(By.XPATH, xp)
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", rb
                    )
                    time.sleep(0.1)
                    try:
                        rb.click()
                    except Exception:
                        ActionChains(driver).move_to_element(rb).click().perform()
                    print(
                        f"✅ Radio coché (fallback proximité/contener radio) : {label}"
                    )
                    _click_next_any(driver)
                    return True
                except Exception:
                    continue
        except Exception:
            pass

    except Exception as e:
        print("💥 Erreur dans click_radio_by_label :", e, "source: input_handler.py")

    # 7) Fallback ultime : JS générique (Decipher/Confirmit & co)
    try:
        if _fallback_click_radio_js_generic(driver, label):
            print(f"✅ Radio cochée (fallback JS générique) : {label}")
            try:
                setattr(driver, "last_action_success", True)
                setattr(driver, "_post_action_t0", time.time())
            except Exception:
                pass
            _click_next_any(driver)
            return True

    except Exception:
        pass
        return False

def _fallback_click_checkbox_js_alchemer(driver, target_text: str) -> bool:
    """
    Fallback ciblé Alchemer (classes 'sg-*'):
    - Matche le texte dans la liste .sg-type-checkbox
    - Préfère <label for="..."> puis coche l'<input id="..."> lié
    - Dispatch 'input' + 'change' pour frameworks
    """
    js = r"""
    const norm = s => (s||'').toLowerCase()
      .normalize('NFKC').replace(/\u00A0/g,' ')
      .replace(/[»«“”"'›→·•:]/g,'').replace(/\s+/g,' ').trim();
    const needle = norm(arguments[0]);

    // Limiter la recherche au bloc checkbox d'Alchemer
    const roots = Array.from(document.querySelectorAll(
      '.sg-type-checkbox, .sg-question-options, ul.sg-list'
    ));
    if (!roots.length) return false;

    // Collecte <label> avec texte
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

    // Choisir le meilleur label (le plus long = plus discriminant)
    items.sort((a,b)=>(b.innerText||'').length-(a.innerText||'').length);
    const lab = items[0];
    lab.scrollIntoView({block:'center'});

    // Récupérer l'input par @for ou voisinage
    let inp = null;
    const fid = lab.getAttribute('for');
    if (fid) inp = document.getElementById(fid);
    if (!inp){
      // input frère/voisin dans le même <li>
      const li = lab.closest('li') || lab.parentElement;
      if (li) inp = li.querySelector('input[type=checkbox], input[type=radio]');
    }
    if (!inp) return false;

    // Clic sur label d’abord (comportement naturel Alchemer)
    try { lab.click(); } catch(e){}

    // Sécuriser l’état + events
    try {
      if (!inp.checked) inp.checked = true;
      inp.dispatchEvent(new Event('input', {bubbles:true}));
      inp.dispatchEvent(new Event('change', {bubbles:true}));
    } catch(e){}

    // Validation d’état
    return !!(inp.checked || (inp.getAttribute('aria-checked')||'').toLowerCase()==='true');
    """
    try:
        return bool(driver.execute_script(js, target_text))
    except Exception:
        return False

def _fallback_click_checkbox_js_generic(driver, target_text: str) -> bool:
    """
    Fallback générique multi-sites :
    - Matche par innerText sur <label>/<span> proches
    - Clique label/wrapper, ou force checked=true + events
    """
    js = r"""
    const norm = s => (s||'').toLowerCase()
      .normalize('NFKC').replace(/\u00A0/g,' ')
      .replace(/[»«“”"'›→·•:]/g,'').replace(/\s+/g,' ').trim();
    const needle = norm(arguments[0]);

    const candidates = [];
    candidates.push(...document.querySelectorAll('label, .checkbox, [role=checkbox]'));
    candidates.push(...document.querySelectorAll('span, div, li'));

    // score par proximité + surface
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

    // Essayer clic naturel
    try { best.click(); } catch(e){}

    // Synchroniser l'input lié si présent
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

def _fallback_click_radio_js_generic(driver, target_text: str) -> bool:
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
      .replace(/[»«“”"'›→·•:]/g,'').replace(/\s+/g,' ').trim();
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
        const d = Math.abs(ct.y - cr.y) + Math.abs(ct.x - cr.x)*0.3; // poids vertical
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

# === Confirmit: table (checkbox/radio en <tr>) ===============================
def click_confirmit_checktable(driver, label: str, context_hint: str | None = None, max_retries: int = 2) -> bool:
    """
    Coche une case (ou radio exclusive) dans une table Confirmit :
    <tr class="cRow/rsRow...">
      <td><input type="checkbox|radio" id="..."></td>
      <td><label for="..."><div><p>Texte ...</p></div></label></td>
    Post-vérifie via is_selected()/@checked.
    """
    def _n(s: str) -> str:
        if not s: return ""
        s = s.replace("\u00A0", " ").replace("’", "'").replace("´", "'").replace("`", "'")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        s = re.sub(r"\s+", " ", s, flags=re.S).strip()
        return s.lower()

    want = _n(label)
    if not want:
        return False

    # Scope: bloc de la question si dispo
    try:
        scope = _find_question_container_by_ctx(driver, context_hint) or driver
    except Exception:
        scope = driver

    # Candidats: lignes de réponses
    rows = []
    try:
        rows += scope.find_elements(By.XPATH, ".//tr[contains(@class,'cRow') or contains(@class,'rsRow')]")
    except Exception:
        pass
    if not rows:
        return False

    def _row_text(row) -> str:
        # Texte de la cellule libellé
        try:
            cell = row.find_element(By.XPATH, ".//td[contains(@class,'cCellRowText')]")
        except Exception:
            try:
                cell = row.find_element(By.XPATH, ".//td[2]")
            except Exception:
                return ""
        try:
            t = cell.text or cell.get_attribute("innerText") or ""
        except Exception:
            t = ""
        return _n(t)

    # Cherche la meilleure ligne par matching souple
    best = None
    best_len = -1
    for r in rows:
        try:
            if not r.is_displayed():
                continue
            txt = _row_text(r)
            if not txt:
                continue
            if want == txt or want in txt or txt in want:
                if len(txt) > best_len:
                    best, best_len = r, len(txt)
        except Exception:
            continue
    if best is None:
        return False

    # Localise input + (fallback) label[for]
    target_input = None
    target_label = None
    try:
        target_input = best.find_element(By.XPATH, ".//td[1]//input[@type='checkbox' or @type='radio']")
    except Exception:
        pass
    try:
        target_label = best.find_element(By.XPATH, ".//td[2]//label[@for]")
    except Exception:
        pass

    # Fonction de post-check
    def _is_checked(inp):
        try:
            if inp.is_selected():
                return True
        except Exception:
            pass
        try:
            if inp.get_attribute("checked"):
                return True
        except Exception:
            pass
        return False

    # Tentatives de clic:
    for _ in range(max_retries):
        # 1) clic direct sur l'input si possible
        if target_input:
            if _safe_click(driver, target_input, trace="confirmit_table_input"):
                time.sleep(0.1)
            else:
                time.sleep(0.1)
            if _is_checked(target_input):
                print("✅ Confirmit table: input coché. source: input_handler.py")
                return True

        # 2) sinon: clic sur le label[for]
        if target_label:
            if _safe_click(driver, target_label, trace="confirmit_table_label"):
                time.sleep(0.1)
            else:
                time.sleep(0.1)
            # re-récup input via @for
            try:
                if not target_input:
                    for_id = target_label.get_attribute("for")
                    if for_id:
                        target_input = best.find_element(By.ID, for_id)
            except Exception:
                pass
            if target_input and _is_checked(target_input):
                print("✅ Confirmit table: label cliqué → coché. source: input_handler.py")
                return True

    print("↪️ Confirmit table: échec de coche. source: input_handler.py")
    return False
# === FIN Confirmit table =====================================================

# === Decipher/FIR checkbox (input caché + icône SVG) =========================
def click_decipher_fir_checkbox(driver, label: str, context_hint: str | None = None, max_retries: int = 2) -> bool:
    """
    Coche un checkbox Decipher/FIR :
      <input class="fir-hidden ...">, voisin <span class="fir-icon"><svg>...</svg></span>,
      libellé dans <label for="ID"> ... </label>.
    Stratégie :
      - retrouver le label par texte normalisé (apostrophes/NBSP/diacritiques),
      - résoudre l'input via @for (attention: ID avec '.'),
      - cliquer label -> .fir-icon -> .cell-input -> JS click(input),
      - post-check: is_selected()/@checked ; sinon forcer checked + events (1 seule fois).
    """
    def _norm(s: str) -> str:
        if not s: return ""
        s = s.replace("\u00A0"," ").replace("’","'").replace("´","'").replace("`","'")
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        s = re.sub(r"\s+"," ", s, flags=re.S).strip()
        return s.lower()

    want = _norm(label)
    if not want:
        return False

    # Scope
    try:
        scope = _find_question_container_by_ctx(driver, context_hint) or driver
    except Exception:
        scope = driver

    # 1) Trouver le LABEL par texte (le texte est dans <label> ... )
    labels = []
    try:
        labels = scope.find_elements(By.XPATH, ".//label[@for]")
    except Exception:
        pass
    target_label, target_input = None, None
    for lab in labels:
        try:
            txt = _norm(lab.text or lab.get_attribute("innerText") or "")
            if not txt: 
                continue
            if want == txt or want in txt or txt in want:
                target_label = lab
                break
        except Exception:
            continue
    if not target_label:
        return False

    # 2) Résoudre l'INPUT depuis @for (éviter CSS #id car id contient '.')
    try:
        for_id = target_label.get_attribute("for") or ""
        if for_id:
            target_input = scope.find_element(By.XPATH, f".//input[@id='{for_id}']")
    except Exception:
        target_input = None

    # 3) Préparer les cibles cliquables
    click_targets = []
    # a) label[for]
    if target_label:
        click_targets.append(target_label)
    # b) l'icône FIR voisine
    try:
        icon = target_label.find_element(By.XPATH, "./ancestor::span[contains(@class,'cell-sub-wrapper')][1]//span[contains(@class,'fir-icon')]")
        click_targets.append(icon)
    except Exception:
        pass
    # c) le wrapper cell-input
    try:
        cell_input = target_label.find_element(By.XPATH, "./ancestor::span[contains(@class,'cell-sub-wrapper')][1]//span[contains(@class,'cell-input')]")
        click_targets.append(cell_input)
    except Exception:
        pass
    # d) l'input (en dernier recours)
    if target_input:
        click_targets.append(target_input)

    # 4) Fonctions utiles
    def _checked(inp):
        if not inp: 
            return False
        try:
            if inp.is_selected():
                return True
        except Exception:
            pass
        try:
            v = (inp.get_attribute("checked") or "").lower()
            if v in ("true","checked","1"): 
                return True
        except Exception:
            pass
        try:
            # domProperty 'checked' si dispo
            v2 = inp.get_dom_property("checked")
            if bool(v2):
                return True
        except Exception:
            pass
        return False

    def _ensure_events(inp):
        # Forcer l'état + events une seule fois si toujours pas coché
        try:
            driver.execute_script("""
                var el = arguments[0];
                if (!el.checked) el.checked = true;
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
            """, inp)
        except Exception:
            pass

    # 5) Essais de clic (une méthode par essai, sortie immédiate si succès)
    for tgt in click_targets:
        for _ in range(max_retries):
            if _safe_click(driver, tgt, trace="decipher_fir_cb"):
                time.sleep(0.12)
            else:
                time.sleep(0.12)

            if target_input and _checked(target_input):
                print("✅ Decipher/FIR: coché via clic. source: input_handler.py")
                return True

            # Si l'on connaît l'input, sécuriser l'état une fois
            if target_input and not _checked(target_input):
                _ensure_events(target_input)
                time.sleep(0.08)
                if _checked(target_input):
                    print("✅ Decipher/FIR: coché via events. source: input_handler.py")
                    return True

    print("↪️ Decipher/FIR: échec de coche. source: input_handler.py")
    return False
# === FIN Decipher/FIR ========================================================

def _force_checkbox_events(driver, checkbox_el):
    driver.execute_script(
        """
        const cb = arguments[0];
        cb.checked = true;
        cb.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        cb.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
        cb.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        cb.dispatchEvent(new Event('change', { bubbles: true }));
        cb.dispatchEvent(new Event('input', { bubbles: true }));
        """,
        checkbox_el
    )

def _privacy_checkbox_is_accepted(driver) -> bool:
    try:
        warn = driver.find_element(By.ID, "privacyPolicyFeedback7")
        return not warn.is_displayed()
    except Exception:
        return True  # plus de warning → OK

def click_checkbox_by_label(
    driver,
    target_text: str,
    context_hint: str | None = None,
):
    """
    Clique un checkbox identifié par son label visible.
    Retourne le WebElement <input type="checkbox"> si succès, sinon None.
    """

    needle = _norm_lc_soft(target_text)
    if not needle:
        return None
    
    scope = _find_context_container(driver, context_hint)

    # ------------------------------------------------------------------
    # 1) Cas standard : <label for="id"> → <input id="id" type="checkbox">
    # ------------------------------------------------------------------
    try:
        labels = (scope or driver).find_elements(
            By.XPATH,
            ".//label[normalize-space()!='' and contains("
            "translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ',"
            "'abcdefghijklmnopqrstuvwxyzàâäéèêëîïôöùûüç'),"
            f"{_xpath_literal(needle)}"
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

            _scroll_into_view(driver, cb)

            if not _is_checked(cb):
                try:
                    cb.click()
                except Exception:
                    _js_click(driver, cb)

            # 🔥 FORCER LES EVENTS JS (clé Ipsos)
            _force_checkbox_events(driver, cb)

            if _is_checked(cb):
                return cb

    except Exception:
        pass

    # ------------------------------------------------------------------
    # 2) Cas checkbox ARIA / custom (role="checkbox")
    # ------------------------------------------------------------------
    try:
        boxes = (scope or driver).find_elements(
            By.XPATH,
            ".//*[@role='checkbox' or @aria-checked]"
        )

        for box in boxes:
            txt = _norm(
                box.text or box.get_attribute("aria-label") or ""
            )
            if needle not in txt:
                continue

            _scroll_into_view(driver, box)

            try:
                box.click()
            except Exception:
                _js_click(driver, box)

            # aria-checked doit passer à true
            if box.get_attribute("aria-checked") == "true":
                return box

    except Exception:
        pass

    # ------------------------------------------------------------------
    # 3) Cas fallback Confirmit / tables (si déjà existant chez toi)
    # ------------------------------------------------------------------
    try:
        cb = click_confirmit_checktable(
            driver,
            label=target_text,
            context_hint=context_hint,
            return_element=True,   # ⚠️ si possible, sinon enlève
        )
        if cb:
            _force_checkbox_events(driver, cb)
            return cb
    except Exception:
        pass

    return None

def _xpath_literal(s: str) -> str:
    """Construit un littéral XPath sûr (gère les quotes)."""
    # on travaille sur la version normalisée déjà minuscule
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ',"\'",'.join([f"'{p}'" for p in parts]) + ")"

def _click_next_any(driver):
    """
    Clique le bouton de navigation après sélection.
    Supporte data-test-id, <button> textuels et <input type=submit>.
    """
    wait = WebDriverWait(driver, 5)

    # a) selectors spécifiques (quand dispo)
    try:
        btn = driver.find_element(
            By.CSS_SELECTOR, 'button[data-test-id="ps-common-actions-button"]'
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.2)
        driver.execute_script("arguments[0].click();", btn)
        print("➡️ Bouton (data-test-id) cliqué.")
        return True
    except Exception:
        pass

    # b) libellés communs
    try:
        btn = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[contains(., 'Suivant') or contains(., 'Continuer') or contains(., 'Next') or contains(., 'Continue')]",
                )
            )
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.2)
        driver.execute_script("arguments[0].click();", btn)
        print("➡️ Bouton navigation cliqué (texte).")
        return True
    except Exception:
        pass

    # c) submit
    try:
        sub = driver.find_element(By.CSS_SELECTOR, "input[type='submit']")
        driver.execute_script("arguments[0].click();", sub)
        print("➡️ Submit cliqué.")
        return True
    except Exception:
        pass

    return False

# --- Helper générique : setter "réactif" + events ---
def _react_set_value_and_fire(driver, el, value: str):
    """
    Pose la valeur via le setter natif (React/PRDG friendly) puis déclenche les
    évènements que ces frameworks attendent.
    """
    try:
        driver.execute_script("""
            const el = arguments[0], v = arguments[1];
            const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                   || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
            if (d && d.set) { d.set.call(el, v); } else { el.value = v; }
            // Séquence d'évènements "humaine"
            try { el.dispatchEvent(new Event('input',  {bubbles:true})); } catch(e){}
            try { el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, key:'0'})); } catch(e){}
            try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch(e){}
            try { el.dispatchEvent(new Event('blur',   {bubbles:true})); } catch(e){}
        """, el, value)
        return True
    except Exception:
        return False

def _swagbucks_zip_patch(driver, value: str) -> bool:
    """
    Patch ciblé Swagbucks (champ zip):
    - cible #profilerNumericInput
    - clear + saisie "humaine" (CDP) + events JS
    - lève le 'disabled' sur le bouton Continue et clique
    """

    try:
        el = driver.find_element(By.ID, "profilerNumericInput")
    except Exception:
        return False  # pas Swagbucks

    # normalise: on ne garde que des chiffres si dispo
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        digits = value or "95000"

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.05)
        try:
            el.click()
        except Exception:
            ActionChains(driver).move_to_element(el).click().perform()
        # clear
        try:
            el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE)
        except Exception:
            pass

        # 1) Frappe simulée via CDP : un premier caractère pour lever 'disabled'
        first = (digits or value or "9")[0]
        try:
            driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type":"keyDown","text": first, "unmodifiedText": first})
            driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type":"keyUp",  "text": first, "unmodifiedText": first})
            time.sleep(0.05)
        except Exception:
            try:
                el.send_keys(first)
            except Exception:
                pass

        # 2) Pose de la valeur complète via setter natif + events
        _react_set_value_and_fire(driver, el, digits or value or "95000")

        # 3) Tentative de lever 'disabled' (filet de sécurité)
        driver.execute_script("""
            const btn = document.querySelector('button#profilerSubmit, button.profilerSubmit, button[id*="profilerSubmit"]');
            if (btn) { try { btn.removeAttribute('disabled'); } catch(e){} }
        """)
        time.sleep(0.15)

        # 4) Clique "Continue"
        try:
            if click_cta_strong_any_context(driver, "continue"):
                return True
            else:
                btn = driver.find_element(By.CSS_SELECTOR, "button#profilerSubmit, button.profilerSubmit, button[id*='profilerSubmit']")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                try: btn.click()
                except Exception: ActionChains(driver).move_to_element(btn).click().perform()
            time.sleep(0.2)
        except Exception:
            pass
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "button#profilerSubmit, button.profilerSubmit, button[id*='profilerSubmit']")
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            try:
                btn.click()
            except Exception:
                ActionChains(driver).move_to_element(btn).click().perform()
            time.sleep(0.2)
        except Exception:
            pass

        # Vérification finale de la valeur
        cur = el.get_attribute("value") or ""
        return cur.strip() == digits
    except Exception:
        return False

def fill_text_input(driver, text: str, context_hint: str | None = None) -> bool:
    """
    Saisie fiable dans input/textarea/contenteditable :
    - scroll+focus
    - clear (CTRL+A, DELETE)
    - filtrage chiffres si le champ est numérique
    - fallback JS (dispatch 'input' & 'change')
    - petit 'nudge' clavier pour React/Angular
    """

    wait = WebDriverWait(driver, 10)

    # Champ texte générique
    selector = "input[type='text'], input[type='search'], input[type='number'], textarea, [contenteditable='true'], input[type='textarea']"
    field = None
    scope = _find_context_container(driver, context_hint)
    print(f"fill_text_input: scope hint='{context_hint}' -> {('none' if scope is None else scope.tag_name)}")

    # --- Voie rapide captcha PureSpectrum ---------------------------------
    try:
        ctx_lc = (context_hint or "").lower()
        has_pscaptcha = bool(driver.find_elements(By.ID, "pscaptcha"))
        if has_pscaptcha or ("captcha" in ctx_lc or "code" in ctx_lc):
            # 1) scope = ancêtre du bloc captcha ou de l'entête covered-if
            try:
                scope = driver.find_element(By.XPATH, "//*[@id='pscaptcha']/ancestor::*[self::h5 or self::div or self::section][1]")
            except Exception:
                try:
                    scope = driver.find_element(
                        By.XPATH,
                        "//h5[contains(@class,'covered-if')][contains(translate(normalize-space(.),"
                        " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'taper le code')]/ancestor::*[self::h5 or self::div or self::section][1]"
                    )
                except Exception:
                    pass

            # 2) cibler l'input spécifique du captcha
            if scope is not None:
                try:
                    field = scope.find_element(By.XPATH,
                        ".//input[contains(translate(@ng-change,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'handlepscaptcha') "
                        " or starts-with(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'ans')]"
                    )
                except Exception:
                    # secours : prendre un textarea/input 'textarea' visible sous ce scope
                    try:
                        field = scope.find_element(By.XPATH, ".//textarea | .//input[@type='textarea']")
                    except Exception:
                        pass
    except Exception:
        pass
    # ----------------------------------------------------------------------

    if scope is not None:
        try:
            cands = [e for e in scope.find_elements(By.CSS_SELECTOR, selector) if e.is_displayed()]

            def _score_input(el):
                s = 0
                tag = (el.tag_name or "").lower()
                if tag in ("input", "textarea"): s += 2
                typ = (el.get_attribute("type") or "").lower()
                if typ in ("number", "tel"): s += 2
                im = (el.get_attribute("inputmode") or "").lower()
                if im in ("numeric", "decimal"): s += 2
                aid = (el.get_attribute("id") or "").lower()
                name = (el.get_attribute("name") or "").lower()
                ph = ((el.get_attribute("placeholder") or "") + " " + (el.get_attribute("aria-label") or "")).lower()
                # indices “code postal”
                if any(k in ph for k in ("postal", "zip")): s += 3
                # Swagbucks
                if "profilernumericinput" in aid: s += 10
                if "profiler" in aid or "profiler" in name: s += 1
                # bonus captcha PureSpectrum
                ngc = (el.get_attribute("ng-change") or "").lower()
                aid = (el.get_attribute("id") or "").lower()
                typ = (el.get_attribute("type") or "").lower()
                if "handlepscaptcha" in ngc: s += 8
                if aid.startswith("ans"): s += 2
                if typ == "textarea": s += 2  # non standard mais vu sur PureSpectrum
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
        lbl  = _norm_txt(text or "")

        # Si le contexte indique clairement le champ, ou si le label ressemble à une valeur date.
        if kind in ("month", "day", "year") or lbl.isdigit():
            # 1) Cible par hint direct (aria-label/placeholder/name/id)
            targets = []
            if kind in ("month","day","year"):
                targets = _find_inputs_by_hint(driver, kind)
            else:
                # si pas de kind explicite, essaie d'inférer selon la longueur
                if len(lbl) == 4:
                    targets = _find_inputs_by_hint(driver, "year")
                    kind = "year"
                elif len(lbl) <= 2:
                    # s'il existe 3 inputs MM/DD/YYYY visibles, essaye d'abord month puis day
                    m = _find_inputs_by_hint(driver, "month")
                    d = _find_inputs_by_hint(driver, "day")
                    y = _find_inputs_by_hint(driver, "year")
                    # Heuristique : si 3 présents, mappe par ordre Month→Day→Year
                    if m and d and y:
                        # on décidera avec le context_hint si présent, sinon month par défaut pour 2 chiffres
                        targets = _find_inputs_by_hint(driver, "month") if kind=="month" or not kind else _find_inputs_by_hint(driver, kind)
                    else:
                        # sinon, essaie month puis day
                        targets = m or d
                        kind = "month" if m else "day"

            # 2) Si trouvé, saisie sécurisée (respect des longueurs)
            if targets:
                el = None
                # prend le premier **visible & enabled**
                for t in targets:
                    try:
                        if t.is_displayed() and t.is_enabled():
                            el = t
                            break
                    except Exception:
                        continue
                    
                if el is not None:
                    # normaliser la valeur
                    raw = "".join(ch for ch in lbl if ch.isdigit())
                    limit = DATE_HINTS.get(kind, {}).get("maxlen", None)
                    if limit:
                        raw = raw[:limit]
                        # pad à gauche pour month/day (ex.: "1" -> "01")
                        if kind in ("month","day") and len(raw)==1:
                            raw = "0"+raw
                    _set_input_value_with_events(driver, el, raw if raw else lbl)
                    return True

    if field is None:
        field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))

    # 🧩 Cas particulier "code postal" / ZIP → activer patch ciblé si possible
    try:
        ctx_lc = (context_hint or "").lower()
        is_zip_ctx = any(k in ctx_lc for k in ("postal", "zip", "code postal"))
        is_swagbucks = False
        try:
            is_swagbucks = bool(driver.find_elements(By.ID, "profilerNumericInput"))
        except Exception:
            pass

        # On normalise la valeur ici (digits-only si le champ est numérique)
        raw_value = re.sub(r"\s+", " ", text).strip()
        digits_only = re.sub(r"\D", "", raw_value)

        if is_zip_ctx or is_swagbucks:
            print(f"[ZIP] ctx='{context_hint}' swag={is_swagbucks} -> trying swagbucks patch" if (is_zip_ctx or is_swagbucks) else "[ZIP] no-zip-context")
            if _swagbucks_zip_patch(driver, digits_only or raw_value):
                return True
    except Exception:
        pass


    # Mise au centre + clic fiable
    try:
        print("Scroll to field")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", field)
    except Exception:
        pass
    try:
        print("Click field")
        field.click()
    except Exception:
        ActionChains(driver).move_to_element(field).click().perform()

    # Nettoyage du champ
    try:
        print("Clear field")
        field.send_keys(Keys.CONTROL, "a")
        field.send_keys(Keys.DELETE)
    except Exception:
        pass

    # Détecter champ numérique et ne garder que les chiffres si besoin
    def is_numeric(el) -> bool:
        t = (el.get_attribute("type") or "").lower()
        im = (el.get_attribute("inputmode") or "").lower()
        pattern = el.get_attribute("pattern") or ""
        return (
            t in ("number", "tel")
            or im in ("numeric", "decimal")
            or bool(re.search(r"\d", pattern))
        )

    value = re.sub(r"\s+", " ", text).strip()
    if is_numeric(field):
        print("[NUM] champ numérique détecté")
        digits = re.sub(r"\D", "", value)
        if digits:
            print("[NUM] champ numérique détecté")
            value = digits

    # Saisie clavier
    try:
        print("Saisie via send_keys()")
        field.send_keys(value)
        print("Saisie via send_keys()2")
    except Exception:
        pass

    # Vérifier
    current = field.get_attribute("value") or field.get_attribute("textContent") or ""
    if current.strip() != value:
        # Tentative B : frappe char-par-char avec ActionChains (plus "humaine")
        try:
            print("Saisie via ActionChains")
            ActionChains(driver).move_to_element(field).click().pause(0.05).perform()
            field.send_keys(Keys.CONTROL, "a")
            field.send_keys(Keys.DELETE)
            for ch in value:
                ActionChains(driver).send_keys(ch).pause(0.06).perform()
                print(f"[NUM] frappe char-par-char")
        except Exception:
            pass

        current = field.get_attribute("value") or field.get_attribute("textContent") or ""

    # [NUM fallback 1] si champ numérique et la valeur "texte" n'a pas pris,
    # on tente la version chiffres seulement (digits-only)
    if is_numeric(field):
        only_digits = re.sub(r"\D", "", value)
        if only_digits and only_digits != current.strip():
            try:
                print("[NUM] resaisie avec chiffres seulement")
                ActionChains(driver).move_to_element(field).click().pause(0.05).perform()
                field.send_keys(Keys.CONTROL, "a"); field.send_keys(Keys.DELETE)
                field.send_keys(only_digits)
                print("[NUM] resaisie avec chiffres seulement2")
            except Exception:
                pass
            # re-lecture
            current = field.get_attribute("value") or field.get_attribute("textContent") or ""

    if current.strip() != value:
        # Tentative C : frappe via CDP (événements clavier natifs)
        try:
            print("Saisie via CDP")
            ActionChains(driver).move_to_element(field).click().pause(0.05).perform()
            field.send_keys(Keys.CONTROL, "a")
            field.send_keys(Keys.DELETE)
            _type_via_cdp(driver, value)
            print("Saisie via CDP2")
        except Exception:
            pass

        current = field.get_attribute("value") or field.get_attribute("textContent") or ""

    if current.strip() != value:
        # Fallback JS + events (React/Angular)
        driver.execute_script(
            """
            const el = arguments[0], v = arguments[1];
            if (el.isContentEditable) {
              el.textContent = v;
            } else {
              el.value = v;
            }
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            """,
            field,
            value,
        )
        # Petit "nudge" pour forcer la MAJ contrôlée
        try:
            print("Petit nudge clavier")
            field.send_keys(" ")
            field.send_keys(Keys.BACK_SPACE)
            print("Petit nudge clavier2")
        except Exception:
            pass

    # [NUM fallback 2] Champ numérique encore récalcitrant → JS générique + blur
    current = field.get_attribute("value") or field.get_attribute("textContent") or ""
    if is_numeric(field) and current.strip() != re.sub(r"\D","", value):
        print("[NUM] champ numérique détecté")
        digits = re.sub(r"\D","", value)
        driver.execute_script("""
            const el = arguments[0], v = arguments[1];
            if (el) {
                el.value = v;
                el.setAttribute("value", v);
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur', {bubbles:true}));
            }
        """, field, digits)
        print("[NUM] patch JS digits-only")
        time.sleep(0.3)

    # Re-lecture finale
    current = field.get_attribute("value") or field.get_attribute("textContent") or ""
    
    # Dernier filet (numérique) : setter natif + évènements
    if is_numeric(field) and (current.strip() != re.sub(r"\D","", value)):
        try:
            _react_set_value_and_fire(driver, field, re.sub(r"\D", "", value))
            time.sleep(0.15)
            current = field.get_attribute("value") or field.get_attribute("textContent") or ""
        except Exception:
            pass

    # PATCH spécifique Swagbucks : champ postal profilerNumericInput
    if current.strip() != value:
        try:
            print("[SWAG] tentative patch JS spécifique Swagbucks")
            special = driver.find_element(By.ID, "profilerNumericInput")
            driver.execute_script("""
                const el = arguments[0], v = arguments[1];
                if (el) {
                    el.value = v;
                    el.setAttribute("value", v);
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                    el.dispatchEvent(new Event('blur', {bubbles:true}));
                }
            """, special, value)
            time.sleep(0.3)
            current = special.get_attribute("value") or ""
            if current.strip() == value:
                print("✅ Champ postal Swagbucks rempli via patch JS direct.")
                return True
        except Exception:
            pass

    return current.strip() == value

def _normalize_lbl(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u00a0", " ")  # espace insécable
    s = unicodedata.normalize("NFKC", s).strip().lower()
    # enlever flèches/guillemets/ponctuation décorative courante
    s = re.sub(r"[»«“”\"'›→·•:]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s

def click_button_by_text(driver, text):
    target = _normalize_lbl(text)
    print(f"Label normalisé: '{target}'; source: input_handler.py")

    # 1) Candidats “boutons” sûrs (jamais des <a>)
    candidates = []
    candidates += driver.find_elements(By.TAG_NAME, "button")
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "input[type='submit'], input[type='button']"
    )
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "div[role='button'], span[role='button']"
    )

    # ➕ [PATCH] Inclure les <a> qui ressemblent à des boutons/CTA
    anchor_ctas = []
    # classes usuelles de boutons
    anchor_ctas += driver.find_elements(
        By.CSS_SELECTOR, "a.btn, a.button, a.btn-primary, a.primary, a.cta"
    )
    anchor_ctas += driver.find_elements(
        By.CSS_SELECTOR, "a[class*='btn'], a[class*='button'], a[class*='cta']"
    )
    # conteneur #btn (vu sur ta page)
    anchor_ctas += driver.find_elements(By.CSS_SELECTOR, "#btn a")

    def _is_blacklisted_anchor(a):
        lbl = _normalize_lbl(
            (
                a.get_attribute("innerText")
                or a.text
                or a.get_attribute("aria-label")
                or ""
            )
        )
        href = (a.get_attribute("href") or "").lower()
        bad = (
            "privacy",
            "policy",
            "confidentialit",
            "cookies",
            "terms",
            "conditions",
            "vie privée",
            "legal",
        )
        # si libellé contient ces mots, on écarte
        if any(b in lbl for b in bad):
            return True
        # si href mène clairement vers CGU/Privacy, on écarte
        return any(b in href for b in bad)

    for a in anchor_ctas:
        try:
            if not _is_blacklisted_anchor(a):
                candidates.append(a)
        except Exception:
            continue

    # 2) On n’ajoute des <a> que s’ils se comportent comme des boutons
    #    (pas de navigation réelle)
    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            role = (a.get_attribute("role") or "").lower()
            href = (a.get_attribute("href") or "").strip().lower()
            # critères d’acceptation
            looks_like_button = (
                role == "button" or href in ("", "#") or href.startswith("javascript:")
            )
            # blacklist évidente (on évite toute confusion “politique de confidentialité”, etc.)
            blacklist = (
                "privacy",
                "policy",
                "cookies",
                "confidentialit",
                "terms",
                "política",
                "bedingungen",
            )
            if looks_like_button and not any(bad in href for bad in blacklist):
                candidates.append(a)
        except Exception:
            continue

    for el in candidates:
        try:
            lbl = el.get_attribute("value") or el.text
            if not lbl:
                spans = el.find_elements(By.TAG_NAME, "span")
                for sp in spans:
                    if sp.text and sp.text.strip():
                        lbl = sp.text
                        break
            if not lbl:
                continue

            if (
                _normalize_lbl(lbl).find(target) != -1
                or target.find(_normalize_lbl(lbl)) != -1
            ):
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el
                )
                time.sleep(0.1)
                try:
                    el.click()
                except Exception:
                    try:
                        ActionChains(driver).move_to_element(el).click().perform()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                time.sleep(0.8)
                return True
        except Exception:
            continue

    # --- Fallback 1 : XPath large, insensible à la casse/ponctuation ---
    try:
        # Cherche directement des éléments dont le texte *contient* le target (après nettoyage côté Python)
        xpath = (
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{t}')] | "
            "//*[self::div or self::span][@role='button'][contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{t}')] | "
            "//input[(translate(@value,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{t}') and (@type='submit' or @type='button')] | "
            "//a[(contains(@class,'btn') or contains(@class,'button') or contains(@class,'cta')) "
            " and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{t}')]"
        ).format(t=target)

        elems = driver.find_elements(By.XPATH, xpath)
        for el in elems:
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el
                )
                time.sleep(0.1)
                el.click()
                time.sleep(0.6)
                return True
            except Exception:
                try:
                    ActionChains(driver).move_to_element(el).click().perform()
                    time.sleep(0.6)
                    return True
                except Exception:
                    continue
    except Exception:
        pass

    # --- Fallback 2 : JS sur tous les boutons visibles (match JS includes) ---
    try:
        js = """
        const norm = s => (s||'').toLowerCase()
            .replaceAll('\\u00A0',' ')
            .replace(/[»«“”"'›→!?.:]/g,'')
            .replace(/\\s+/g,' ')
            .trim();
        const target = arguments[0];
        const candidates = Array.from(document.querySelectorAll(
          'button, input[type=submit], input[type=button], [role=button]'
        ));
        for (const el of candidates) {
          const label = (el.value || el.innerText || '').trim();
          if (!label) continue;
          if (norm(label).includes(target)) {
            el.scrollIntoView({block:'center'});
            el.click();
            return true;
          }
        }
        return false;
        """
        if driver.execute_script(js, target):
            time.sleep(0.6)
            return True
    except Exception:
        pass

    if _looks_like_nav_label(text):
        try:
            if click_primary_cta(driver):
                print("✅ CTA principal cliqué (fallback nav). source: input_handler.py")
                return True
        except Exception:
            pass

    print(f"❌ Aucun élément cliquable trouvé (après normalisation) pour : {text} source: input_handler.py")
    return False

def apply_ai_response(driver, response):
    print("run: apply_ai_response")
    """
    Essaye d'appliquer dynamiquement la réponse de l'assistant IA
    à tous les types d'inputs (texte, bouton, checkbox...).
    ⚠️ NEW: si 'response' ressemble à un CTA, on NE TOUCHE PAS aux checkboxes.
    """
    # 0) Si ça ressemble à un CTA, on laisse les stratégies bouton gérer.
    if _looks_like_nav_label(response):  # NEW
        # On tente juste du texte (rare) puis bouton; jamais checkbox
        try:
            input_fields = driver.find_elements(
                By.CSS_SELECTOR, "input[type='text'], textarea"
            )
            for field in input_fields:
                try:
                    field.clear()
                    field.send_keys(response)
                    time.sleep(1)
                    print(
                        f"✍️ Réponse texte insérée (CTA-like ignoré côté checkbox) : {response}"
                    )
                    return True
                except:
                    continue
        except Exception as e:
            print(f"❌ Erreur saisie texte (CTA-like) : {e} source: input_handler.py")

        # Bouton par texte (au cas où)
        try:
            if click_button_by_text(driver, response):
                return True
        except Exception as e:
            print(f"❌ Erreur clic bouton (CTA-like) : {e} source: input_handler.py")

        # Ne pas toucher aux checkboxes ici
        return False  # NEW

    # 1. Essayer comme champ texte
    try:
        input_fields = driver.find_elements(
            By.CSS_SELECTOR, "input[type='text'], textarea"
        )
        for field in input_fields:
            try:
                field.clear()
                field.send_keys(response)
                time.sleep(1)
                print(f"✍️ Réponse texte insérée : {response}")
                return True
            except:
                continue
    except Exception as e:
        print(f"❌ Erreur saisie texte : {e} source: input_handler.py")

    # 2. Essayer comme bouton ou élément cliquable
    try:
        if handle_generic_input(driver, response):
            return True
    except Exception as e:
        print(f"❌ Erreur generic_input : {e}: source: input_handler.py")

    try:
        if click_button_by_text(driver, response):
            return True
    except Exception as e:
        print(f"❌ Erreur clic bouton : {e} source: input_handler.py")

    # 3. Essayer comme checkbox (CTA déjà filtré au début)
    try:
        if click_checkbox_by_label(driver, response):
            return True
    except Exception as e:
        print(f"❌ Erreur clic checkbox : {e} source: input_handler.py")

    print(
        f"❌ Aucune méthode n’a fonctionné pour : {response} source: input_handler.py"
    )
    return False

def _is_visible(driver, el):
    try:
        if not el.is_displayed():
            return False
        box = el.rect
        return box and box.get("width", 0) > 5 and box.get("height", 0) > 5
    except Exception:
        return False

def click_icon_like_button(driver, hints=None):
    """
    Tente de cliquer un bouton sans texte (icône flèche).
    On matche aria-label/title/classes/sous-éléments <svg>/<i>.
    """

    hints = [
        h.lower()
        for h in (
            hints or ["flèche", "suivant", "continuer", "next", "continue", "start"]
        )
    ]
    candidates = []

    # 1) Boutons classiques
    candidates += driver.find_elements(By.TAG_NAME, "button")
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "input[type='submit'], input[type='button']"
    )
    candidates += driver.find_elements(By.CSS_SELECTOR, "[role='button']")

    # 2) <a> qui se comportent comme des boutons (pas de navigation réelle)
    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            role = (a.get_attribute("role") or "").lower()
            href = (a.get_attribute("href") or "").strip().lower()
            looks_like_button = (
                role == "button" or href in ("", "#") or href.startswith("javascript:")
            )
            if looks_like_button:
                candidates.append(a)
        except Exception:
            continue

    def score(el):
        sc = 0
        aria = (el.get_attribute("aria-label") or "").lower()
        title = (el.get_attribute("title") or "").lower()
        cls = (el.get_attribute("class") or "").lower()

        # indices textuels
        for h in hints:
            if h in aria or h in title or h in cls:
                sc += 3

        # icônes internes
        try:
            has_svg = bool(el.find_elements(By.TAG_NAME, "svg"))
            has_i = any(
                k in (i.get_attribute("class") or "").lower()
                for i in el.find_elements(By.TAG_NAME, "i")
                for k in ("arrow", "chevron", "next", "right")
            )
            if has_svg or has_i:
                sc += 2
        except Exception:
            pass

        # taille (CTA)
        try:
            r = el.rect
            sc += min(int((r.get("width", 0) * r.get("height", 0)) / 3000), 5)
        except Exception:
            pass

        return sc

    # Filtre visible + tri par score décroissant
    filtered = [el for el in candidates if _is_visible(driver, el)]
    filtered.sort(key=score, reverse=True)

    for el in filtered:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.1)
            try:
                el.click()
            except Exception:
                ActionChains(driver).move_to_element(el).click().perform()
            time.sleep(0.6)
            print("✅ Bouton icône cliqué (heuristique). source: input_handler.py")
            return True
        except Exception:
            continue

    print("❌ Aucun bouton-icône pertinent trouvé. source: input_handler.py")
    return False

def click_primary_cta(driver):
    """
    Clique le CTA principal lorsque le bouton n'a pas de texte.
    Heuristique: plus grand bouton visible et proche du centre de l'écran.
    """
    def center_score(el, vw, vh):
        try:
            r = el.rect
            cx = r["x"] + r["width"] / 2
            cy = r["y"] + r["height"] / 2
            dx = abs(cx - vw / 2)
            dy = abs(cy - vh / 2)
            return -(dx + dy)  # plus proche du centre ➜ score plus haut
        except Exception:
            return -1e9

    # Candidats: jamais de liens purs (sauf role=button)
    candidates = []
    candidates += driver.find_elements(By.TAG_NAME, "button")
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "input[type='submit'], input[type='button']"
    )
    candidates += driver.find_elements(By.CSS_SELECTOR, "[role='button']")

    # Ajoute <a role=button> seulement
    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            if (a.get_attribute("role") or "").lower() == "button":
                candidates.append(a)
        except Exception:
            continue

    # Filtre visible + dimensions
    visibles = [el for el in candidates if _is_visible(driver, el)]

    if not visibles:
        print("❌ Aucun CTA visible. source: input_handler.py")
        return False

    # Viewport
    vw = driver.execute_script("return window.innerWidth") or 1200
    vh = driver.execute_script("return window.innerHeight") or 800

    # Score composité: aire + proximité centre
    def score(el):
        try:
            r = el.rect
            area = r["width"] * r["height"]
            return area + 2000 + center_score(el, vw, vh)  # aire + centre
        except Exception:
            return 0

    visibles.sort(key=score, reverse=True)

    for el in visibles:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.1)
            try:
                el.click()
            except Exception:
                ActionChains(driver).move_to_element(el).click().perform()
            time.sleep(0.6)
            print("✅ CTA principal cliqué. source: input_handler.py")
            return True
        except Exception:
            continue

    print("❌ Impossible de cliquer le CTA principal. source: input_handler.py")
    return False

def _iter_iframes_safe(driver):
    """Retourne la liste des iframes visibles et probablement interactives."""
    frames = []
    for fr in driver.find_elements(By.TAG_NAME, "iframe"):
        try:
            r = fr.rect
            if fr.is_displayed() and r.get("width", 0) > 20 and r.get("height", 0) > 20:
                frames.append(fr)
        except Exception:
            continue
    return frames

def _in_each_frame_recursive(driver, fn_try, depth=2):
    """
    Appelle fn_try(driver) dans le contexte courant.
    Si échec, essaye récursivement dans chaque iframe (profondeur limitée).
    Reviens toujours au default_content() après chaque descente.
    """
    if depth < 0:
        return False

    # 1) Essai dans le contexte courant
    try:
        if fn_try(driver):
            return True
    except Exception:
        pass

    # 2) Descente dans les iframes si non trouvé
    frames = _iter_iframes_safe(driver)
    for fr in frames:
        try:
            driver.switch_to.frame(fr)
            if _in_each_frame_recursive(driver, fn_try, depth - 1):
                # on remonte puis on annonce succès
                driver.switch_to.default_content()
                return True
            driver.switch_to.default_content()
        except Exception:
            # en cas d’erreur, on remonte quoi qu’il arrive
            try:
                driver.switch_to.default_content()
            except:
                pass
            continue

    return False

def click_button_by_text_any_context(driver, text, depth=2):
    """
    Tente de cliquer un bouton par texte dans le DOM courant et,
    en cas d’échec, dans les iframes (jusqu’à 'depth' niveaux).
    Multi‑méthodes (utilise click_button_by_text à chaque niveau).
    """

    def _try_here(drv):
        return click_button_by_text(drv, text)

    return _in_each_frame_recursive(driver, _try_here, depth=depth)

def click_icon_like_button_any_context(driver, hints=None, depth=2):
    """
    Même logique mais pour les boutons sans texte (icône/flèche).
    """

    def _try_here(drv):
        return click_icon_like_button(drv, hints=hints)

    return _in_each_frame_recursive(driver, _try_here, depth=depth)

def click_primary_cta_any_context(driver, depth=2):
    """
    Clique le CTA principal, en testant aussi à travers les iframes.
    """

    def _try_here(drv):
        return click_primary_cta(drv)

    return _in_each_frame_recursive(driver, _try_here, depth=depth)

def _norm_btn_text(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip().lower()
    # enlève flèches / décorations fréquentes
    s = s.replace("→", " ").replace("»", " ").replace(">", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def try_click_navigation_cta(driver) -> bool:
    """
    Cherche un CTA de navigation (Continue/Suivant/Next/Valider…)
    et clique le meilleur candidat visible.
    """
    candidates = []

    # buttons
    for el in driver.find_elements(By.XPATH, "//button|//a[@role='button']|//input[@type='submit' or @type='button']"):
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue
            txt = el.text or el.get_attribute("value") or el.get_attribute("aria-label") or ""
            t = _norm_btn_text(txt)
            if not t:
                continue

            # exclure liens “learn more” / privacy etc
            if any(x in t for x in ["learn more", "privacy", "terms", "cookies"]):
                continue

            bad = ("refuser", "disagree", "quitter", "quit", "exit", "annuler", "cancel", "fermer", "close")
            if any(b in t for b in bad):
                continue

            score = 0
            if any(x in t for x in ["continue", "continuer", "next", "suivant", "proceed"]):
                score += 50
            if any(x in t for x in ["valider", "submit", "envoyer", "terminer", "send", "start", "commencer"]):
                score += 30

            cls = (el.get_attribute("class") or "").lower()
            if "primary" in cls:
                score += 10
            if "btn" in cls:
                score += 5

            candidates.append((score, el))
        except Exception:
            continue

    if not candidates:
        return False

    candidates.sort(key=lambda x: x[0], reverse=True)

    # ✅ Essayer plusieurs candidats (pas seulement le "best")
    for score, el in candidates[:6]:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            continue

    return False

def try_click_navigation_cta_any_context(driver, depth=2) -> bool:
    """
    Même CTA nav, mais tente aussi à travers les iframes.
    Indispensable à l’échelle (100 bots) car les providers varient beaucoup.
    """
    def _try_here(drv):
        return try_click_navigation_cta(drv)
    return _in_each_frame_recursive(driver, _try_here, depth=depth)

def click_cta_strong_any_context(driver, text=None, label_hint=None, depth: int = 2, **_kwargs) -> bool:
    """
    Clique un CTA (Suivant / Continuer / Next / Continue / Start...) en scannant
    default_content + iframes (Decipher/Confirmit).
    Cette définition est volontairement à la FIN du fichier pour écraser toute version dupliquée.
    """
    import re
    import unicodedata
    from selenium.webdriver.common.by import By
    from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain

    raw = text if text is not None else (label_hint or "")
    raw = (raw or "").strip()
    if not raw:
        return False

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s or "").replace("\u00A0", " ").lower()
        s = re.sub(r"[»«“”'\"›→·•:]+", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    needle = norm(raw)
    if not needle:
        return False

    bad = ["exit", "quit", "refuse", "do not agree", "disagree", "je ne suis pas d'accord", "pas d'accord"]
    good_fallback = ["suivant", "continuer", "next", "continue", "proceed", "start", "begin", "accept", "agree"]

    def is_bad(t: str) -> bool:
        return any(b in t for b in bad)

    def is_match(t: str) -> bool:
        if not t:
            return False
        if is_bad(t):
            return False
        # match direct ou fallback si on nous donne "suivant" mais le bouton est "suivant »"
        if needle in t or t in needle:
            return True
        # si raw est très court, autoriser un match via listes standards
        if len(needle) <= 5:
            return any(w in t for w in good_fallback)
        return False

    css = "button, input[type='submit'], input[type='button'], a, [role='button']"

    for chain in iter_frame_chains(driver, max_depth=depth):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue

            try:
                els = driver.find_elements(By.CSS_SELECTOR, css)
            except Exception:
                els = []

            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    t = norm((el.text or "") or (el.get_attribute("value") or "") or (el.get_attribute("aria-label") or ""))
                    if not is_match(t):
                        continue

                    # enabled ?
                    try:
                        if el.get_attribute("aria-disabled") == "true":
                            continue
                        if el.get_attribute("disabled") is not None:
                            continue
                    except Exception:
                        pass

                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    except Exception:
                        pass

                    # click robuste (JS)
                    try:
                        driver.execute_script("arguments[0].click();", el)
                    except Exception:
                        try:
                            el.click()
                        except Exception:
                            continue

                    try:
                        setattr(driver, "last_action_success", True)
                    except Exception:
                        pass
                    return True

                except Exception:
                    continue

    return False
