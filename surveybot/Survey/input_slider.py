"""
input_slider.py - Gestion des sliders pour input_handler

Ce module contient:
- set_sliderpoints: Gestion des sliders Decipher/Behaviorally
- Support jQuery-UI sliders
- Vérification off-scale

Dépendances:
- input_utils pour les fonctions utilitaires
"""

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
import unicodedata
import re
import time

# Import depuis input_utils
from Survey.input_utils import (
    find_context_container,
    pause_here,
)


# =============================================================================
# HELPERS SLIDER
# =============================================================================

def _strip_accents(s: str) -> str:
    """Retire les accents d'une chaîne."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _normalize_slider_text(s: str) -> str:
    """Normalisation pour comparaison de textes slider."""
    s = _strip_accents((s or "").replace("\u00a0", " "))
    s = s.lower()
    s = re.sub(r"[»«""\"''›→·•:…]", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _handle_left_pct(track) -> float | None:
    """Retourne le pourcentage de position du handle slider."""
    try:
        h = track.find_element(By.CSS_SELECTOR, "a.ui-slider-handle")
        style = (h.get_attribute("style") or "").lower()
        m = re.search(r"left\s*:\s*([0-9.]+)%", style)
        if m:
            return float(m.group(1))
    except Exception:
        return None
    return None


def _is_off_scale(track) -> bool:
    """Vérifie si le slider est en position 'off scale' (tout à droite)."""
    try:
        cls = (track.get_attribute("class") or "").lower()
        if "off scale" in cls or "offscale" in cls:
            return True
    except Exception:
        pass
    pct = _handle_left_pct(track)
    if pct is not None and pct >= 99.0:
        return True
    return False


# =============================================================================
# FONCTION PRINCIPALE SET_SLIDERPOINTS
# =============================================================================

def set_sliderpoints(driver, choice_text: str, context_hint: str | None = None) -> bool:
    """
    Behaviorally/Decipher 'sq-sliderpoints'.
    
    Stratégie (prédictible, 2 tentatives max):
    1) Scoper strictement la bonne ligne via le row-legend (si context_hint fourni)
    2) Mapper choice_text -> index sur la légende visible
    3) Appliquer via click sur legend + set du <select> + (si présent) jQuery-UI slider('value', ...)
    4) Vérifier que le slider n'est plus off-scale (handle != ~100%) et que le select a la bonne value.
    
    Args:
        driver: WebDriver
        choice_text: texte de l'option à sélectionner
        context_hint: contexte de question pour scoping
    
    Returns:
        True si slider positionné avec succès
    """
    needle = _normalize_slider_text(choice_text)
    if not needle:
        return False

    # scope optionnel (question courante), sinon page entière
    try:
        scope = find_context_container(driver, context_hint) if context_hint else None
    except Exception:
        scope = None
    root = scope if scope is not None else driver

    # IMPORTANT: utiliser les *containers* (1 ligne = 1 container).
    try:
        blocks_all = root.find_elements(By.CSS_SELECTOR, ".sq-sliderpoints-container")
    except Exception:
        blocks_all = []

    if not blocks_all:
        return False

    # Scoping strict par row label si fourni (évite de répondre la mauvaise ligne)
    blocks = blocks_all
    row_ctx = _normalize_slider_text(context_hint or "")
    if row_ctx:
        matched = None
        saw_any_label = False
        for c in blocks_all:
            try:
                lbl = _normalize_slider_text(c.find_element(By.CSS_SELECTOR, ".sq-sliderpoints-row-legend").text)
            except Exception:
                lbl = ""
            if lbl:
                saw_any_label = True
            if lbl and (lbl == row_ctx or row_ctx in lbl or lbl in row_ctx):
                matched = c
                break

        # Si on a des row labels mais aucun match, on ne "devine" pas.
        if saw_any_label and matched is None:
            return False
        if matched is not None:
            blocks = [matched]

    for b in blocks:
        try:
            legends = b.find_elements(By.CSS_SELECTOR, ".sliderpoints_legend .sliderpoints-legenditem")
            legend_txts = [_normalize_slider_text(x.text) for x in legends if (x.text or "").strip()]
            if not legend_txts:
                continue

            idx = next(
                (i for i, t in enumerate(legend_txts) if t and (needle == t or needle in t or t in needle)),
                -1,
            )
            if idx < 0:
                continue

            try:
                track = b.find_element(By.CSS_SELECTOR, ".ui-slider-horizontal")
            except Exception:
                track = None
            if not track:
                continue

            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", track)

            # calcule position (fallback seulement)
            r = track.rect or {}
            w = int(r.get("width", 0) or 0)
            h = int(r.get("height", 0) or 0)
            steps = max(1, len(legend_txts) - 1)
            x = int((idx / steps) * max(1, w - 4)) + 2
            y = max(1, h // 2)

            # Prépare select + value cible (si présent)
            sel = None
            desired_val: str | None = None
            real_idx = idx
            try:
                sel = b.find_element(By.TAG_NAME, "select")
                S = Select(sel)

                def _is_placeholder(opt) -> bool:
                    v = (opt.get_attribute("value") or "").strip()
                    t = _normalize_slider_text(opt.text)
                    return (v in ("", "-1")) or any(k in t for k in ("selection", "select", "choose", "sélection"))

                offset = 1 if S.options and _is_placeholder(S.options[0]) else 0
                real_idx = idx + offset
                real_idx = min(len(S.options) - 1, max(0, real_idx))
                opt = S.options[real_idx]
                desired_val = (opt.get_attribute("value") or "").strip() or None
            except Exception:
                sel = None
                desired_val = None

            def _dispatch_select_events(el) -> None:
                try:
                    driver.execute_script(
                        """
                        const s = arguments[0];
                        for (const t of ['input','change','blur']) {
                          try { s.dispatchEvent(new Event(t, {bubbles:true})); } catch(e) {}
                        }
                        """,
                        el,
                    )
                except Exception:
                    pass

            def _apply_via_widget() -> None:
                # 1) Click sur le "point" (circle)
                clicked = False
                circles = []
                try:
                    circles = b.find_elements(
                        By.CSS_SELECTOR,
                        ".sliderpoints_circleLegend span.fa-icon-circle, .sliderpoints_circleLegend span"
                    )
                except Exception:
                    circles = []

                try:
                    if 0 <= idx < len(circles):
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", circles[idx])
                        driver.execute_script("arguments[0].click();", circles[idx])
                        clicked = True
                except Exception:
                    clicked = False

                # Fallback: click sur le texte de légende
                if not clicked:
                    try:
                        if 0 <= idx < len(legends):
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", legends[idx])
                            driver.execute_script("arguments[0].click();", legends[idx])
                    except Exception:
                        pass

                # 2) Set <select> (value backend)
                if sel is not None:
                    try:
                        cur = (sel.get_attribute("value") or "").strip()
                        if desired_val is not None and cur != desired_val:
                            driver.execute_script("arguments[0].value = arguments[1];", sel, desired_val)
                        elif desired_val is None:
                            driver.execute_script("arguments[0].selectedIndex = arguments[1];", sel, int(real_idx))
                        _dispatch_select_events(sel)
                    except Exception:
                        pass

                # 3) jQuery-UI slider('value', ...) si dispo
                try:
                    v = int(desired_val) if desired_val is not None and str(desired_val).lstrip("-").isdigit() else int(idx)
                    driver.execute_script(
                        """
                        const root = arguments[0];
                        const v = arguments[1];
                        const slider = root.querySelector('.ui-slider-horizontal, .ui-slider');
                        if (slider && window.jQuery && window.jQuery(slider).slider) {
                          try { window.jQuery(slider).slider('value', v); } catch(e) {}
                          try { window.jQuery(slider).trigger('change'); } catch(e) {}
                          try { window.jQuery(slider).trigger('slidechange'); } catch(e) {}
                        }
                        """,
                        b,
                        v,
                    )
                except Exception:
                    pass

            def _verify() -> bool:
                ok_val = False
                if sel is not None and desired_val is not None:
                    try:
                        ok_val = ((sel.get_attribute("value") or "").strip() == desired_val)
                    except Exception:
                        ok_val = False
                elif sel is None:
                    ok_val = True

                try:
                    t2 = b.find_element(By.CSS_SELECTOR, ".ui-slider-horizontal")
                    ok_scale = not _is_off_scale(t2)
                except Exception:
                    ok_scale = False

                return bool(ok_val and ok_scale)

            # Tentative 1: widget/JS (le plus fiable)
            _apply_via_widget()

            try:
                time.sleep(0.15)
            except Exception:
                pass

            if _verify():
                print(f"✓ Sliderpoints rempli: '{choice_text}'. source: input_slider.py")
                return True

            # Tentative 2: clic piste (fallback)
            try:
                if w > 4:
                    driver.execute_script(
                        """
                        const track = arguments[0];
                        const x = arguments[1];
                        const y = arguments[2];
                        const r = track.getBoundingClientRect();

                        const cx = Math.min(r.right - 2, Math.max(r.left + 2, r.left + x));
                        const cy = Math.min(r.bottom - 2, Math.max(r.top + 2, r.top + y));

                        const ev = (type) => new MouseEvent(type, {
                        bubbles: true,
                        cancelable: true,
                        clientX: cx,
                        clientY: cy
                        });

                        track.dispatchEvent(ev('mousemove'));
                        track.dispatchEvent(ev('mousedown'));
                        track.dispatchEvent(ev('mouseup'));
                        track.dispatchEvent(ev('click'));
                        """,
                        track, int(x), int(y)
                    )
                    time.sleep(0.10)
            except Exception:
                pass

            if sel is not None:
                _dispatch_select_events(sel)

            if _verify():
                print(f"✓ Sliderpoints rempli: '{choice_text}'. source: input_slider.py")
                return True

        except Exception:
            continue

    return False