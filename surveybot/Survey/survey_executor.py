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
    LOCAL ONLY: attend que l'utilisateur appuie sur EntrÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e avant de cliquer un CTA.
    ProtÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨ge prod/docker: ne bloque jamais si stdin non-interactif.
    Active uniquement si LOCAL_CTA_REQUIRE_ENTER=1.
    """
    try:
        if (os.getenv("RUN_ENV", "local") or "").strip().lower() != "local":
            return
        if not _env_truthy("LOCAL_CTA_REQUIRE_ENTER", "0"):
            return
        if not getattr(sys.stdin, "isatty", lambda: False)():
            # ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©vite de bloquer CI / docker / logs non interactifs
            return

        msg = "[LOCAL][PAUSE] Appuie sur EntrÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e pour autoriser le clic CTA"
        if reason:
            msg += f" ({reason})"
        print(msg, flush=True)
        try:
            input()
        except KeyboardInterrupt:
            # abandon contrÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â´lÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© : on ne clique pas
            raise
    except Exception:
        # best-effort : ne jamais casser un run parce que le pause-mode bug
        return

def _is_visible_js(driver, el) -> bool:
    """
    Fallback JavaScript pour vÃƒÆ’Ã‚Â©rifier la visibilitÃƒÆ’Ã‚Â© d'un ÃƒÆ’Ã‚Â©lÃƒÆ’Ã‚Â©ment.
    UtilisÃƒÆ’Ã‚Â© quand Selenium.is_displayed() retourne False sur des structures DOM
    complexes (tables imbriquÃƒÆ’Ã‚Â©es AreYouNet, etc.) alors que l'ÃƒÆ’Ã‚Â©lÃƒÆ’Ã‚Â©ment est visible.
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
    Si le modÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨le renvoie par erreur un intitulÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© de question au lieu d'une valeur,
    fabrique une valeur sÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â»re en fonction du texte.
    Remappe aussi 'number' -> 'text'.
    """
    line = (raw_line or "").strip()
    # parse "label //// type //// contexte" tolÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©rant
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
            "ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ge",
            "age",
            "annÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e",
            "naissance",
            "year of birth",
        ]
    )

    if itype in ("text", "textarea") and (is_questiony or not label or len(label) < 2):
        # Heuristiques de valeur sÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â»re
        if any(k in low for k in ["postal", "code postal", "zip"]):
            label = "95000"  # 5 chiffres FR
        elif any(k in low for k in ["ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ge", "age", "how old"]):
            label = "28"  # adulte ok
        elif any(k in low for k in ["annÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e", "naissance", "year of birth"]):
            label = "1996"
        else:
            # valeur texte par dÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©faut : ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©vite les caractÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨res non numÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©riques si champ num.
            label = "28"

    return f"{label} //// {itype} //// {context}"

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“Ãƒâ€šÃ‚ÂÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚Â Fonction principale
def execute_survey_page(driver, api_key):
    """
    Nouvelle version : capture lÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢image, demande ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â  GPT-4o quoi faire, puis applique l'action.
    """
    import Management.guards.url_guard
    # import Survey.screenshot_analyzer as screenshot_analyzer
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

    # # ÃƒÆ’Ã‚Â¢Ãƒâ€šÃ‚ÂÃƒâ€šÃ‚Â³ Attente que le DOM ait fini de charger avant capture
    # try:
    #     selenium.webdriver.support.ui.WebDriverWait(driver, 8).until(
    #         lambda d: d.execute_script("return document.readyState") == "complete"
    #     )
    #     selenium.webdriver.support.ui.WebDriverWait(driver, 8).until(
    #         lambda d: len(d.find_elements(By.CSS_SELECTOR,
    #             "input, select, textarea, button, [role='button'], [role='radio'], [role='checkbox']"
    #         )) > 0
    #     )
    # except Exception:
    #     print("ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã‚Â¡Ãƒâ€šÃ‚Â ÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚Â Page encore vide, tentative de capture malgrÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© tout.")

    # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂºÃƒâ€šÃ‚Â¡ÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚Â Garde-fou URL: si on est hors pÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©rimÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨tre, on n'agit pas
    try:
        cur = driver.current_url
    except Exception:
        cur = ""
    if not Management.guards.url_guard.is_allowed(cur):
        print(f"[URL_GUARD] Page hors pÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©rimÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨tre, aucune action: {cur}")
        return False

    # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€¹Ã¢â‚¬Â  micro-mÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©trique: compteur rescans DOM sur CETTE page (reset ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â  chaque page)
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

    # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ NEW: FocusVision/Decipher cardsort (DOM-only) avant OpenAI
    try:
        from Survey.action_dispatcher import solve_focusvision_cardsort
        if solve_focusvision_cardsort(driver):
            return True
    except Exception as e:
        print(f"[CARDSORT] solver failed: {e}")

    if not question_blocks:
        # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ NEW: Decipher cardrating multi-rows (DOM-only) avant vision
        try:
            from Survey.action_dispatcher import solve_decipher_cardrating_rows
            if solve_decipher_cardrating_rows(driver):
                return True
        except Exception as e:
            print(f"[CARD RATING] solver failed before vision: {e}")

    # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ SNAPSHOT DEBUG (opt-in)
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

        # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ Meta par QID (pour sanitizer avec les options du DOM)
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

        # exÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©cution "plan" (multi actions) + anti-double-fallback par action
        result = action_dispatcher.execute_actions_plan(driver, actions, stop_on_navigation=True)

        # --- Si on a rÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©pondu ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â  la page mais qu'on n'a pas bougÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©, on tente CTA nav ---
        try:
            before_url = driver.current_url
            before_sig = redirect_watcher._dom_signature(driver)  # ou recalc local si tu prÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©fÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨res

            # iframe-safe recommandÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©
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
                    print("ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ Navigation/DOM change dÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©tectÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© aprÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨s CTA.")
        except Exception:
            pass

        # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€¹Ã¢â‚¬Â  Export DynamoDB : compteur unique des rescans DOM (si > 0)
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
        # fallback vision (existant) ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â mais on ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©vite le plein-page si possible (moins cher + moins de bruit)
        print("ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€šÃ‚Â¸ Fallback vision (DOM insuffisant). source: survey_executor.py")

        screenshot_path = None

        # ------------------------------------------------------------
        # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ FALLBACK LOCAL "CTA-only" (zÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©ro question mais un bouton existe)
        # Objectif: ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©viter un appel vision coÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â»teux sur des pages comme "Consent"
        # ------------------------------------------------------------
        try:
            before_url = driver.current_url
        except Exception:
            before_url = ""

        try:
            before_sig = redirect_watcher._dom_signature(driver)
        except Exception:
            before_sig = ""

        # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬
        # PHASE 1: Fallback CSS direct (sÃƒÆ’Ã‚Â©lecteurs connus de boutons nav)
        # Plus fiable que la recherche par texte pour les frameworks connus
        # ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬
        NAV_BUTTON_SELECTORS = [
            "#cm-NextButton",                    # CMIX
            ".cm-navigation-next-button",        # CMIX alt
            "#btn_continue",                     # Decipher
            "input.continue",                    # Decipher alt
            "[data-role='next']",                # Generic data-role
            "#btn_next",                         # AreYouNet (img inside <a>)
        ]
        
        try:
            _local_pause_before_cta("cta_only_fallback")
            
            # Phase 1: CSS selectors directs (frameworks connus)
            for selector in NAV_BUTTON_SELECTORS:
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, selector)
                    # Si c'est une image dans un lien <a>, cibler le lien parent (AreYouNet, etc.)
                    if btn.tag_name.lower() == "img":
                        try:
                            parent = btn.find_element(By.XPATH, "./..")
                            if parent.tag_name.lower() == "a":
                                btn = parent
                        except Exception:
                            pass
                    if btn and (btn.is_displayed() or _is_visible_js(driver, btn)):                        # VÃƒÆ’Ã‚Â©rifier que ce n'est pas un bouton "refuser/exit"
                        btn_text = (btn.text or btn.get_attribute("value") or "").lower()
                        if any(bad in btn_text for bad in ["exit", "quit", "refuse", "disagree"]):
                            continue
                        
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        driver.execute_script("arguments[0].click();", btn)
                        print(f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ CTA cliquÃƒÆ’Ã‚Â© via sÃƒÆ’Ã‚Â©lecteur CSS: {selector}")
                        
                        try:
                            redirect_watcher.wait_for_navigation_or_dom_change(
                                driver, before_url=before_url, before_sig=before_sig, timeout=10
                            )
                        except Exception:
                            pass
                        return True
                except Exception:
                    continue  # SÃƒÆ’Ã‚Â©lecteur non trouvÃƒÆ’Ã‚Â©, essayer le suivant
            
            # Phase 2: Recherche par texte (fallback existant)
            clicked = (
                input_handler.click_cta_strong_any_context(driver, text="accepter")
                or input_handler.click_cta_strong_any_context(driver, text="continuer")
                or input_handler.click_cta_strong_any_context(driver, text="accept")
                or input_handler.click_cta_strong_any_context(driver, text="agree")
                or input_handler.click_cta_strong_any_context(driver, text="next")
                or input_handler.click_cta_strong_any_context(driver, text="suivant")
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
                print("ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ CTA cliquÃƒÆ’Ã‚Â© via recherche par texte")
                try:
                    redirect_watcher.wait_for_navigation_or_dom_change(
                        driver, before_url=before_url, before_sig=before_sig, timeout=10
                    )
                except Exception:
                    pass
                return True
                
        except Exception as e:
            # ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Logger l'erreur au lieu de l'avaler silencieusement
            print(f"ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Fallback CTA-only ÃƒÆ’Ã‚Â©chouÃƒÆ’Ã‚Â©: {type(e).__name__}: {e}")


        # ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã‚ÂÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ Vision fallback = OFF par dÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©faut (V1 stable)
        if not _env_truthy("SURVEY_VISION_FALLBACK", "0"):
            print("ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸Ãƒâ€¦Ã‚Â¸Ãƒâ€šÃ‚Â£ Vision fallback dÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©sactivÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© (SURVEY_VISION_FALLBACK=0) -> abandon contrÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â´lÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©.")
            return False

        # Import lazy: ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©vite d'embarquer screenshot_analyzer / PIL si on n'a pas explicitement activÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© la vision
        import Survey.screenshot_analyzer as screenshot_analyzer
        # 1) Tentative screenshot ciblÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© (EdgeSurvey/InnovateMR : question souvent dans img.taImage)
        try:
            img = driver.find_element(By.CSS_SELECTOR, "img.taImage")
            tmp_dir = os.path.join(tempfile.gettempdir(), "surveybot_screens")
            os.makedirs(tmp_dir, exist_ok=True)
            screenshot_path = os.path.join(tmp_dir, f"taImage_{int(time.time()*1000)}.png")
            img.screenshot(screenshot_path)
            print(f"ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€šÃ‚Â¸ Screenshot ciblÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© (img.taImage) -> {screenshot_path}")
        except Exception:
            screenshot_path = None

        # 2) Fallback viewport (moins lourd que full_page) puis full_page en dernier recours
        if not screenshot_path:
            print("ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€šÃ‚Â¸ Screenshot viewport (pas full-page). source: survey_executor.py")
            try:
                screenshot_path = screenshot_analyzer.take_screenshot(driver, full_page=False)
            except Exception:
                screenshot_path = screenshot_analyzer.take_screenshot(driver, full_page=True)

        print("ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸Ãƒâ€šÃ‚Â¤ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“ Envoi ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â  GPT pour interprÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©tation visuelle. source: survey_executor.py line 59")
        instruction = screenshot_analyzer.send_image_to_gpt(screenshot_path, api_key)

        # ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã‚Â¾Ãƒâ€¦Ã¢â‚¬Å“ UTILISATION, juste aprÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨s avoir reÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â§u la rÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©ponse du modÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨le (variable `instruction`)
        #    et avant de la renvoyer ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â  lÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢exÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©cuteur :
        lines = [ln for ln in (instruction or "").splitlines() if ln.strip()]
        fixed_lines = [_coerce_safe_value_if_questionish(ln) for ln in lines]
        instruction = "\n".join(fixed_lines)
        #print("ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€šÃ‚Â¥ Instruction reÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â§ue (nettoyÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e dans le fixed_lines) :", instruction, " source: survey_executor.py")

        # --- Ne conserver que la 1ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¨re ligne non vide ---
        if instruction:
            instruction = next(
                (ln.strip() for ln in instruction.splitlines() if ln.strip()), ""
            )

        print(
            "ÃƒÆ’Ã‚Â°Ãƒâ€¦Ã‚Â¸ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€šÃ‚Â¥ Instruction reÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â§ue (nettoyÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e) :",
            instruction,
            " source: survey_executor.py line 67",
        )

        try:
            success = action_dispatcher.execute_action(driver, instruction)
            if not success:
                print(
                    "ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¾Ãƒâ€šÃ‚Â¹ÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚Â Aucune action appliquÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e par le dispatcher. source: survey_executor.py"
                )
            return success
        except Exception as e:
            print(
                "ÃƒÆ’Ã‚Â¢Ãƒâ€šÃ‚ÂÃƒâ€¦Ã¢â‚¬â„¢ Erreur dans lÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢exÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©cution de lÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢action basÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e sur GPT; source: survey_executor.py",
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
        print("ÃƒÆ’Ã‚Â¢Ãƒâ€šÃ‚ÂÃƒâ€¦Ã¢â‚¬â„¢ JS extraction erreur:", e, "survey_executor.py line 251")
        return []

# ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã‚Â¡ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“ÃƒÆ’Ã‚Â¯Ãƒâ€šÃ‚Â¸Ãƒâ€šÃ‚Â Sous-fonction : appliquer une action recommandÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e par l'IA

def perform_action_based_on_text(driver, action):
    """
    Essaie de cliquer sur un bouton ou un label qui correspond ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â  l'action textuelle de l'IA.
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
                    f"ÃƒÆ’Ã‚Â¢Ãƒâ€¦Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ Action '{action}' exÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©cutÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©e sur l'ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©lÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©ment : {label} survey_executor.py line 274"
                )
                time.sleep(2)
                return True
        except:
            continue

    print(
        f"ÃƒÆ’Ã‚Â¢Ãƒâ€šÃ‚ÂÃƒâ€¦Ã¢â‚¬â„¢ Aucun ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©lÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â©ment ne correspond ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â  l'action source: survey_executor.py line 280"
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