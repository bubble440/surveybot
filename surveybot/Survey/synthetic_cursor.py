"""
synthetic_cursor.py - Primitive bas niveau de déplacement de souris synthétique + clic.

Fournit move_and_click(page, target) : déplace le curseur depuis sa dernière position
connue sur `page` jusqu'à un point (borné, légèrement décalé) dans la zone de `target`,
en suivant une trajectoire courbe (Bézier cubique) à timing non-uniforme, puis presse et
relâche le bouton de souris avec une tenue variable.

Module isolé, sans aucune connaissance des extracteurs/plateformes/flags de screener,
et sans aucun appel vers le reste du pipeline. Non câblé pour l'instant : livrable
autonome, destiné à être appelé depuis un patch séparé à des points d'interaction précis.

État : la dernière position connue du curseur est portée directement sur l'objet Page
Playwright (attribut privé, cf. le même principe déjà utilisé dans le projet pour
porter un état sur Page/Context, ex. Survey/frame_utils.py::_current_frame). Un nouvel
onglet n'a jamais cet attribut et s'initialise donc proprement, sans supposer de
continuité avec une Page précédente.
"""

from __future__ import annotations

import math
import random
import time

from Survey.log_utils import log_debug

_TAG = "[SYN_CURSOR]"

_CURSOR_STATE_ATTR = "_synth_cursor_xy"

# Nombre de points intermédiaires de la trajectoire (borne dure de boucle).
_MIN_POINTS = 10
_MAX_POINTS = 22

# Durée totale du déplacement.
_MIN_DURATION_S = 0.22
_MAX_DURATION_S = 0.55

# Amplitude de la courbure Bézier, en fraction de la distance start->arrivée.
_MIN_BOW_FRACTION = 0.04
_MAX_BOW_FRACTION = 0.18

# Décalage max du point d'arrivée par rapport au centre de la zone cible,
# en fraction de la demi-largeur / demi-hauteur de cette zone.
_ARRIVAL_JITTER_FRACTION = 0.30

# Amplitude de la modulation de timing (accélération puis décélération).
# Bornée < 1 pour que le poids reste strictement positif au pic (sin=1).
_TIMING_WEIGHT_AMPLITUDE = 0.65

# Tenue du clic (pression -> relâchement).
_MIN_HOLD_S = 0.03
_MAX_HOLD_S = 0.09

# Dimensions de secours si ni viewport_size ni window.inner{Width,Height} ne sont exploitables.
_FALLBACK_WINDOW_W = 1280.0
_FALLBACK_WINDOW_H = 800.0


def move_and_click(page, target, *, button: str = "left") -> bool:
    """
    Déplace le curseur synthétique jusqu'à `target` (Locator ou ElementHandle
    Playwright) puis presse/relâche `button`. Retourne True en cas de succès,
    False en cas de dégradation propre (cible non exploitable ou échec imprévu) -
    ne lève jamais d'exception.
    """
    try:
        box = target.bounding_box()
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            log_debug(_TAG, f"cible non exploitable (bounding box vide/absente) : {box}")
            return False

        start = _current_position(page)
        end = _pick_arrival_point(box)
        _run_move(page, start, end)
        _hold_click(page, button)
        _set_current_position(page, end)
        log_debug(_TAG, f"move_and_click ok start=({start[0]:.0f},{start[1]:.0f}) "
                         f"end=({end[0]:.0f},{end[1]:.0f})")
        return True
    except Exception as exc:
        log_debug(_TAG, f"échec move_and_click, abandon propre : {exc}")
        return False


def _current_position(page):
    pos = getattr(page, _CURSOR_STATE_ATTR, None)
    if isinstance(pos, tuple) and len(pos) == 2:
        return pos
    return _default_start_position(page)


def _default_start_position(page):
    w, h = _window_dims(page)
    return (w / 2.0, h / 2.0)


def _set_current_position(page, pos) -> None:
    try:
        page._synth_cursor_xy = (float(pos[0]), float(pos[1]))
    except Exception:
        pass


def _window_dims(page):
    try:
        vs = page.viewport_size
    except Exception:
        vs = None
    if vs and vs.get("width") and vs.get("height"):
        return float(vs["width"]), float(vs["height"])

    try:
        dims = page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
        w = float((dims or {}).get("w") or 0)
        h = float((dims or {}).get("h") or 0)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass

    return _FALLBACK_WINDOW_W, _FALLBACK_WINDOW_H


def _pick_arrival_point(box):
    x0 = float(box.get("x", 0.0))
    y0 = float(box.get("y", 0.0))
    w = float(box.get("width", 0.0))
    h = float(box.get("height", 0.0))
    cx, cy = x0 + w / 2.0, y0 + h / 2.0

    off_x = (w / 2.0) * _ARRIVAL_JITTER_FRACTION
    off_y = (h / 2.0) * _ARRIVAL_JITTER_FRACTION
    tx = cx + random.uniform(-off_x, off_x)
    ty = cy + random.uniform(-off_y, off_y)

    tx = min(max(tx, x0), x0 + w)
    ty = min(max(ty, y0), y0 + h)
    return tx, ty


def _build_control_points(start, end):
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy)
    if dist < 1.0:
        dist = 1.0

    perp_x, perp_y = -dy / dist, dx / dist
    bow = random.uniform(_MIN_BOW_FRACTION, _MAX_BOW_FRACTION) * dist
    sign = random.choice((-1.0, 1.0))
    offset_x, offset_y = perp_x * bow * sign, perp_y * bow * sign

    p1 = (sx + dx * 0.30 + offset_x, sy + dy * 0.30 + offset_y)
    p2 = (sx + dx * 0.70 + offset_x, sy + dy * 0.70 + offset_y)
    return p1, p2


def _cubic_bezier_point(p0, p1, p2, p3, u):
    mu = 1.0 - u
    x = (mu ** 3) * p0[0] + 3 * (mu ** 2) * u * p1[0] + 3 * mu * (u ** 2) * p2[0] + (u ** 3) * p3[0]
    y = (mu ** 3) * p0[1] + 3 * (mu ** 2) * u * p1[1] + 3 * mu * (u ** 2) * p2[1] + (u ** 3) * p3[1]
    return x, y


def _run_move(page, start, end) -> None:
    p1, p2 = _build_control_points(start, end)
    n_points = random.randint(_MIN_POINTS, _MAX_POINTS)
    duration_s = random.uniform(_MIN_DURATION_S, _MAX_DURATION_S)

    page.mouse.move(start[0], start[1], steps=1)

    # Poids de timing par pas : formule unique (bosse en sinus), minimale au
    # milieu du trajet et maximale aux extrémités -> déplacements spatiaux
    # égaux mais espacés dans le temps de façon non-uniforme (accélération
    # puis décélération), jamais un intervalle fixe.
    weights = [
        1.0 - _TIMING_WEIGHT_AMPLITUDE * math.sin(math.pi * ((i - 0.5) / n_points))
        for i in range(1, n_points + 1)
    ]
    weight_sum = sum(weights)

    for i in range(1, n_points + 1):
        u = i / n_points
        x, y = _cubic_bezier_point(start, p1, p2, end, u)
        dt = duration_s * weights[i - 1] / weight_sum
        time.sleep(max(0.0, dt))
        page.mouse.move(x, y, steps=1)


def _hold_click(page, button: str) -> None:
    hold_s = random.uniform(_MIN_HOLD_S, _MAX_HOLD_S)
    page.mouse.down(button=button)
    time.sleep(hold_s)
    page.mouse.up(button=button)
