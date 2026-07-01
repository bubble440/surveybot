"""
normal_captcha.py — Détection et résolution des CAPTCHA image-texte normaux.

Flow:
  1. detect_normal_captcha(driver)  → repère un <img>/<canvas> CAPTCHA + input réponse
  2. solve_normal_captcha(b64)      → envoie à 2Captcha (ImageToTextTask), retourne le texte
  3. handle_captcha(driver)         → orchestrateur complet (détect → résout → saisit → soumet)
                                      retourne True si un CAPTCHA a été traité, False sinon (no-op)
"""
import base64
import time

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
        candidates = driver.query_selector_all("img, canvas")
        captcha_img = None
        for el in candidates:
            try:
                if not el.is_visible():
                    continue
            except Exception:
                continue
            if _el_looks_like_captcha(el):
                captcha_img = el
                break

        if captcha_img is None:
            return None

        # Cherche le champ de saisie de la réponse — d'abord dans le même conteneur
        # evaluate_handle retourne un JSHandle ; as_element() donne un ElementHandle utilisable
        # PATCH: la parenthèse fermante de l'arrow function "(e => { ... })" manquait,
        # ce qui produisait un JS syntaxiquement invalide (SyntaxError: Unexpected end
        # of input) silencieusement avalé par le except englobant → faux négatif de
        # détection. Chaîne reconstruite avec parenthésage explicite et correct.
        input_handle = captcha_img.evaluate_handle(
            "(e => {"
            " let n = e;"
            " for (let i = 0; i < 8; i++) {"
            "  n = n.parentElement;"
            "  if (!n) break;"
            "  const inp = n.querySelector(\"input[type='text'], input[type='tel'],"
            " input:not([type]), input[type='']\");"
            "  if (inp) return inp;"
            " }"
            " return null;"
            "})"
        )
        input_el = input_handle.as_element() if input_handle else None

        if input_el is None:
            # Fallback : premier <input type="text"> visible sur la page
            for inp in driver.query_selector_all(
                "input[type='text'], input[type='tel'], input:not([type])"
            ):
                try:
                    if inp.is_visible():
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
# Orchestrateur principal
# ──────────────────────────────────────────────────────────────────────────────

def handle_captcha(driver) -> bool:
    """
    Détecte, résout et soumet un CAPTCHA image-texte normal.

    Retourne True  si un CAPTCHA a été trouvé et traité (succès ou non).
    Retourne False si aucun CAPTCHA n'est présent (no-op total).

    Conçu pour être appelé en début de chaque itération de boucle survey :
    si aucun CAPTCHA n'est présent, le coût est quasi nul (quelques find_elements).

    Saisie via Survey.input_text.fill_text_input  (même logique que les questions normales).
    CTA    via Survey.cta_handler.try_click_navigation_cta_any_context (même logique que nav).
    """
    info = detect_normal_captcha(driver)
    if info is None:
        return False

    img_el = info["img_el"]

    print("[CAPTCHA] CAPTCHA image-texte détecté — résolution via 2Captcha…")

    # 1) Capture de l'image CAPTCHA en base64 (screenshot de l'élément DOM)
    try:
        image_b64 = base64.b64encode(img_el.screenshot()).decode()
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

    # 3) Saisie via fill_text_input — même gestionnaire que les questions texte normales.
    #    context_hint="captcha" active le chemin rapide dédié aux CAPTCHAs (ex. PureSpectrum)
    #    et assure scroll + focus + clear + fallback JS/React.
    try:
        from Survey.input_text import fill_text_input
        filled = fill_text_input(driver, solution, context_hint="captcha")
        if filled:
            print("[CAPTCHA] Solution saisie via fill_text_input.")
        else:
            print("[CAPTCHA][WARN] fill_text_input n'a pas trouvé de champ à remplir.")
    except Exception as exc:
        print(f"[CAPTCHA] Saisie via fill_text_input échouée : {exc}")
        return True

    # 4) Clic CTA via try_click_navigation_cta_any_context — même gestionnaire que les boutons
    #    de navigation sondage ; explore le contenu principal et les iframes.
    try:
        from Survey.cta_handler import try_click_navigation_cta_any_context
        clicked = try_click_navigation_cta_any_context(driver)
        if clicked:
            print("[CAPTCHA] CTA soumis via try_click_navigation_cta_any_context.")
            time.sleep(1.0)  # laisser le DOM se stabiliser après navigation
        else:
            print("[CAPTCHA] Aucun CTA de navigation trouvé — le flux normal gère la suite.")
    except Exception as exc:
        print(f"[CAPTCHA][WARN] Clic CTA échoué (non bloquant) : {exc}")

    return True