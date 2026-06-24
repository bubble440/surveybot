"""
input_slider.py - Gestion des sliders pour input_handler

Ce module contient:
- set_sliderpoints: Gestion des sliders Decipher/Behaviorally
- Support jQuery-UI sliders
- Vérification off-scale

Dépendances:
- input_utils pour les fonctions utilitaires
"""






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
        h = track.query_selector("a.ui-slider-handle")
        if h is None:
            return None
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
        blocks_all = root.find_elements("css selector", ".sq-sliderpoints-container")
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
                _leg = c.query_selector(".sq-sliderpoints-row-legend")
                lbl = _normalize_slider_text(_leg.inner_text() if _leg else "")
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
            legends = b.query_selector_all(".sliderpoints_legend .sliderpoints-legenditem")
            legend_txts = [_normalize_slider_text(x.inner_text()) for x in legends if (x.inner_text() or "").strip()]
            if not legend_txts:
                continue

            idx = next(
                (i for i, t in enumerate(legend_txts) if t and (needle == t or needle in t)),
                -1,
            )
            if idx < 0:
                continue

            track = b.query_selector(".ui-slider-horizontal")
            if not track:
                continue

            driver.evaluate("(el) => el.scrollIntoView({block:\'center\'})", track)

            # calcule position (fallback seulement)
            r = track.bounding_box() or {}
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
                sel = b.query_selector("select")
                if sel is None:
                    raise Exception("no select")
                _sel_el_sl = sel

                def _is_placeholder(opt) -> bool:
                    v = (opt.get_attribute("value") or "").strip()
                    t = _normalize_slider_text(opt["text"])
                    return (v in ("", "-1")) or any(k in t for k in ("selection", "select", "choose", "sélection"))

                offset = 1 if driver.evaluate("el => Array.from(el.options).map(o => ({value:o.value,text:o.text.trim()}))", _sel_el_sl) and _is_placeholder(driver.evaluate("el => Array.from(el.options).map(o => ({value:o.value,text:o.text.trim()}))", _sel_el_sl)[0]) else 0
                real_idx = idx + offset
                real_idx = min(len(driver.evaluate("el => Array.from(el.options).map(o => ({value:o.value,text:o.text.trim()}))", _sel_el_sl)) - 1, max(0, real_idx))
                opt = driver.evaluate("el => Array.from(el.options).map(o => ({value:o.value,text:o.text.trim()}))", _sel_el_sl)[real_idx]
                desired_val = (opt.get("value") or "").strip() or None
            except Exception:
                sel = None
                desired_val = None

            def _dispatch_select_events(el) -> None:
                try:
                    el.evaluate("(s) => { for (const t of ['input','change','blur']) "
                                "{ try { s.dispatchEvent(new Event(t, {bubbles:true})); } catch(e) {} } }")
                except Exception:
                    pass

            def _apply_via_widget() -> None:
                # 1) Click sur le "point" (circle)
                clicked = False
                circles = []
                try:
                    circles = b.query_selector_all(
                        ".sliderpoints_circleLegend span.fa-icon-circle, .sliderpoints_circleLegend span"
                    )
                except Exception:
                    circles = []

                try:
                    if 0 <= idx < len(circles):
                        driver.evaluate("(el) => el.scrollIntoView({block:'center'})", circles[idx])
                        driver.evaluate("(el) => el.click()", circles[idx])
                        clicked = True
                except Exception:
                    clicked = False

                # Fallback: click sur le texte de légende
                if not clicked:
                    try:
                        if 0 <= idx < len(legends):
                            driver.evaluate("(el) => el.scrollIntoView({block:'center'})", legends[idx])
                            driver.evaluate("(el) => el.click()", legends[idx])
                    except Exception:
                        pass

                # 2) Set <select> (value backend)
                if sel is not None:
                    try:
                        cur = (sel.get_attribute("value") or "").strip()
                        if desired_val is not None and cur != desired_val:
                            driver.evaluate("([e,v]) => { e.value = v; }", [sel, desired_val])
                        elif desired_val is None:
                            driver.evaluate("([e,i]) => { e.selectedIndex = i; }", [sel, int(real_idx)])
                        _dispatch_select_events(sel)
                    except Exception:
                        pass

                # 3) jQuery-UI slider('value', ...) si dispo
                try:
                    v = int(desired_val) if desired_val is not None and str(desired_val).lstrip("-").isdigit() else int(idx)
                    b.evaluate("([root, v]) => { const s = root.querySelector('.ui-slider-horizontal,.ui-slider');"
                               " if (s && window.jQuery && window.jQuery(s).slider) {"
                               " try { window.jQuery(s).slider('value', v); } catch(e) {}"
                               " try { window.jQuery(s).trigger('change'); } catch(e) {}"
                               " try { window.jQuery(s).trigger('slidechange'); } catch(e) {} } }",
                               [b, v])
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
                    t2 = b.query_selector(".ui-slider-horizontal")
                    if t2 is None:
                        raise Exception("no slider")
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
                    track.evaluate(
                        "([track, x, y]) => { const r = track.getBoundingClientRect();"
                        " const cx = Math.min(r.right-2, Math.max(r.left+2, r.left+x));"
                        " const cy = Math.min(r.bottom-2, Math.max(r.top+2, r.top+y));"
                        " const ev = (t) => new MouseEvent(t,{bubbles:true,cancelable:true,clientX:cx,clientY:cy});"
                        " ['mousemove','mousedown','mouseup','click'].forEach(t => track.dispatchEvent(ev(t))); }",
                        [track, int(x), int(y)]
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
