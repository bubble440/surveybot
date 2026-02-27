"""
normal_captcha.py — Détection et résolution des CAPTCHA image-texte normaux.

Flow:
  1. detect_normal_captcha(driver)  → repère un <img>/<canvas> CAPTCHA + input réponse
  2. solve_normal_captcha(b64)      → envoie à 2Captcha (ImageToTextTask), retourne le texte
  3. handle_captcha(driver)         → orchestrateur complet (détect → résout → saisit → soumet)
                                      retourne True si un CAPTCHA a été traité, False sinon (no-op)
"""
import time
from selenium.webdriver.common.by import By

# Mots-clés qui signalent un élément CAPTCHA dans ses attributs DOM
_CAPTCHA_ATTRS = ("id", "class", "alt", "name", "aria-label")
_CAPTCHA_KEYWORDS = frozenset({"captcha", "cap_img", "securecode", "security-code", "verif-img"})


# ──────────────────────────────────────────────────────────────────────────────
# Détection
# ──────────────────────────────────────────────────────────────────────────────

def _el_looks_like_captcha(el) -> bool:
    """True si l'élément ressemble à une image CAPTCHA (basé sur ses attributs)."""
    for attr in _CAPTCHA_ATTRS:
        try:
            val = (el.get_attribute(attr) or "").lower()
            if any(kw in val for kw in _CAPTCHA_KEYWORDS):
                return True
        except Exception:
            pass
    try:
        src = (el.get_attribute("src") or "").lower()
        if "captcha" in src:
            return True
    except Exception:
        pass
    return False


def detect_normal_captcha(driver) -> dict | None:
    """
    Inspecte le DOM pour un CAPTCHA image-texte normal.

    Stratégie :
      - Cherche un <img> ou <canvas> dont les attributs contiennent "captcha"
      - Cherche un <input type="text"> associé (dans le même conteneur)

    Retourne un dict {"img_el": el, "input_el": el} ou None si rien trouvé.
    """
    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, "img, canvas")
        captcha_img = None
        for el in candidates:
            try:
                if not el.is_displayed():
                    continue
            except Exception:
                continue
            if _el_looks_like_captcha(el):
                captcha_img = el
                break

        if captcha_img is None:
            return None

        # Cherche le champ de saisie de la réponse — d'abord dans le même conteneur
        input_el = driver.execute_script(
            """
            var img = arguments[0];
            var el = img;
            for (var i = 0; i < 8; i++) {
                el = el.parentElement;
                if (!el) break;
                var inp = el.querySelector(
                    "input[type='text'], input[type='tel'], input:not([type]), input[type='']"
                );
                if (inp) return inp;
            }
            return null;
            """,
            captcha_img,
        )

        if input_el is None:
            # Fallback : premier <input type="text"> visible sur la page
            for inp in driver.find_elements(
                By.CSS_SELECTOR, "input[type='text'], input[type='tel'], input:not([type])"
            ):
                try:
                    if inp.is_displayed():
                        input_el = inp
                        break
                except Exception:
                    continue

        if input_el is None:
            print("[CAPTCHA][DETECT] Image CAPTCHA trouvée mais aucun champ de saisie — skip")
            return None

        return {"img_el": captcha_img, "input_el": input_el}

    except Exception as exc:
        print(f"[CAPTCHA][DETECT] Erreur inattendue : {exc}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Résolution via 2Captcha
# ──────────────────────────────────────────────────────────────────────────────

def solve_normal_captcha(image_base64: str) -> str:
    """
    Envoie l'image CAPTCHA (base64) à 2Captcha via ImageToTextTask.
    Retourne le texte solution.

    Raises RuntimeError / TimeoutError en cas d'échec.
    """
    from captcha.captcha_solver import TwoCaptchaClient
    client = TwoCaptchaClient()
    if not client.api_key:
        raise RuntimeError(
            "Clé 2Captcha manquante — configurez CAPTCHA_API_KEY ou TWO_CAPTCHA_KEY"
        )
    return client.solve_image_to_text(image_base64)


# ──────────────────────────────────────────────────────────────────────────────
# Soumission
# ──────────────────────────────────────────────────────────────────────────────

def _try_submit_captcha_form(driver, input_el) -> bool:
    """
    Cherche le bouton de validation CAPTCHA le plus proche et le clique.
    Retourne True si un bouton a été cliqué.
    """
    btn = driver.execute_script(
        """
        var inp = arguments[0];
        // 1) bouton de soumission dans le même <form>
        var form = inp.closest('form');
        if (form) {
            var b = form.querySelector(
                "button[type='submit'], input[type='submit'], button:not([type='button'])"
            );
            if (b) return b;
        }
        // 2) bouton dans les ancêtres directs
        var el = inp;
        for (var i = 0; i < 6; i++) {
            el = el.parentElement;
            if (!el) break;
            var btns = Array.from(el.querySelectorAll("button, input[type='submit']"));
            for (var b of btns) {
                if (b !== inp) return b;
            }
        }
        return null;
        """,
        input_el,
    )
    if btn is None:
        return False
    try:
        if btn.is_displayed():
            driver.execute_script("arguments[0].click();", btn)
            return True
    except Exception:
        pass
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrateur principal
# ──────────────────────────────────────────────────────────────────────────────

def handle_captcha(driver) -> bool:
    """
    Détecte, résout et soumet un CAPTCHA image-texte normal.

    Retourne True  si un CAPTCHA a été trouvé et traité (succès ou non).
    Retourne False si aucun CAPTCHA n'est présent (no-op total).

    Conçu pour être appelé en début de chaque itération de boucle survey :
    si aucun CAPTCHA n'est présent, le coût est quasi nul (quelques find_elements).
    """
    info = detect_normal_captcha(driver)
    if info is None:
        return False

    img_el = info["img_el"]
    input_el = info["input_el"]

    print("[CAPTCHA] CAPTCHA image-texte détecté — résolution via 2Captcha…")

    # 1) Capture de l'image CAPTCHA en base64
    try:
        image_b64 = img_el.screenshot_as_base64
    except Exception as exc:
        print(f"[CAPTCHA] Impossible de capturer l'image CAPTCHA : {exc}")
        return True  # CAPTCHA détecté mais non résolu — signaler quand même

    # 2) Résolution 2Captcha (ImageToTextTask)
    try:
        solution = solve_normal_captcha(image_b64)
        print(f"[CAPTCHA] Solution reçue : {solution!r}")
    except Exception as exc:
        print(f"[CAPTCHA] Échec de résolution 2Captcha : {exc}")
        return True

    # 3) Saisie de la solution dans le champ texte
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", input_el
        )
        time.sleep(0.2)
        input_el.clear()
        input_el.send_keys(solution)
        print("[CAPTCHA] Solution saisie dans le champ.")
    except Exception as exc:
        print(f"[CAPTCHA] Impossible de saisir la solution : {exc}")
        return True

    # 4) Soumission (best-effort — si aucun bouton trouvé, le flux normal continue)
    try:
        submitted = _try_submit_captcha_form(driver, input_el)
        if submitted:
            print("[CAPTCHA] Formulaire CAPTCHA soumis.")
            time.sleep(1.0)  # laisser le DOM se stabiliser
        else:
            print("[CAPTCHA] Aucun bouton de soumission trouvé — le flux normal gère la suite.")
    except Exception as exc:
        print(f"[CAPTCHA][WARN] Soumission échouée (non bloquant) : {exc}")

    return True
