"""
input_frame.py - Gestion des iframes pour input_handler

Ce module contient:
- Helpers d'itération sur les iframes
- Wrappers pour exécuter des actions dans plusieurs contextes de frames
- Fonctions de navigation CTA cross-frame

Dépendances:
- input_utils pour les fonctions utilitaires
- frame_utils pour iter_frame_chains et switch_to_frame_chain
"""






import unicodedata
import re

# =============================================================================
# HELPERS IFRAME BASIQUES
# =============================================================================

def iter_iframes_safe(driver):
    """
    Retourne la liste des iframes visibles et probablement interactives.
    Filtre les iframes trop petites (< 20x20 pixels).
    """
    frames = []
    for fr in driver.query_selector_all("iframe"):
        try:
            r = fr.bounding_box() or {}
            if fr.is_visible() and r.get("width", 0) > 20 and r.get("height", 0) > 20:
                frames.append(fr)
        except Exception:
            continue
    return frames


def in_each_frame_recursive(driver, fn_try, depth=2):
    """
    Appelle fn_try(driver) dans le contexte courant puis dans chaque iframe
    (profondeur limitée). Utilise iter_frame_chains + switch_to_frame_chain
    pour naviguer inter-frames sans API Selenium.

    Args:
        driver: Page Playwright ou shim
        fn_try: fonction callback(driver) -> bool
        depth: profondeur maximale de frames à explorer

    Returns:
        True si fn_try a réussi dans n'importe quel contexte
    """
    from frame_utils import iter_frame_chains, switch_to_frame_chain

    for chain in iter_frame_chains(driver, max_depth=depth):
        with switch_to_frame_chain(driver, chain) as ok:
            if not ok:
                continue
            try:
                if fn_try(driver):
                    return True
            except Exception:
                continue

    return False


# =============================================================================
# WRAPPERS CROSS-FRAME POUR FONCTIONS EXISTANTES
# =============================================================================

def click_button_by_text_any_context(driver, text, depth=2):
    """
    Tente de cliquer un bouton par texte dans le DOM courant et,
    en cas d'échec, dans les iframes (jusqu'à 'depth' niveaux).
    
    Note: Requiert click_button_by_text défini ailleurs (importé dynamiquement
    pour éviter les imports circulaires).
    """
    # Import dynamique pour éviter circular import
    from input_handler import click_button_by_text

    def _try_here(drv):
        return click_button_by_text(drv, text)

    return in_each_frame_recursive(driver, _try_here, depth=depth)


def click_icon_like_button_any_context(driver, hints=None, depth=2):
    """
    Même logique mais pour les boutons sans texte (icône/flèche).
    """
    from input_handler import click_icon_like_button

    def _try_here(drv):
        return click_icon_like_button(drv, hints=hints)

    return in_each_frame_recursive(driver, _try_here, depth=depth)


def click_primary_cta_any_context(driver, depth=2):
    """
    Clique le CTA principal, en testant aussi à travers les iframes.
    """
    from input_handler import click_primary_cta

    def _try_here(drv):
        return click_primary_cta(drv)

    return in_each_frame_recursive(driver, _try_here, depth=depth)


def try_click_navigation_cta_any_context(driver, depth=2) -> bool:
    """
    Même CTA nav, mais tente aussi à travers les iframes.
    Indispensable à l'échelle (100 bots) car les providers varient beaucoup.
    """
    from input_handler import try_click_navigation_cta

    def _try_here(drv):
        return try_click_navigation_cta(drv)

    return in_each_frame_recursive(driver, _try_here, depth=depth)


# =============================================================================
# CTA NAVIGATION CROSS-FRAME AVEC FRAME_UTILS
# =============================================================================

def click_cta_strong_any_context(driver, text=None, label_hint=None, depth: int = 2, **_kwargs) -> bool:
    """
    Clique un CTA (Suivant / Continuer / Next / Continue / Start...) en scannant
    default_content + iframes (Decipher/Confirmit).
    
    Utilise frame_utils pour itération robuste sur les frames.
    
    Args:
        driver: WebDriver instance
        text: texte exact du bouton (optionnel)
        label_hint: hint de label (optionnel, fallback si text vide)
        depth: profondeur max de frames à explorer
    
    Returns:
        True si CTA cliqué avec succès
    """
    from frame_utils import iter_frame_chains, switch_to_frame_chain

    raw = text if text is not None else (label_hint or "")
    raw = (raw or "").strip()
    if not raw:
        return False

    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s or "").replace("\u00A0", " ").lower()
        s = re.sub(r"[»«""\"'›→·•:]+", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    needle = _norm(raw)
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
                els = driver.query_selector_all(css)
            except Exception:
                els = []

            for el in els:
                try:
                    if not el.is_visible():
                        continue
                    # Extraction texte : value peut être symbolique (">>" etc.), fallback sur aria-label
                    raw_val = (el.inner_text() or "") or (el.get_attribute("value") or "")
                    t = _norm(raw_val)
                    if not t or not any(c.isalpha() for c in t):
                        t = _norm(el.get_attribute("aria-label") or "")
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
                        driver.evaluate("(el) => el.scrollIntoView({block:\'center\'})", el)
                    except Exception:
                        pass

                    # click robuste (JS)
                    try:
                        driver.evaluate("(el) => el.click()", el)
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