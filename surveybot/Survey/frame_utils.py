# surveybot/Survey/frame_utils.py
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, List
from selenium.webdriver.common.by import By

FrameChain = List[int]

def _frame_elements(driver):
    """Retourne la liste des <iframe>/<frame> du contexte courant."""
    try:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")

        # Fallback: certaines pages rendent le CSS selector instable.
        # On tente TAG_NAME (sans exception si l'un des tags n'existe pas).
        if not frames:
            out = []
            try:
                out.extend(driver.find_elements(By.TAG_NAME, "iframe"))
            except Exception:
                pass
            try:
                out.extend(driver.find_elements(By.TAG_NAME, "frame"))
            except Exception:
                pass
            frames = out

        return frames
    except Exception:
        return []

@contextmanager
def switch_to_frame_chain(driver, chain: FrameChain):
    """
    Se positionne dans une chaîne d'iframes via des indices par niveau.
    Toujours retour à default_content en sortie.
    """
    try:
        # IMPORTANT: ne jamais crasher si la fenêtre/onglet a été fermé
        try:
            driver.switch_to.default_content()
        except Exception:
            yield False
            return

        for idx in chain:
            frames = _frame_elements(driver)
            if idx < 0 or idx >= len(frames):
                yield False
                return
            try:
                driver.switch_to.frame(frames[idx])
            except Exception:
                yield False
                return

        yield True
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass


def iter_frame_chains(driver, max_depth: int = 2) -> Iterator[FrameChain]:
    """
    Génère: [] puis toutes les chaînes d'iframes jusqu'à max_depth.
    Exemple: [], [0], [1], [0,0], [0,1], ...
    """
    yield []

    def rec(prefix: FrameChain, depth: int):
        if depth >= max_depth:
            return

        # compter les frames dans CE contexte
        with switch_to_frame_chain(driver, prefix) as ok:
            if not ok:
                return
            n = len(_frame_elements(driver))

        for i in range(n):
            chain = prefix + [i]
            yield chain
            yield from rec(chain, depth + 1)

    yield from rec([], 0)
