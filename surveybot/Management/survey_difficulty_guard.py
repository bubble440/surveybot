# survey_difficulty_guard.py
"""
Détection DOM des surveys "stricts" (anti-bot / interactions complexes).
But : ne pas dépendre d'une liste de domaines, mais du CONTENU réel de la page.

Retour :
  detect_strict_survey(driver) -> (is_strict: bool, reason: str|None)
"""

from __future__ import annotations
from typing import Tuple, Optional

from selenium.webdriver.common.by import By


# ✅ Selectors "forts" (si présents → très probable que ce soit strict)
STRICT_SELECTORS = {
    # Captcha / anti-bot classique
    "captcha": [
        "iframe[src*='captcha']",
        "iframe[src*='recaptcha']",
        "iframe[title*='recaptcha']",
        "[class*='captcha']",
        "#captcha",
        ".g-recaptcha",
        "[data-sitekey]",
    ],

    # Drag & drop (très fréquent dans les contrôles humains)
    "drag_drop": [
        # élément explicitement draggable
        "[draggable='true']",

        # bibliothèques de drag & drop connues
        "[class*='drag-handle']",
        "[class*='drag-item']",
        "[class*='draggable']",

        # zones explicites de dépôt (dropzone, pas backdrop)
        "[class*='dropzone']",
        "[class*='drop-zone']",
        "[data-dropzone]",
        # Angular CDK Drag & Drop (très utilisé par PureSpectrum)
        "[cdkdrag]",
        "[cdkdroplist]",
        "[cdkdroplistgroup]",
        "[class*='cdk-drag']",
        "[class*='cdk-drop-list']",
        "[class*='drop-zone']",
        "[aria-label*='drag']",
        "[aria-label*='drop']",
    ],

    # Maintenir un bouton / slider humain
    "hold_button": [
        "[class*='hold']",
        "[id*='hold']",
        "[aria-label*='hold']",
        "[aria-label*='maintenir']",
        "[class*='press']",
        "[class*='slider']",
    ],
}

# ✅ Mots-clés (fallback quand le DOM est trop dynamique)
STRICT_KEYWORDS = {
    "captcha": ["captcha", "recaptcha", "i am not a robot", "je ne suis pas un robot"],
    "hold_button": ["press and hold", "maintenir", "appuyez et maintenez"],
    "audio_video": ["listen", "écoutez", "watch", "regardez", "video", "audio"],
}


def _page_text_lc(driver) -> str:
    """Récupère le texte de la page en minuscules, de façon safe."""
    try:
        return (driver.execute_script("return document.body.innerText || ''") or "").lower()
    except Exception:
        return ""


def detect_strict_survey(driver) -> Tuple[bool, Optional[str]]:
    """
    Détecte si la page demande une interaction "stricte".
    - 1) Check selectors (rapide)
    - 2) Fallback keywords (texte)
    """
    # 1) DOM selectors
    for reason, selectors in STRICT_SELECTORS.items():
        matches = []
        for sel in selectors:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    matches.append(sel)
            except Exception:
                continue

        # 🧠 Drag & drop : nécessite AU MOINS 2 signaux
        if reason == "drag_drop":
            if len(matches) >= 2:
                return True, "drag_drop"
            continue

        if reason == "captcha":
            visible = False
            for sel in matches:
                try:
                    for el in driver.find_elements(By.CSS_SELECTOR, sel):
                        if el.is_displayed():
                            visible = True
                            break
                except Exception:
                    continue

            if visible:
                return True, "captcha"

            continue

        # autres raisons : 1 signal suffit
        if reason == "hold_button":
            if len(matches) >= 2:
                txt = _page_text_lc(driver)
                HOLD_TEXTS = [
                    "maintenez",
                    "press and hold",
                    "appuyez et maintenez",
                    "keep pressing",
                    "hold to continue",
                ]
                if any(t in txt for t in HOLD_TEXTS):
                    return True, "hold_button"
            continue

    # --- AUDIO / VIDEO : strict UNIQUEMENT si obligation explicite ---
    txt = _page_text_lc(driver)

    AUDIO_VIDEO_OBLIGATION_KEYWORDS = [
        "écoutez",
        "regardez",
        "listen",
        "watch",
        "please listen",
        "please watch",
        "après avoir écouté",
        "après avoir regardé",
        "you must listen",
        "you must watch",
    ]

    has_media = False
    try:
        if driver.find_elements(By.TAG_NAME, "audio") or driver.find_elements(By.TAG_NAME, "video"):
            has_media = True
    except Exception:
        pass

    if has_media:
        if any(k in txt for k in AUDIO_VIDEO_OBLIGATION_KEYWORDS):
            return True, "audio_video_required"

    # 2) Keywords fallback (⚠️ captcha traité différemment)
    if txt:
        for reason, toks in STRICT_KEYWORDS.items():
            if reason == "captcha":
                continue  # captcha déjà géré par DOM + visibilité
            if any(tok in txt for tok in toks):
                return True, reason

    return False, None
