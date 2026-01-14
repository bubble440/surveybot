# surveybot/Survey/frame_utils.py
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, List
from selenium.webdriver.common.by import By

FrameChain = List[int]


def _frame_elements(driver):
    """Retourne la liste des <iframe>/<frame> du contexte courant."""
    try:
        return driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    except Exception:
        return []


@contextmanager
def switch_to_frame_chain(driver, chain: FrameChain):
    """
    Se positionne dans une chaîne d'iframes via des indices par niveau.
    Toujours retour à default_content en sortie.
    """
    try:
        driver.switch_to.default_content()
        for idx in chain:
            frames = _frame_elements(driver)
            if idx < 0 or idx >= len(frames):
                yield False
                return
            driver.switch_to.frame(frames[idx])
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
            rec(chain, depth + 1)

    rec([], 0)
