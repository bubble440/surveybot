# surveybot/Survey/frame_utils.py
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, List


FrameChain = List[int]




def _frame_elements(driver):
    """
    Retourne les frames enfants du contexte courant (Playwright natif).

    Utilisé en interne pour compter les frames disponibles à un niveau donné.
    Fonctionne avec driver = shim ou Page native :
    - Si driver._current_frame est une Frame Playwright → retourne ses child_frames.
    - Si driver._current_frame est une Page Playwright → retourne les child_frames du main_frame.
    """
    try:
        page = driver
        current = getattr(driver, "_current_frame", page)
        if hasattr(current, "child_frames"):
            # current est une Frame Playwright
            return list(current.child_frames)
        # current est une Page Playwright — accéder via main_frame
        return list(current.main_frame.child_frames)
    except Exception:
        return []


@contextmanager
def switch_to_frame_chain(driver, chain: FrameChain):
    """
    Context manager qui positionne dans une chaîne d'iframes par indices (Playwright natif).

    Interface publique :
    - Entrée : driver = PlaywrightDriverShim OU Page Playwright native.
    - Yield   : True si la navigation réussit, False si un index est hors-borne ou erreur.
    - Met à jour driver._current_frame (si driver est un shim) pour que les appelants
      puissent lire le contexte courant via :
          current_frame = getattr(driver, "_current_frame", driver)
      puis appeler current_frame.evaluate(...) / current_frame.content() etc.
    - Toujours retour au contexte racine (Page) en sortie du with (équivalent default_content()).

    Exemples :
        with switch_to_frame_chain(driver, []) as ok:    # contexte racine
        with switch_to_frame_chain(driver, [0]) as ok:   # première iframe
        with switch_to_frame_chain(driver, [0, 1]) as ok: # iframe imbriquée
    """
    page = driver

    def _reset():
        """Retour au contexte racine : driver._current_frame = page."""
        if hasattr(driver, "_current_frame"):
            driver._current_frame = page

    try:
        _reset()  # Repositionner au contexte racine avant toute navigation

        if not chain:
            # Chaîne vide → contexte racine, _current_frame reste page
            yield True
            return

        # Navigation dans la chaîne d'iframes via l'API Playwright native
        current = page.main_frame
        for idx in chain:
            children = list(current.child_frames)
            if idx < 0 or idx >= len(children):
                yield False
                return
            current = children[idx]

        # Mettre à jour _current_frame pour les appelants (compat getattr pattern)
        if hasattr(driver, "_current_frame"):
            driver._current_frame = current

        yield True
    except Exception:
        yield False
    finally:
        _reset()


def iter_frame_chains(driver, max_depth: int = 2) -> Iterator[FrameChain]:
    """
    Génère: [] puis toutes les chaînes d'iframes jusqu'à max_depth.
    Exemple: [], [0], [1], [0,0], [0,1], ...
    """
    yield []

    def rec(prefix: FrameChain, depth: int):
        if depth >= max_depth:
            return

        # compter les frames dans ce contexte
        with switch_to_frame_chain(driver, prefix) as ok:
            if not ok:
                return
            n = len(_frame_elements(driver))

        for i in range(n):
            chain = prefix + [i]
            yield chain
            rec(chain, depth + 1)

    rec([], 0)
