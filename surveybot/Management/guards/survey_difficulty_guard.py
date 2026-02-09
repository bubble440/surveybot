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


def _detect_image_evaluation(driver) -> bool:
    """
    Détecte les pages d'évaluation d'image (Walr) qui nécessitent Vision API.
    
    Pattern DOM:
        <div class="rsScrollGridWrappper">  (contient une image)
        <div class="rsFlexBtnContainer">    (boutons de réponse)
    
    Ces pages ne sont pas supportées en V1 prod → on les abandonne.
    """
    try:
        # Chercher le conteneur de scroll avec image
        scroll_containers = driver.find_elements(By.CSS_SELECTOR, 
            "div.rsScrollGridWrappper, div[class*='rsScrollGrid']")
        
        if not scroll_containers:
            return False
        
        # Vérifier qu'il y a une image dans le conteneur
        has_image = False
        for sc in scroll_containers:
            try:
                style = sc.get_attribute("style") or ""
                if "display: none" in style.lower() or "display:none" in style.lower():
                    continue
                    
                imgs = sc.find_elements(By.CSS_SELECTOR, "img")
                for img in imgs:
                    src = img.get_attribute("src") or ""
                    if src and src.startswith("http"):
                        has_image = True
                        break
                if has_image:
                    break
            except Exception:
                continue
        
        if not has_image:
            return False
        
        # Vérifier qu'il y a des boutons de réponse
        btn_containers = driver.find_elements(By.CSS_SELECTOR, 
            "div.rsFlexBtnContainer, div[class*='rsFlexBtn']")
        
        if not btn_containers:
            return False
        
        # Vérifier qu'au moins un conteneur a des boutons rsBtn
        for bc in btn_containers:
            try:
                style = bc.get_attribute("style") or ""
                if "display: none" in style.lower() or "display:none" in style.lower():
                    continue
                    
                btns = bc.find_elements(By.CSS_SELECTOR, "div.rsBtn")
                if len(btns) >= 2:
                    print(f"[DIFFICULTY_GUARD] Image evaluation détectée: {len(btns)} boutons rsBtn")
                    return True
            except Exception:
                continue
        
        return False
    except Exception as e:
        print(f"[DIFFICULTY_GUARD] Exception _detect_image_evaluation: {e}")
        return False


def detect_strict_survey(driver) -> Tuple[bool, Optional[str]]:
    """
    Détecte si la page demande une interaction "stricte".
    - 1) Check selectors (rapide)
    - 2) Check image evaluation (Walr) 
    - 3) Fallback keywords (texte)
    """
    # 0) IMAGE EVALUATION (Walr) - Non supporté en V1 prod
    # Détection PRIORITAIRE car ces pages sont des slideshow images
    if _detect_image_evaluation(driver):
        return True, "image_evaluation"
    
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