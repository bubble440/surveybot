from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
import re, openai, time, unicodedata, os, sys, hashlib, tempfile

def _norm_lc(s: str) -> str:
    s = unicodedata.normalize("NFKC", (s or "")).lower().strip()
    return re.sub(r"\s+", " ", s)

def _env_truthy(name: str, default: str = "0") -> bool:
    v = (os.getenv(name, default) or "").strip().lower()
    return v in ("1", "true", "yes", "on")

def _local_pause_before_cta(reason: str = "") -> None:
    """
    LOCAL ONLY: attend que l'utilisateur appuie sur  avant de cliquer un CTA.
     prod/docker: ne bloque jamais si stdin non-interactif.
    Active uniquement si LOCAL_CTA_REQUIRE_ENTER=1.
    
    En mode LOCAL_UNATTENDED, cette fonction retourne .
    """
    try:
        from config import should_block_for_input
        # En mode unattended ou prod, pas de pause
        if not should_block_for_input():
            return
        if not _env_truthy("LOCAL_CTA_REQUIRE_ENTER", "0"):
            return

        msg = "[LOCAL][PAUSE] Appuie sur  pour autoriser le clic CTA"
        if reason:
            msg += f" ({reason})"
        print(msg, flush=True)
        try:
            input()
        except KeyboardInterrupt:
            raise
    except Exception:
        return

def _is_visible_js(driver, el) -> bool:
    """
    Fallback JavaScript pour  la  d'un .
     quand Selenium.is_displayed() retourne False sur des structures DOM
    complexes (tables  AreYouNet, etc.) alors que l' est visible.
    """
    try:
        return driver.execute_script("""
            var el = arguments[0];
            if (!el) return false;
            var style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            var rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        """, el)
    except Exception:
        return False
    
def _coerce_safe_value_if_questionish(raw_line: str) -> str:
    """
    Si le modÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¨le renvoie par erreur un intitulÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â© de question au lieu d'une valeur,
    fabrique une valeur sÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â»re en fonction du texte.
    Remappe aussi 'number' -> 'text'.
    """
    line = (raw_line or "").strip()
    # parse "label //// type //// contexte" tolÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â©rant
    m = re.split(r"/{4,}", line)
    label = (m[0] if m else "").strip()
    itype = (m[1] if len(m) > 1 else "").strip().lower() or "text"
    context = (m[2] if len(m) > 2 else "").strip()

    # forcer number -> text
    if itype == "number":
        itype = "text"


    low = _norm_lc(label)
    is_questiony = ("?" in label) or any(
        k in low
        for k in [
            "quel est",
            "quelle est",
            "what is",
            "how old",
            "postal code",
            "code postal",
            "zip",
            "age",
            "naissance",
            "year of birth",
        ]
    )

    if itype in ("text", "textarea") and (is_questiony or not label or len(label) < 2):
        # Heuristiques de valeur
        if any(k in low for k in ["postal", "code postal", "zip"]):
            label = "95000"  # 5 chiffres FR
        elif any(k in low for k in ["age", "how old"]):
            label = "28"  # adulte ok
        elif any(k in low for k in ["naissance", "year of birth"]):
            label = "1996"
        else:
            # valeur texte par  :  les  non  si champ num.
            label = "28"

    return f"{label} //// {itype} //// {context}"

# Fonction principale

# ============================================================================
# PATCH: Detection popup TopSurveys "Bon travail !" AVANT url_guard
# Ferme le popup, relance la preselection, ET execute le nouveau survey
# ============================================================================
def _handle_walr_image_eval_blocks(driver, question_blocks: list, api_key: str) -> bool:
    """
    Walr Image Evaluation: traitement spécial des questions d'évaluation d'images.
    
    Ce type de question nécessite l'envoi de l'image à OpenAI Vision pour analyse.
    Le bloc DOM contient:
      - requires_vision: True
      - image_url: URL de l'image à évaluer
      - context.walr_image_eval: True
      - target_id: pour récupérer option_xpath_map du registry
    
    Retourne True si un bloc a été traité, False sinon.
    """
    import base64
    import requests
    from Survey.dom_registry import get_target
    
    # Filtrer les blocs walr_image_eval
    vision_blocks = [
        b for b in question_blocks 
        if b.get("requires_vision") and b.get("context", {}).get("walr_image_eval")
    ]
    
    if not vision_blocks:
        return False
    
    print(f"[WALR_IMG_VISION] {len(vision_blocks)} bloc(s) image_eval détecté(s)")
    
    for block in vision_blocks:
        target_id = block.get("target_id")
        image_url = block.get("image_url")
        question = block.get("question", "Is this image positive or negative?")
        options = block.get("options", [])
        
        if not target_id or not image_url:
            print(f"[WALR_IMG_VISION] SKIP - missing target_id or image_url")
            continue
        
        # Récupérer les infos du registry (option_xpath_map)
        registry_data = get_target(target_id)
        if not registry_data:
            print(f"[WALR_IMG_VISION] SKIP - target_id {target_id} not in registry")
            continue
        
        option_xpath_map = registry_data.get("option_xpath_map", {})
        frame_chain = registry_data.get("frame_chain", [])
        
        if not option_xpath_map:
            print(f"[WALR_IMG_VISION] SKIP - no option_xpath_map for {target_id}")
            continue
        
        print(f"[WALR_IMG_VISION] Processing: question='{question[:50]}...'")
        print(f"[WALR_IMG_VISION] Options: {options}")
        print(f"[WALR_IMG_VISION] Image URL: {image_url[:80]}...")
        
        # Télécharger l'image et convertir en base64
        try:
            resp = requests.get(image_url, timeout=15)
            resp.raise_for_status()
            img_data = base64.b64encode(resp.content).decode("utf-8")
            
            # Détecter le type MIME
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            if "png" in content_type.lower():
                media_type = "image/png"
            elif "gif" in content_type.lower():
                media_type = "image/gif"
            elif "webp" in content_type.lower():
                media_type = "image/webp"
            else:
                media_type = "image/jpeg"
            
            print(f"[WALR_IMG_VISION] Image downloaded: {len(resp.content)} bytes, type={media_type}")
        except Exception as e:
            print(f"[WALR_IMG_VISION] FAILED to download image: {e}")
            continue
        
        # Construire le prompt pour Vision API
        options_str = ", ".join(f'"{opt}"' for opt in options)
        vision_prompt = f"""Analyze this image and answer the following question.

Question: {question}

Available options: {options_str}

You MUST respond with EXACTLY one of the available options, nothing else.
Just output the option text that best answers the question based on what you see in the image."""
        
        # Appel OpenAI Vision API
        try:
            client = openai.OpenAI(api_key=api_key)
            
            vision_response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{img_data}",
                                    "detail": "low"  # low detail = moins cher
                                }
                            },
                            {
                                "type": "text",
                                "text": vision_prompt
                            }
                        ]
                    }
                ],
                max_tokens=50
            )
            
            chosen_option = (vision_response.choices[0].message.content or "").strip()
            print(f"[WALR_IMG_VISION] Vision API response: '{chosen_option}'")
        except Exception as e:
            print(f"[WALR_IMG_VISION] Vision API FAILED: {e}")
            # Fallback: choisir la première option
            chosen_option = options[0] if options else ""
            print(f"[WALR_IMG_VISION] Using fallback option: '{chosen_option}'")
        
        # Normaliser et matcher l'option
        chosen_lc = _norm_lc(chosen_option)
        matched_xpath = None
        matched_option = None
        
        for opt, xpath in option_xpath_map.items():
            if _norm_lc(opt) == chosen_lc:
                matched_xpath = xpath
                matched_option = opt
                break
        
        # Si pas de match exact, essayer match partiel
        if not matched_xpath:
            for opt, xpath in option_xpath_map.items():
                opt_lc = _norm_lc(opt)
                if chosen_lc in opt_lc or opt_lc in chosen_lc:
                    matched_xpath = xpath
                    matched_option = opt
                    print(f"[WALR_IMG_VISION] Partial match: '{chosen_option}' -> '{opt}'")
                    break
        
        if not matched_xpath:
            print(f"[WALR_IMG_VISION] NO MATCH for '{chosen_option}' in options")
            # Fallback: utiliser la première option
            matched_option = list(option_xpath_map.keys())[0]
            matched_xpath = option_xpath_map[matched_option]
            print(f"[WALR_IMG_VISION] Fallback to first option: '{matched_option}'")
        
        print(f"[WALR_IMG_VISION] Clicking option '{matched_option}' via XPath: {matched_xpath}")
        
        # Naviguer vers le frame si nécessaire
        try:
            driver.switch_to.default_content()
            for frame_idx in frame_chain:
                iframes = driver.find_elements(By.CSS_SELECTOR, "iframe")
                if frame_idx < len(iframes):
                    driver.switch_to.frame(iframes[frame_idx])
        except Exception as e:
            print(f"[WALR_IMG_VISION] Frame switch error (non-fatal): {e}")
        
        # Cliquer sur le bouton
        try:
            btn = driver.find_element(By.XPATH, matched_xpath)
            
            # Scroll into view
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
            time.sleep(0.3)
            
            # Clic via ActionChains (plus fiable que .click())
            ActionChains(driver).move_to_element(btn).pause(0.1).click().perform()
            print(f"[WALR_IMG_VISION] SUCCESS - clicked '{matched_option}'")
            
            time.sleep(0.5)  # Attendre réaction
            return True
            
        except Exception as e:
            print(f"[WALR_IMG_VISION] Click FAILED: {e}")
            # Essayer JS click en fallback
            try:
                btn = driver.find_element(By.XPATH, matched_xpath)
                driver.execute_script("arguments[0].click();", btn)
                print(f"[WALR_IMG_VISION] SUCCESS via JS click")
                return True
            except Exception as e2:
                print(f"[WALR_IMG_VISION] JS click also FAILED: {e2}")
                continue
    
    return False


def _handle_topsurveys_exclusion_popup(driver) -> bool:
    """
    Detecte et ferme le popup 'Bon travail !' sur TopSurveys.
    Si detecte: ferme le popup, navigue vers le meilleur survey, et l'execute.
    Retourne True si popup traite (le nouveau survey a ete lance).
    """
    import unicodedata
    import time
    from selenium.webdriver.common.by import By
    
    try:
        url = (driver.current_url or "").lower()
        if "topsurveys.app" not in url:
            return False
    except:
        return False
    
    try:
        txt = (driver.execute_script("return document.body.innerText || ''") or "").lower()
    except:
        return False
    
    def _norm(s):
        s = s.replace("'", "'").replace("'", "'")
        s = unicodedata.normalize('NFD', s)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        return s.lower()
    
    txt_norm = _norm(txt)
    
    patterns = ["bon travail", "tu as partiellement repondu", "credite ton compte"]
    
    if not any(p in txt_norm for p in patterns):
        return False
    
    print("[TOPSURVEYS_POPUP] Popup 'Bon travail !' detecte - fermeture...")
    
    # === ETAPE 1: Fermer le popup ===
    btn = None
    try:
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-test-id='ps-common-actions-button']"))
        )
    except:
        pass
    
    if not btn:
        try:
            for b in driver.find_elements(By.CSS_SELECTOR, "button"):
                if b.is_displayed() and "compl" in _norm(b.text or ""):
                    btn = b
                    break
        except:
            pass
    
    if btn:
        try:
            driver.execute_script("arguments[0].click();", btn)
            print("[TOPSURVEYS_POPUP] Bouton 'Complete' clique.")
            time.sleep(1.0)
        except Exception as e:
            print(f"[TOPSURVEYS_POPUP] Erreur clic: {e}")
            return False
    else:
        print("[TOPSURVEYS_POPUP] Bouton non trouve.")
        return False
    
    # === ETAPE 2: Relancer la preselection vers un nouveau survey ===
    print("[TOPSURVEYS_POPUP] Relance preselection...")
    try:
        import preselection.survey_navigator as survey_navigator
        survey_navigator.go_to_best_paid_survey(driver)
        print("[TOPSURVEYS_POPUP] Navigation vers nouveau survey OK")
        time.sleep(1.0)
    except Exception as e:
        print(f"[TOPSURVEYS_POPUP] Erreur navigation: {e}")
        return False
    
    return True  # La boucle takeover continuera sur le nouveau survey

def execute_survey_page(driver, api_key):
    """
    Nouvelle version : capture , demande GPT-4o quoi faire, puis applique l'action.
    """
    import Management.guards.url_guard
    import Survey.action_dispatcher as action_dispatcher
    import selenium.webdriver.support.ui
    import Survey.dom_analyzer as dom_analyzer
    import Survey.prompt_builder as prompt_builder
    import Survey.batch_response_parser as batch_response_parser
    import Survey.dom_classifier as dom_classifier
    import Survey.action_dispatcher as action_dispatcher
    import Survey.dom_metrics as dom_metrics
    import Survey.batch_response_parser as batch_response_parser
    import Survey.input_handler as input_handler
    import Management.redirect_watcher as redirect_watcher
    import Survey.page_snapshot as page_snapshot

    # =========================================================================
    # PATCH: Detecter popup TopSurveys AVANT url_guard
    # =========================================================================
    try:
        _cur = driver.current_url
        if "topsurveys.app" in (_cur or "").lower():
            if _handle_topsurveys_exclusion_popup(driver):
                print("[TOPSURVEYS_POPUP] Popup traite -> continue boucle takeover")
                return True
    except Exception as e:
        print(f"[TOPSURVEYS_POPUP] Exception: {e}")

    try:
        cur = driver.current_url
    except Exception:
        cur = ""
    if not Management.guards.url_guard.is_allowed(cur):
        print(f"[URL_GUARD] Page hors , aucune action: {cur}")
        return False

    #  micro-: compteur rescans DOM sur CETTE page (reset  chaque page)
    try:
        driver._dom_rescans_this_page = 0
    except Exception:
        pass


    classification = dom_classifier.classify_dom(driver)

    if classification:
        itype = classification["itype"]
        handler_name = classification["handler"]
        allow_openai = classification["openai"]

        print(f"[DOM_CLASSIFIER] itype={itype} handler={handler_name} openai={allow_openai}")

        if not allow_openai:
            # handler local direct
            return getattr(action_dispatcher, handler_name)(driver)
        
    dom_metrics.log_snapshot()

    question_blocks = dom_analyzer.analyze_dom(driver)
    question_blocks = prompt_builder.filter_blocks_for_openai(question_blocks)

    # =========================================================================
    # WALR IMAGE EVALUATION: Traitement Vision API AVANT le flux standard
    # Ces questions necessitent envoi de image a OpenAI Vision pour analyse.
    # =========================================================================
    try:
        if question_blocks and _handle_walr_image_eval_blocks(driver, question_blocks, api_key):
            print("[WALR_IMG_VISION] Bloc traite avec succes -> return True")
            return True
    except Exception as e:
        print(f"[WALR_IMG_VISION] Exception: {e}")
        import traceback
        traceback.print_exc()


    #  NEW: FocusVision/Decipher cardsort (DOM-only) avant OpenAI
    try:
        from Survey.action_dispatcher import solve_focusvision_cardsort
        if solve_focusvision_cardsort(driver):
            return True
    except Exception as e:
        print(f"[CARDSORT] solver failed: {e}")

    if not question_blocks:
        #  NEW: Decipher cardrating multi-rows (DOM-only) avant vision
        try:
            from Survey.action_dispatcher import solve_decipher_cardrating_rows
            if solve_decipher_cardrating_rows(driver):
                return True
        except Exception as e:
            print(f"[CARD RATING] solver failed before vision: {e}")

    #  SNAPSHOT DEBUG (opt-in)
    try:
        page_snapshot.snapshot_if_enabled(driver, reason="after_dom_analyze", question_blocks=question_blocks)
    except Exception:
        pass

    client = openai.OpenAI(api_key=api_key)

    if question_blocks:
        prompt = prompt_builder.build_batch_prompt(question_blocks)

        instruction_raw = client.responses.create(
            input=prompt,
            model="gpt-5-nano",
        )

        raw_text = instruction_raw.output_text
        # contraintes max_select par QID (doit matcher le build_batch_prompt)
        qid_constraints = {f"Q{i}": int((b.get("max_select", 1) or 1)) for i, b in enumerate(question_blocks, start=1)}

        #  Meta par QID (pour sanitizer avec les options du DOM)
        qid_meta = {
            f"Q{i}": {
                "question": (b.get("question") or ""),
                "itype": (b.get("itype") or ""),
                "options": (b.get("options") or []),
                "max_select": int(b.get("max_select", 1) or 1),
            }
            for i, b in enumerate(question_blocks, start=1)
        }

        actions = batch_response_parser.parse_batch_response(raw_text, constraints=qid_constraints)
        actions = batch_response_parser.sanitize_actions(actions, qid_meta=qid_meta)

        #  "plan" (multi actions) + anti-double-fallback par action
        result = action_dispatcher.execute_actions_plan(driver, actions, stop_on_navigation=True)

        # --- Si on a  0 la page mais qu'on n'a pas, on tente CTA nav ---
        try:
            before_url = driver.current_url
            before_sig = redirect_watcher._dom_signature(driver)  # ou recalc local si tu veux optimiser

            # iframe-safe
            _local_pause_before_cta("navigation_cta")
            clicked = input_handler.try_click_navigation_cta_any_context(driver)

            if clicked:
                changed = redirect_watcher.wait_for_navigation_or_dom_change(
                    driver,
                    before_url=before_url,
                    before_sig=before_sig,
                    timeout=10,
                )
                if changed:
                    print(" Navigation/DOM change   CTA.")
        except Exception:
            pass

        #  Export DynamoDB : compteur unique des rescans DOM (si > 0)
        try:
            rescans = int(getattr(driver, "_dom_rescans_this_page", 0))
            if rescans:
                # (optionnel) log local 1 ligne (utile pour debug)
                print(f"[DOM_RESCAN] rescans_this_page={rescans} url={driver.current_url}")
                dom_metrics.export_dom_rescans(rescans)
        except Exception:
            pass

        return result    
    else:
        # fallback vision (existant)  mais on  le plein-page si possible (moins cher + moins de bruit)
        print(" Fallback vision (DOM insuffisant). source: survey_executor.py")

        screenshot_path = None

        # ------------------------------------------------------------
        # FALLBACK LOCAL "CTA-only" (question mais un bouton existe)
        # Objectif:  un appel vision sur des pages comme "Consent"
        # ------------------------------------------------------------
        try:
            before_url = driver.current_url
        except Exception:
            before_url = ""

        try:
            before_sig = redirect_watcher._dom_signature(driver)
        except Exception:
            before_sig = ""

        # Essayer de cliquer sur un CTA de navigation (ex: "Start Survey", "Continue", "Next", etc.)
        # PHASE 1: Fallback CSS direct (connus de boutons nav)
        # Plus fiable que la recherche par texte pour les frameworks connus
        # PHASE 2: Si pas de bouton trouvé, on peut envisager une recherche plus générique (ex: boutons avec texte "next", "continue", etc.) ou un fallback vision plein-page
        NAV_BUTTON_SELECTORS = [
            "#cm-NextButton",                    # CMIX
            ".cm-navigation-next-button",        # CMIX alt
            "#btn_continue",                     # Decipher
            "input.continue",                    # Decipher alt
            "[data-role='next']",                # Generic data-role
            "#btn_next",                         # AreYouNet (img inside <a>)
            '[data-testid="start-button"]',      # Quantilope coversheet
            "#bnNext", # Primis/Primisoft (bouton "Suivant")
        ]
        
        try:
            _local_pause_before_cta("cta_only_fallback")
            
            # Phase 1: CSS selectors directs (frameworks connus)
            print(f"[DEBUG] Phase 1: testing {len(NAV_BUTTON_SELECTORS)} selectors")
            for selector in NAV_BUTTON_SELECTORS:
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, selector)
                    print(f"[DEBUG] Selector {selector} found: {btn.tag_name}")
                    # Si c'est une image dans un lien <a>, cibler le lien parent (AreYouNet, etc.)
                    if btn.tag_name.lower() == "img":
                        try:
                            parent = btn.find_element(By.XPATH, "./..")
                            if parent.tag_name.lower() == "a":
                                btn = parent
                        except Exception:
                            pass
                    is_disp = btn.is_displayed() if btn else False
                    is_vis_js = _is_visible_js(driver, btn) if btn else False
                    print(f"[DEBUG] {selector}: is_displayed={is_disp}, _is_visible_js={is_vis_js}")
                    if btn and (is_disp or is_vis_js):                        #  que ce n'est pas un bouton "refuser/exit"
                        btn_text = (btn.text or btn.get_attribute("value") or "").lower()
                        if any(bad in btn_text for bad in ["exit", "quit", "refuse", "disagree"]):
                            continue
                        
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        driver.execute_script("arguments[0].click();", btn)
                        print(f" CTA  via  CSS: {selector}")
                        
                        try:
                            redirect_watcher.wait_for_navigation_or_dom_change(
                                driver, before_url=before_url, before_sig=before_sig, timeout=10
                            )
                        except Exception:
                            pass
                        return True
                except Exception as e:
                    print(f"[DEBUG] Selector {selector} FAILED: {type(e).__name__}")
                    continue  #  non , essayer le suivant
            
            # Phase 2: Recherche par texte (fallback existant)
            clicked = (
                input_handler.click_cta_strong_any_context(driver, text="accepter")
                or input_handler.click_cta_strong_any_context(driver, text="continuer")
                or input_handler.click_cta_strong_any_context(driver, text="accept")
                or input_handler.click_cta_strong_any_context(driver, text="agree")
                or input_handler.click_cta_strong_any_context(driver, text="next")
                or input_handler.click_cta_strong_any_context(driver, text="suivant")
                or input_handler.click_cta_strong_any_context(driver, text="dÃ©marrer")
                or input_handler.click_cta_strong_any_context(driver, text="commencer")
            )
            # Fallback direct par ID pour Qualtrics et CTA standards
            if not clicked:
                for cta_id in ["NextButton", "nextButton", "continueButton", "submitButton"]:
                    try:
                        btn = driver.find_element(By.ID, cta_id)
                        if btn.is_displayed():
                            driver.execute_script("arguments[0].click();", btn)
                            clicked = True
                            print(f"[CTA_FALLBACK] Clicked by ID: {cta_id}")
                            break
                    except Exception:
                        pass            
            if clicked:
                print(" CTA  via recherche par texte")
                try:
                    redirect_watcher.wait_for_navigation_or_dom_change(
                        driver, before_url=before_url, before_sig=before_sig, timeout=10
                    )
                except Exception:
                    pass
                return True
                
        except Exception as e:
            #  Logger l'erreur au lieu de l'avaler silencieusement
            print(f" Fallback CTA-only : {type(e).__name__}: {e}")


        #  Vision fallback = OFF par  (V1 stable)
        if not _env_truthy("SURVEY_VISION_FALLBACK", "0"):
            print(" Vision fallback  (SURVEY_VISION_FALLBACK=0) -> abandon .")
            return False

        # Import lazy:  d'embarquer screenshot_analyzer / PIL si on n'a pas explicitement  la vision
        import Survey.screenshot_analyzer as screenshot_analyzer
        # 1) Tentative screenshot  (EdgeSurvey/InnovateMR : question souvent dans img.taImage)
        try:
            img = driver.find_element(By.CSS_SELECTOR, "img.taImage")
            tmp_dir = os.path.join(tempfile.gettempdir(), "surveybot_screens")
            os.makedirs(tmp_dir, exist_ok=True)
            screenshot_path = os.path.join(tmp_dir, f"taImage_{int(time.time()*1000)}.png")
            img.screenshot(screenshot_path)
            print(f" Screenshot  (img.taImage) -> {screenshot_path}")
        except Exception:
            screenshot_path = None

        # 2) Fallback viewport (moins lourd que full_page) puis full_page en dernier recours
        if not screenshot_path:
            print(" Screenshot viewport (pas full-page). source: survey_executor.py")
            try:
                screenshot_path = screenshot_analyzer.take_screenshot(driver, full_page=False)
            except Exception:
                screenshot_path = screenshot_analyzer.take_screenshot(driver, full_page=True)

        print(" Envoi  GPT pour  visuelle. source: survey_executor.py line 59")
        instruction = screenshot_analyzer.send_image_to_gpt(screenshot_path, api_key)

        #  UTILISATION, juste  avoir  la  du  (variable `instruction`)
        #    et avant de la renvoyer   :
        lines = [ln for ln in (instruction or "").splitlines() if ln.strip()]
        fixed_lines = [_coerce_safe_value_if_questionish(ln) for ln in lines]
        instruction = "\n".join(fixed_lines)
        #print(" Instruction  ( dans le fixed_lines) :", instruction, " source: survey_executor.py")

        # --- Ne conserver que la  ligne non vide ---
        if instruction:
            instruction = next(
                (ln.strip() for ln in instruction.splitlines() if ln.strip()), ""
            )

        print(
            " Instruction  () :",
            instruction,
            " source: survey_executor.py line 67",
        )

        try:
            success = action_dispatcher.execute_action(driver, instruction)
            if not success:
                print(
                    " Aucune action  par le dispatcher. source: survey_executor.py"
                )
            return success
        except Exception as e:
            print(
                " Erreur dans  de   sur GPT; source: survey_executor.py",
            )
            return False

def extract_full_visible_text(driver):
    """
    Extrait tout le texte visible de la page, en ignorant les balises de type lien, script, style, header, etc.
    """
    js = """
    return Array.from(document.querySelectorAll('body *'))
      .filter(e => {
          const style = window.getComputedStyle(e);
          const tag = e.tagName.toLowerCase();
          const ignored = ['a', 'footer', 'header', 'nav', 'script', 'style'];
          return style && style.display !== 'none' &&
                 style.visibility !== 'hidden' &&
                 e.offsetParent !== null &&
                 !ignored.includes(tag);
      })
      .map(e => e.innerText)
      .filter(t => t && t.trim().length > 5)
      .map(t => t.trim());
    """

    try:
        result = driver.execute_script(js)
        return list(dict.fromkeys(result))  # supprimer les doublons
    except Exception as e:
        print(" JS extraction erreur:", e, "survey_executor.py line 251")
        return []

#  Sous-fonction : appliquer une action  par l'IA

def perform_action_based_on_text(driver, action):
    """
    Essaie de cliquer sur un bouton ou un label qui correspond  l'action textuelle de l'IA.
    """
    buttons = (
        driver.find_elements(By.TAG_NAME, "button")
        + driver.find_elements(By.TAG_NAME, "input")
        + driver.find_elements(By.TAG_NAME, "a")
    )

    for elem in buttons:
        try:
            label = elem.get_attribute("value") or elem.text
            if not label:
                spans = elem.find_elements(By.TAG_NAME, "span")
                for span in spans:
                    if span.text.strip():
                        label = span.text.strip()
                        break
            if label and action.lower() in label.lower():
                ActionChains(driver).move_to_element(elem).click().perform()
                print(
                    f" Action '{action}'  sur l' : {label} survey_executor.py line 274"
                )
                time.sleep(2)
                return True
        except:
            continue

    print(
        f" Aucun  ne correspond  l'action source: survey_executor.py line 280"
    )
    return False

def _page_fingerprint(driver) -> str:
    url = driver.current_url or ""
    # cheap: titre + un bout de body text
    title = driver.title or ""
    body = ""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text[:2000]
    except Exception:
        pass
    raw = f"{url}\n{title}\n{body}"
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()