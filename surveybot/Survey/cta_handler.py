"""
cta_handler.py - Gestion des CTA (Call To Action) et boutons de navigation

Ce module contient:
- click_button_by_text: clic sur bouton par texte
- click_icon_like_button: clic sur bouton icône (sans texte)
- click_primary_cta: clic sur le CTA principal
- try_click_navigation_cta: recherche et clic CTA navigation
- Variantes *_any_context: recherche dans les iframes
- click_cta_strong_any_context: version robuste multi-frame

Dépendances:
- input_utils pour les fonctions utilitaires
- frame_utils pour la navigation iframe
"""

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
import unicodedata
import re
import time


# =============================================================================
# CONSTANTES CTA
# =============================================================================

CTA_SYNONYMS = {
    "continuer", "suivant", "start", "commencer", "démarrer",
    "accepter", "accepter et commencer", "next", "continue",
    "submit", "soumettre", "valider", "proceed", "begin",
    "envoyer", "terminer", "send",
}


# =============================================================================
# HELPERS CTA
# =============================================================================

def _normalize_lbl(s: str) -> str:
    """Normalise un label de bouton pour comparaison."""
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r"[»«""\"'›→·•:]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_btn_text(s: str) -> str:
    """Normalise le texte d'un bouton."""
    s = re.sub(r"\s+", " ", (s or "")).strip().lower()
    s = s.replace("→", " ").replace("»", " ").replace(">", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def looks_like_nav_label(s: str) -> bool:
    """
    Détermine si un texte ressemble à un label de navigation (CTA).
    """
    if not s:
        return False
    s = s.lower().strip()
    nav_kw = {
        "continuer", "suivant", "start", "commencer", "démarrer",
        "accepter", "accepter et commencer", "next", "continue",
        "submit", "soumettre", "valider",
    }
    return any(k in s for k in nav_kw)


def _is_visible(driver, el) -> bool:
    """Vérifie si un élément est visible et a une taille suffisante."""
    try:
        if not el.is_displayed():
            return False
        box = el.rect
        return box and box.get("width", 0) > 5 and box.get("height", 0) > 5
    except Exception:
        return False


# =============================================================================
# IFRAME HELPERS
# =============================================================================

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
                driver.switch_to.default_content()
                return True
            driver.switch_to.default_content()
        except Exception:
            try:
                driver.switch_to.default_content()
            except:
                pass
            continue

    return False


# =============================================================================
# CLICK_BUTTON_BY_TEXT
# =============================================================================

def click_button_by_text(driver, text) -> bool:
    """
    Clique un bouton par son texte visible.
    
    Stratégie multi-niveaux:
    1) Collecte candidats (buttons, inputs, role=button, anchors CTA)
    2) Match par texte normalisé
    3) Fallback XPath large
    4) Fallback JS sur tous les boutons visibles
    5) Si texte ressemble à nav, fallback click_primary_cta
    
    Args:
        driver: WebDriver
        text: texte du bouton à cliquer
    
    Returns:
        True si bouton cliqué avec succès
    """
    target = _normalize_lbl(text)
    print(f"Label normalisé: '{target}'; source: cta_handler.py")

    # 1) Candidats "boutons" sûrs
    candidates = []
    candidates += driver.find_elements(By.TAG_NAME, "button")
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "input[type='submit'], input[type='button']"
    )
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "div[role='button'], span[role='button']"
    )

    # Inclure les <a> qui ressemblent à des boutons/CTA
    anchor_ctas = []
    anchor_ctas += driver.find_elements(
        By.CSS_SELECTOR, "a.btn, a.button, a.btn-primary, a.primary, a.cta"
    )
    anchor_ctas += driver.find_elements(
        By.CSS_SELECTOR, "a[class*='btn'], a[class*='button'], a[class*='cta']"
    )
    anchor_ctas += driver.find_elements(By.CSS_SELECTOR, "#btn a")

    def _is_blacklisted_anchor(a):
        lbl = _normalize_lbl(
            (a.get_attribute("innerText") or a.text or a.get_attribute("aria-label") or "")
        )
        href = (a.get_attribute("href") or "").lower()
        bad = ("privacy", "policy", "confidentialit", "cookies", "terms", "conditions", "vie privée", "legal")
        if any(b in lbl for b in bad):
            return True
        return any(b in href for b in bad)

    for a in anchor_ctas:
        try:
            if not _is_blacklisted_anchor(a):
                candidates.append(a)
        except Exception:
            continue

    # 2) Ajouter des <a> qui se comportent comme des boutons
    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            role = (a.get_attribute("role") or "").lower()
            href = (a.get_attribute("href") or "").strip().lower()
            looks_like_button = (
                role == "button" or href in ("", "#") or href.startswith("javascript:")
            )
            blacklist = ("privacy", "policy", "cookies", "confidentialit", "terms", "política", "bedingungen")
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

    # Fallback 1: XPath large
    try:
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

    # Fallback 2: JS sur tous les boutons visibles
    try:
        js = """
        const norm = s => (s||'').toLowerCase()
            .replaceAll('\\u00A0',' ')
            .replace(/[»«""\"'›→·•:]/g,'')
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

    if looks_like_nav_label(text):
        try:
            if click_primary_cta(driver):
                print("✓ CTA principal cliqué (fallback nav). source: cta_handler.py")
                return True
        except Exception:
            pass

    print(f"✗ Aucun élément cliquable trouvé (après normalisation) pour : {text} source: cta_handler.py")
    return False


# =============================================================================
# CLICK_ICON_LIKE_BUTTON
# =============================================================================

def click_icon_like_button(driver, hints=None) -> bool:
    """
    Tente de cliquer un bouton sans texte (icône flèche).
    On matche aria-label/title/classes/sous-éléments <svg>/<i>.
    
    Args:
        driver: WebDriver
        hints: liste de mots-clés à rechercher
    
    Returns:
        True si bouton cliqué
    """
    hints = [
        h.lower()
        for h in (hints or ["flèche", "suivant", "continuer", "next", "continue", "start"])
    ]
    candidates = []

    # 1) Boutons classiques
    candidates += driver.find_elements(By.TAG_NAME, "button")
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "input[type='submit'], input[type='button']"
    )
    candidates += driver.find_elements(By.CSS_SELECTOR, "[role='button']")

    # 2) <a> qui se comportent comme des boutons
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

        for h in hints:
            if h in aria or h in title or h in cls:
                sc += 3

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

        try:
            r = el.rect
            sc += min(int((r.get("width", 0) * r.get("height", 0)) / 3000), 5)
        except Exception:
            pass

        return sc

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
            print("✓ Bouton icône cliqué (heuristique). source: cta_handler.py")
            return True
        except Exception:
            continue

    print("✗ Aucun bouton-icône pertinent trouvé. source: cta_handler.py")
    return False


# =============================================================================
# CLICK_PRIMARY_CTA
# =============================================================================

def click_primary_cta(driver) -> bool:
    """
    Clique le CTA principal lorsque le bouton n'a pas de texte.
    Heuristique: plus grand bouton visible et proche du centre de l'écran.
    
    Returns:
        True si CTA cliqué
    """
    def center_score(el, vw, vh):
        try:
            r = el.rect
            cx = r["x"] + r["width"] / 2
            cy = r["y"] + r["height"] / 2
            dx = abs(cx - vw / 2)
            dy = abs(cy - vh / 2)
            return -(dx + dy)
        except Exception:
            return -1e9

    candidates = []
    candidates += driver.find_elements(By.TAG_NAME, "button")
    candidates += driver.find_elements(
        By.CSS_SELECTOR, "input[type='submit'], input[type='button']"
    )
    candidates += driver.find_elements(By.CSS_SELECTOR, "[role='button']")

    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            if (a.get_attribute("role") or "").lower() == "button":
                candidates.append(a)
        except Exception:
            continue

    visibles = [el for el in candidates if _is_visible(driver, el)]

    if not visibles:
        print("✗ Aucun CTA visible. source: cta_handler.py")
        return False

    vw = driver.execute_script("return window.innerWidth") or 1200
    vh = driver.execute_script("return window.innerHeight") or 800

    def score(el):
        try:
            r = el.rect
            area = r["width"] * r["height"]
            return area + 2000 + center_score(el, vw, vh)
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
            print("✓ CTA principal cliqué. source: cta_handler.py")
            return True
        except Exception:
            continue

    print("✗ Impossible de cliquer le CTA principal. source: cta_handler.py")
    return False


# =============================================================================
# TRY_CLICK_NAVIGATION_CTA
# =============================================================================

def try_click_navigation_cta(driver) -> bool:
    """
    Cherche un CTA de navigation (Continue/Suivant/Next/Valider…)
    et clique le meilleur candidat visible.
    
    Supporte:
    - AreYouNet (#btn_next, EnqueteDef_submit)
    - Decipher (#btn_continue)
    - RSCH (#btnsmall, .enterButton.submitButton)
    - Boutons génériques avec scoring
    
    Returns:
        True si CTA navigation cliqué
    """
    # --- AreYouNet / runet : CTA image sans texte ---
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "#btn_next")
        if btns:
            el = btns[0]
            try:
                a = el.find_element(By.XPATH, "ancestor::a[1]")
                if a:
                    el = a
            except Exception:
                pass

            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            print("[CTA_NAV] AreYouNet: clicked #btn_next")
            return True
    except Exception:
        pass

    # Variante AreYouNet: lien direct vers EnqueteDef_submit()
    try:
        links = driver.find_elements(By.CSS_SELECTOR, "a[href*='EnqueteDef_submit']")
        if links:
            el = links[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            print("[CTA_NAV] AreYouNet: clicked EnqueteDef_submit link")
            return True
    except Exception:
        pass

    # --- Decipher : CTA avec value symbolique (">>" etc.) ---
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "#btn_continue")
        if btns:
            el = btns[0]
            if el.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                print("[CTA_NAV] Decipher: clicked #btn_continue")
                return True
    except Exception:
        pass

    # --- RSCH / Survey japonais ---
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "#btnsmall")
        if not btns:
            btns = driver.find_elements(By.CSS_SELECTOR, "input.enterButton.submitButton, button.enterButton.submitButton")
        if btns:
            el = btns[0]
            if el.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                print("[CTA_NAV] RSCH: clicked #btnsmall or .enterButton.submitButton")
                return True
    except Exception:
        pass

    candidates = []

    nav_xpath = (
        "//button"
        "|//input[@type='submit' or @type='button']"
        "|//a[@role='button']"
        "|//a[contains(concat(' ', normalize-space(@class), ' '), ' btn ')]"
    )

    for el in driver.find_elements(By.XPATH, nav_xpath):
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue

            if (el.get_attribute("aria-disabled") or "").lower() == "true":
                continue
            
            cls = (el.get_attribute("class") or "").lower()
            cls_tokens = cls.split()
            disabled_patterns = ("disabled", "btn-disabled", "is-disabled", "button--disabled", "btn--disabled")
            if any(tok in disabled_patterns for tok in cls_tokens):
                continue

            txt = el.text or el.get_attribute("value") or el.get_attribute("aria-label") or ""
            t = _norm_btn_text(txt)
            if not t:
                continue

            bad = ("refuser", "disagree", "quitter", "quit", "exit", "annuler", "cancel", "fermer", "close", "retour", "précédent", "precedent", "previous", "back")
            if any(b in t for b in bad):
                continue

            score = 0
            if any(x in t for x in ["continue", "continuer", "next", "suivant", "proceed"]):
                score += 50
            if any(x in t for x in ["valider", "submit", "envoyer", "terminer", "send", "start", "commencer", "démarrer"]):
                score += 30

            el_id = (el.get_attribute("id") or "").lower()
            if el_id == "submitquestion":
                score += 120
            elif any(k in el_id for k in ["submit", "next", "continue"]):
                score += 60

            try:
                if el.find_elements(By.XPATH, "ancestor::form[1]"):
                    score += 10
            except Exception:
                pass

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


# =============================================================================
# WRAPPERS *_ANY_CONTEXT (recherche dans les iframes)
# =============================================================================

def click_button_by_text_any_context(driver, text, depth=2) -> bool:
    """
    Tente de cliquer un bouton par texte dans le DOM courant et,
    en cas d'échec, dans les iframes (jusqu'à 'depth' niveaux).
    """
    def _try_here(drv):
        return click_button_by_text(drv, text)
    return _in_each_frame_recursive(driver, _try_here, depth=depth)


def click_icon_like_button_any_context(driver, hints=None, depth=2) -> bool:
    """
    Même logique mais pour les boutons sans texte (icône/flèche).
    """
    def _try_here(drv):
        return click_icon_like_button(drv, hints=hints)
    return _in_each_frame_recursive(driver, _try_here, depth=depth)


def click_primary_cta_any_context(driver, depth=2) -> bool:
    """
    Clique le CTA principal, en testant aussi à travers les iframes.
    """
    def _try_here(drv):
        return click_primary_cta(drv)
    return _in_each_frame_recursive(driver, _try_here, depth=depth)


def try_click_navigation_cta_any_context(driver, depth=2) -> bool:
    """
    Même CTA nav, mais tente aussi à travers les iframes.
    """
    def _try_here(drv):
        return try_click_navigation_cta(drv)
    return _in_each_frame_recursive(driver, _try_here, depth=depth)


# =============================================================================
# CLICK_CTA_STRONG_ANY_CONTEXT (version robuste multi-frame)
# =============================================================================

def click_cta_strong_any_context(driver, text=None, label_hint=None, depth: int = 2, **_kwargs) -> bool:
    """
    Clique un CTA (Suivant / Continuer / Next / Continue / Start...) en scannant
    default_content + iframes (Decipher/Confirmit).
    
    Args:
        driver: WebDriver
        text: texte explicite du CTA
        label_hint: alias pour text
        depth: profondeur maximale d'exploration des iframes
    
    Returns:
        True si CTA cliqué
    """
    # Import dynamique pour éviter dépendances circulaires
    try:
        from frame_utils import iter_frame_chains, switch_to_frame_chain
    except ImportError:
        try:
            from Survey.frame_utils import iter_frame_chains, switch_to_frame_chain
        except ImportError:
            # Fallback sans frame_utils
            return try_click_navigation_cta_any_context(driver, depth=depth)

    raw = text if text is not None else (label_hint or "")
    raw = (raw or "").strip()
    if not raw:
        return False

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s or "").replace("\u00A0", " ").lower()
        s = re.sub(r"[»«""\"›→·•:]+", "", s)
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
        if needle in t or t in needle:
            return True
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
                    
                    raw_val = (el.text or "") or (el.get_attribute("value") or "")
                    t = norm(raw_val)
                    if not t or not any(c.isalpha() for c in t):
                        t = norm(el.get_attribute("aria-label") or "")
                    if not is_match(t):
                        continue

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