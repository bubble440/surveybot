# recaptcha_handler.py
import time

from captcha.recaptcha_utils import extract_recaptcha_v2_sitekey, inject_recaptcha_token
from captcha.captcha_solver import TwoCaptchaClient
import Management.guards.survey_difficulty_guard


def solve_recaptcha_v2_auto(driver) -> bool:
    """
    Tente de résoudre automatiquement un reCAPTCHA v2 via 2Captcha.
    Retourne True si résolu, False si sitekey introuvable / erreur / timeout.
    1 seule tentative — pas de retry.
    """
    # 1. Extraire le sitekey
    sitekey, invisible = extract_recaptcha_v2_sitekey(driver)
    if not sitekey:
        print("[RECAPTCHA_HANDLER] sitekey introuvable")
        return False

    inv_label = "invisible" if invisible else "visible"
    print(f"[RECAPTCHA_HANDLER] sitekey extrait : {sitekey} ({inv_label})")

    # 2. Résoudre via 2Captcha
    current_url = driver.current_url
    print(f"[RECAPTCHA_HANDLER] Envoi à 2Captcha (url={current_url})")
    try:
        client = TwoCaptchaClient()
        token = client.solve_recaptcha_v2(sitekey, current_url, invisible)
    except TimeoutError as e:
        print(f"[RECAPTCHA_HANDLER] Timeout 2Captcha : {e}")
        return False
    except Exception as e:
        print(f"[RECAPTCHA_HANDLER] Erreur 2Captcha : {e}")
        return False

    if not token:
        print("[RECAPTCHA_HANDLER] Token vide reçu de 2Captcha")
        return False

    # 3. Injecter le token
    print("[RECAPTCHA_HANDLER] Token reçu, injection...")
    try:
        inject_recaptcha_token(driver, token)
    except Exception as e:
        print(f"[RECAPTCHA_HANDLER] Erreur lors de l'injection du token : {e}")
        return False

    # 4. Déclencher le callback JS reCAPTCHA si présent
    try:
        driver.execute_script("""
          try {
            const resp = document.getElementById('g-recaptcha-response');
            if (resp && window.___grecaptcha_cfg) {
              const clients = Object.values(window.___grecaptcha_cfg.clients || {});
              for (const c of clients) {
                const cb = c?.l?.callback || c?.o?.callback || c?.R?.callback;
                if (typeof cb === 'function') { cb(arguments[0]); break; }
              }
            }
          } catch(e) {}
        """, token)
    except Exception as e:
        print(f"[RECAPTCHA_HANDLER] Avertissement callback JS : {e}")
        # non-bloquant

    # 5. Attendre max 10s que le captcha disparaisse (poll toutes les 1.5s)
    start = time.time()
    deadline = start + 10.0
    resolved = False
    while time.time() < deadline:
        try:
            still_strict, still_reason = Management.guards.survey_difficulty_guard.detect_strict_survey(driver)
            if not still_strict or still_reason != "captcha":
                resolved = True
                break
        except Exception:
            pass
        time.sleep(1.5)

    elapsed = round(time.time() - start, 1)
    if resolved:
        print(f"[RECAPTCHA_HANDLER] ✅ Captcha résolu en {elapsed}s")
        return True
    else:
        print(f"[RECAPTCHA_HANDLER] ❌ Captcha toujours présent après {elapsed}s (timeout)")
        return False
