# survey_difficulty_guard.py
"""
Détection DOM des surveys "stricts" (anti-bot / interactions complexes).
But : ne pas dépendre d'une liste de domaines, mais du CONTENU réel de la page.

Retour :
  detect_strict_survey(driver) -> (is_strict: bool, reason: str|None)
"""

from __future__ import annotations
from typing import Tuple, Optional
from urllib.parse import parse_qs, urlparse

from selenium.webdriver.common.by import By


# ✅ Selectors "forts" (si présents → très probable que ce soit strict)
STRICT_SELECTORS = {
    # Captcha / anti-bot classique
    # IMPORTANT : [class*='captcha'] RETIRÉ — trop générique.
    # Decipher encode le type de bloc dans les classes CSS (ex: label_Recaptcha_Human)
    # ce qui fait matcher n'importe quelle page Decipher contenant un bloc "Recaptcha".
    # On ne garde que les sélecteurs qui ciblent des WIDGETS réels et interactifs.
    "captcha": [
        "iframe[src*='recaptcha']",
        "iframe[src*='captcha']",
        "iframe[title*='recaptcha']",
        ".g-recaptcha",
        "[data-sitekey]",
        "#captcha",
    ],

    # Drag & drop (très fréquent dans les contrôles humains)
    # NOTE: Ces selectors nécessitent une vérification de VISIBILITÉ
    # car beaucoup de frameworks ont des éléments drag UI cachés (modales, etc.)
    "drag_drop": [
        "[draggable='true']",
        "[class*='draggable']",
        "[class*='dropzone']",
        "[class*='drop-zone']",
        "[data-dropzone]",
        # Angular CDK Drag & Drop (très utilisé par PureSpectrum)
        "[cdkdrag]",
        "[cdkdroplist]",
        "[cdkdroplistgroup]",
        "[class*='cdk-drag']",
        "[class*='cdk-drop-list']",
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
    # NOTE: pas de fallback générique "audio/video" ici.
    # Les mots "video" / "audio" créent des faux positifs sur des questions standards
    # (ex: "Amazon Prime Video"). Le blocage média est géré plus bas avec des signaux
    # explicites de permission/capture ou de lecture obligatoire (<audio>/<video> + consigne).
}


def _has_datadome_iframe(driver) -> bool:
    """True si un iframe DataDome (captcha-delivery.com) est présent dans la page."""
    try:
        iframes = driver.find_elements(
            By.CSS_SELECTOR,
            'iframe[src*="captcha-delivery.com"], iframe[title*="DataDome"]',
        )
        return bool(iframes)
    except Exception:
        return False


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
        + div.rsBtn (boutons de réponse, ≥2)
    
    Ces pages ne sont pas supportées en V1 prod → on les abandonne.
    """
    try:
        # 1) Vérifier la présence de rsScrollGridWrappper ou similaire
        scroll_els = driver.find_elements(By.CSS_SELECTOR, 
            "div.rsScrollGridWrappper, div[class*='rsScrollGridW']")
        
        if not scroll_els:
            return False
        
        # 2) Vérifier la présence de boutons rsBtn (≥2)
        rsBtn_els = driver.find_elements(By.CSS_SELECTOR, "div.rsBtn")
        
        if len(rsBtn_els) < 2:
            return False
        
        # 3) Vérifier qu'il y a une image dans la page
        imgs = driver.find_elements(By.TAG_NAME, "img")
        has_walr_image = False
        for img in imgs:
            try:
                src = img.get_attribute("src") or ""
                if "walr.com" in src or (src.startswith("http") and img.is_displayed()):
                    has_walr_image = True
                    break
            except Exception:
                continue
        
        if has_walr_image:
            print(f"[DIFFICULTY_GUARD] ✓ Image evaluation: rsScrollGrid + {len(rsBtn_els)} rsBtn + image")
            return True

        return False

    except Exception as e:
        print(f"[DIFFICULTY_GUARD] Exception _detect_image_evaluation: {e}")
        return False


def _is_large_visible_image(el) -> bool:
    """Détecte une image réellement centrale (pas un petit logo décoratif)."""
    try:
        if not el.is_displayed():
            return False
        rect = el.rect or {}
        width = rect.get("width", 0) or 0
        height = rect.get("height", 0) or 0
        return width >= 220 and height >= 120
    except Exception:
        return False


def _detect_ta_image_only_question(driver) -> bool:
    """
    Détecte un pattern DOM précis observé sur certaines pages:
      - image centrale avec class taImage
      - zone de réponse texte
      - absence d'options radio/checkbox visibles

    Ce pattern indique une question dépendante de l'image (DOM-only insuffisant).
    """
    try:
        ta_images = driver.find_elements(By.CSS_SELECTOR, "img.taImage")
        has_large_ta_image = any(_is_large_visible_image(img) for img in ta_images)
        if not has_large_ta_image:
            return False

        textareas = driver.find_elements(
            By.CSS_SELECTOR,
            "textarea[required], textarea.mat-mdc-input-element, textarea[name='selectedOptField']",
        )
        if not textareas:
            return False

        option_inputs = driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='radio'], input[type='checkbox'], [role='radio'], [role='checkbox'], div.rsBtn",
        )
        if option_inputs:
            return False

        print("[DIFFICULTY_GUARD] ✓ Image evaluation: taImage + textarea + no_choice_options")
        return True

    except Exception as e:
        print(f"[DIFFICULTY_GUARD] Exception _detect_ta_image_only_question: {e}")
        return False


def _is_element_visible(el) -> bool:
    """Vérifie si un élément est réellement visible (pas caché dans une modale, etc.)"""
    try:
        if not el.is_displayed():
            return False
        rect = el.rect
        if rect.get("width", 0) < 10 or rect.get("height", 0) < 10:
            return False
        return True
    except Exception:
        return False


def _is_actionable_captcha_element(el) -> bool:
    """
    Évite les faux positifs sur les badges/passive widgets (ex: reCAPTCHA invisible en footer).
    On ne garde que les éléments qui ressemblent à un challenge utilisateur réel.
    """
    try:
        tag = (el.tag_name or "").lower()
        cls = (el.get_attribute("class") or "").lower()
        el_id = (el.get_attribute("id") or "").lower()
        src = (el.get_attribute("src") or "").lower()
        try:
            badge_ancestor = bool(
                el.parent.execute_script(
                    "return !!arguments[0].closest('.g-recaptcha-badge, .grecaptcha-badge');",
                    el,
                )
            )
        except Exception:
            badge_ancestor = False

        # Badge reCAPTCHA v3/invisible (footer), non bloquant pour la progression.
        if "g-recaptcha-badge" in cls:
            return False

        # Iframe placé dans le badge reCAPTCHA footer (v3/enterprise invisible).
        if badge_ancestor:
            return False

        # Textarea/token caché injecté par reCAPTCHA (jamais un challenge à résoudre).
        if tag == "textarea" and (
            "g-recaptcha-response" in el_id
            or "g-recaptcha-response" in (el.get_attribute("name") or "").lower()
        ):
            return False

        # Anchor iframe invisible -> pas de challenge (simple initialisation widget).
        if tag == "iframe" and "recaptcha" in src and ("/anchor" in src):
            try:
                size = (parse_qs(urlparse(src).query).get("size", [""])[0] or "").lower()
            except Exception:
                size = ""
            if size == "invisible":
                return False

        return True
    except Exception:
        return False


def detect_strict_survey(driver) -> Tuple[bool, Optional[str]]:
    """
    Détecte si la page demande une interaction "stricte".
    - 0) Check image evaluation (Walr) - PRIORITAIRE
    - 1) Check selectors (rapide)
    - 2) Fallback keywords (texte)
    """
    # === 0) IMAGE EVALUATION (Walr) - Non supporté en V1 prod ===
    if _detect_image_evaluation(driver) or _detect_ta_image_only_question(driver):
        return True, "image_evaluation"
    
    # === 1) DOM selectors ===
    for reason, selectors in STRICT_SELECTORS.items():
        matches = []
        for sel in selectors:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    matches.append(sel)
            except Exception:
                continue

        # 🧠 Drag & drop : nécessite AU MOINS 2 signaux VISIBLES
        # Beaucoup de frameworks ont des éléments drag cachés (modales, etc.)
        if reason == "drag_drop":
            # DataDome expose un slider (draggable) dans son iframe — ne pas le confondre
            # avec une interaction drag_drop stricte non gérée : DataDome est gérable.
            if _has_datadome_iframe(driver):
                continue
            visible_count = 0
            for sel in matches:
                try:
                    for el in driver.find_elements(By.CSS_SELECTOR, sel):
                        if _is_element_visible(el):
                            visible_count += 1
                            if visible_count >= 2:
                                print(
                                    f"[DIFFICULTY_GUARD] drag_drop detected -> allowed (supported), "
                                    f"visible_elements={visible_count}"
                                )
                                return False, None
                except Exception:
                    continue
            continue

        # Captcha : au moins un élément VISIBLE
        if reason == "captcha":
            # DataDome est gérable par datadome_handler — ne pas le traiter comme strict.
            # Son iframe (captcha-delivery.com) matche "iframe[src*='captcha']" → exclusion.
            if _has_datadome_iframe(driver):
                continue
            for sel in matches:
                try:
                    for el in driver.find_elements(By.CSS_SELECTOR, sel):
                        if _is_element_visible(el) and _is_actionable_captcha_element(el):
                            return True, "captcha"
                except Exception:
                    continue
            continue

        # Hold button : 2 signaux + texte explicite
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

    # --- AUDIO / VIDEO ---
    txt = _page_text_lc(driver)

    MICRO_CAMERA_KEYWORDS = [
        "microphone",
        "mic",
        "webcam",
        "camera",
        "caméra",
        "open camera",
        "open microphone",
        "autoriser le micro",
        "autoriser la camera",
        "autoriser la caméra",
        "permission",
        "enregistrement",
        "record",
        "recording",
        "audio player",
        "change microphone",
        "change camera",
        "flip camera",
    ]

    # Signaux textuels explicites d'action/permission micro-caméra.
    # Objectif: éviter les faux positifs quand "audio" est cité dans un disclaimer légal.
    MICRO_CAMERA_PERMISSION_TEXT_SIGNALS = [
        "allow microphone",
        "allow camera",
        "autoriser le micro",
        "autoriser la camera",
        "autoriser la caméra",
        "enable microphone",
        "enable camera",
        "turn on your microphone",
        "turn on your camera",
        "start recording",
        "record your answer",
        "enregistrez votre réponse",
        "change microphone",
        "change camera",
        "flip camera",
    ]

    # Signaux DOM typiques (ex: mediatestimonial / videojs-record)
    MICRO_CAMERA_SELECTORS = [
        ".mediatestimonial-dq",
        "[class*='mediatestimonial']",
        "[class*='vjs-record']",
        ".vjs-record",
        ".vjs-camera-button",
        ".vjs-record-button",
        "[title*='Microphone']",
        "[title*='Camera']",
        "[aria-label*='microphone']",
        "[aria-label*='camera']",
        "[aria-label*='webcam']",
    ]

    has_mic_cam_keyword = any(k in txt for k in MICRO_CAMERA_KEYWORDS)
    has_mic_cam_ui = False
    try:
        for sel in MICRO_CAMERA_SELECTORS:
            try:
                if driver.find_elements(By.CSS_SELECTOR, sel):
                    has_mic_cam_ui = True
                    break
            except Exception:
                continue
    except Exception:
        pass

    has_mic_cam_permission_text = any(k in txt for k in MICRO_CAMERA_PERMISSION_TEXT_SIGNALS)

    has_mic_cam_js_api = False
    try:
        page_html = (
            driver.execute_script("return document.documentElement.outerHTML || ''") or ""
        ).lower()
        has_mic_cam_js_api = any(
            token in page_html
            for token in (
                "navigator.mediadevices.getusermedia",
                "getusermedia(",
                "mediarecorder",
                "webkitgetusermedia",
            )
        )
    except Exception:
        pass

    if has_mic_cam_keyword:
        has_ui_or_permission = has_mic_cam_ui or has_mic_cam_permission_text or has_mic_cam_js_api
        if has_ui_or_permission:
            print("[DIFFICULTY_GUARD] audio_detected_reason=ui_or_permission")
            return True, "audio_capture"
        print("[DIFFICULTY_GUARD] audio_detected_reason=keyword_only")

# --- AUDIO / VIDEO : strict UNIQUEMENT si obligation explicite ---

    AUDIO_VIDEO_OBLIGATION_KEYWORDS = [
        "écoutez", "regardez", "listen", "watch",
        "please listen", "please watch",
        "après avoir écouté", "après avoir regardé",
        "you must listen", "you must watch",
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

    # Keywords fallback
    if txt:
        for reason, toks in STRICT_KEYWORDS.items():
            if reason == "captcha":
                continue
            if any(tok in txt for tok in toks):
                return True, reason

    return False, None
