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
    - Met à jour driver._current_frame (assignation dynamique inconditionnelle, y compris
      sur une Page Playwright native sans shim) pour que les appelants puissent lire le
      contexte courant via :
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
        # Assignation inconditionnelle (pas de hasattr() gate) : Playwright Page/Frame
        # supportent l'attribution dynamique d'attributs (cf. main.py:864
        # page._survey_account_id = account_id, sur le même type d'objet). Gater
        # derrière hasattr(driver, "_current_frame") empêchait toute création de cet
        # attribut sur un driver qui ne l'avait jamais eu au préalable — notamment la
        # Page brute obtenue via connect_over_cdp dans le flux d'attache à un navigateur
        # déjà lancé (main.py::run_attach_takeover) — bloquant silencieusement la
        # propagation du contexte de frame pour toute la session : _current_frame
        # n'était jamais créé, donc getattr(driver, "_current_frame", driver) retombait
        # en permanence sur le document racine dans tous les modules appelants.
        try:
            driver._current_frame = page
        except Exception:
            pass

    _reset()  # Repositionner au contexte racine avant toute navigation

    # Résolution de la chaîne AVANT le yield : toute exception ici ne peut pas
    # entrer en conflit avec un throw() ultérieur du context manager, car on
    # n'est pas encore suspendu sur un yield. Ne jamais fusionner cette
    # résolution avec le bloc try/finally qui entoure le yield ci-dessous —
    # un except Exception autour du yield intercepterait aussi l'exception
    # relancée par __exit__() quand le corps du "with" échoue, et un second
    # yield après throw() lève RuntimeError("generator didn't stop after
    # throw()") côté contextlib, masquant l'erreur d'origine.
    ok = not chain
    target = None
    if chain:
        try:
            current = page.main_frame
            for idx in chain:
                children = list(current.child_frames)
                if idx < 0 or idx >= len(children):
                    ok = False
                    break
                current = children[idx]
            else:
                ok = True
                target = current
        except Exception:
            ok = False

    if ok and chain:
        # Mettre à jour _current_frame pour les appelants (compat getattr pattern).
        # Assignation inconditionnelle, même raison que dans _reset() ci-dessus.
        try:
            driver._current_frame = target
        except Exception:
            pass

    try:
        yield ok
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
