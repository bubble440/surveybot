# survey_solver.py
# Orchestration minimaliste et robuste pour enchaîner les actions de page
# ➜ Laisse l'intelligence d'action à survey_executor.execute_survey_page()

import time, os
from Survey.log_utils import log_debug, log_info

# SNAP_ENABLED est une variable GLOBAL_CONFIG : en build compilé (Nuitka), elle provient
# exclusivement de global_config.py, jamais de l'environnement du process (cf. config.py).
# En dev/attach (global_config.py absent du projet), fallback os.getenv.
try:
    from global_config import SNAP_ENABLED  # type: ignore
except ImportError:
    SNAP_ENABLED = os.getenv("SNAP_ENABLED", "")





def _wait_for_url_stable(page, max_wait: int = 30) -> str:
    """
    Replacement natif Playwright de redirect_watcher.wait_for_final_redirection().
    Attend que l'URL se stabilise (3 vérifications consécutives identiques à 5s d'intervalle).
    """
    last_url = page.url
    start = time.time()
    stable_count = 0
    while time.time() - start < max_wait:
        time.sleep(5)
        current_url = page.url
        if current_url != last_url:
            print(f"🔀 Redirection détectée : {last_url} -> {current_url}")
            last_url = current_url
            stable_count = 0
        else:
            stable_count += 1
            if stable_count >= 3:
                print(f"✅ URL stabilisée : {current_url}")
                return current_url
    print(f"⏱️ _wait_for_url_stable: timeout {max_wait}s, URL courante : {page.url}")
    return page.url


class TopSurveysReturn(BaseException):
    """Sentinelle levée quand handle_post_survey() signale un retour sur la plateforme.
    Hérite de BaseException pour traverser les blocs except Exception sans être avalée.
    Interceptée dans survey_handler pour reboucler sur la préselection.
    """


STABILIZE_SLEEP = 2.0       # délai court entre deux actions pour laisser le DOM respirer
PAUSE_BEFORE_FIRST_SCAN = 1.5  # post-chargement, avant le premier scan DOM (absorbe latence proxy)
PAUSE_POST_CTA_NAV = 2.0       # après navigation CTA, avant toute interaction avec la nouvelle page


def _publish_live_page(pg):
    """
    🔎 FIX (même famille que survey_handler.py::_resync_live_page) : quand ce module
    bascule en interne sur une nouvelle Page (onglet externe du survey, focus multi-
    onglets), l'appelant (survey_handler.py) ne récupère jamais cette nouvelle page —
    l'appel à solve_full_survey() ignore sa valeur de retour, et le chemin
    `except TopSurveysReturn: continue` relance la boucle avec le `driver` d'avant
    l'appel. On republie donc systématiquement vers le RuntimeGuard (source de vérité
    globale) à chaque switch, pour que soft_restart et les guards internes utilisent
    toujours la page réellement vivante.
    """
    try:
        from Management.guards.runtime_guard import get_guard
        get_guard().attach_driver(pg)
    except Exception:
        pass


def _switch_to_external_tab(driver, platform):
    """
    Identifie l'onglet du survey (non-plateforme) parmi tous les onglets du contexte CDP.
    Retourne la Page externe, ou None si non trouvée.
    Met aussi à jour driver._page si driver est un shim (compat prod path).
    """
    time.sleep(3)
    page = driver
    context = page.context
    domains = platform.get_domains()
    for pg in context.pages:
        url = (pg.url or "").lower()
        if not any(d in url for d in domains):
            print(f"🧭 Onglet externe détecté : {pg.url}")
            if hasattr(driver, "_page"):
                driver._page = pg
                driver._current_frame = pg
            _publish_live_page(pg)
            return pg
    print("⚠ Aucun onglet externe détecté. Reste sur la plateforme.")
    return None


def count_actionable_elements(driver) -> int:
    """
    Compte rapidement les éléments actionnables visibles sur la page.
    Sert à savoir s'il reste 'beaucoup' d'inputs (évite d'envoyer prev inutilement).
    """
    page = driver
    total = 0
    try:
        sels = [
            "input[type='radio']",
            "input[type='checkbox']",
            "input[type='text']",
            "textarea",
            "select",
            "button",
            "[role='button']",
            "input[type='submit']",
            "input[type='button']",
        ]
        for sel in sels:
            for el in page.query_selector_all(sel):
                try:
                    if el.is_visible():
                        bb = el.bounding_box()
                        if bb and bb.get("width", 0) > 10 and bb.get("height", 0) > 10:
                            total += 1
                except Exception:
                    continue
    except Exception:
        pass
    return total


def _has_actionable_elements(driver):
    """
    Heuristique : y a-t-il des éléments actionnables ?
    ➜ Vérifie le DOM courant ET les iframes (profondeur 2).
    """
    page = driver

    def _is_actionable(el) -> bool:
        """Évite les faux positifs : caché / disabled / taille nulle."""
        try:
            if not el.is_visible():
                return False
            if not el.is_enabled():
                return False
            bb = el.bounding_box()
            if bb is None:
                return False
            return bb.get("width", 0) > 2 and bb.get("height", 0) > 2
        except Exception:
            return False

    def _here(frame) -> bool:
        try:
            # Inputs classiques (uniquement visibles)
            inputs = frame.query_selector_all(
                "input[type='radio'], input[type='checkbox'], input[type='text'], textarea, select"
            )
            if any(_is_actionable(el) for el in inputs):
                return True
            # Labels cliquables (widgets masquant l'input natif)
            labels = frame.query_selector_all("label[for]")
            if any(_is_actionable(el) for el in labels):
                return True
            # Widgets custom (role=checkbox/radio)
            custom = frame.query_selector_all("[role='checkbox'], [role='radio']")
            if any(_is_actionable(el) for el in custom):
                return True
            # Boutons navigation (FR/EN), inclut Start! et Start (case insensitive)
            btn_xpath = (
                "xpath=//button[normalize-space()='Start!' or "
                "contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'start') or "
                "contains(., 'Continuer') or contains(., 'Suivant') or "
                "contains(., 'Next') or contains(., 'Continue') or "
                "contains(., 'Commencer') or contains(., 'Soumettre') or contains(., 'Submit')]"
                " | xpath=//a[(contains(@class,'btn') or contains(@class,'button') or contains(@class,'cta')) and "
                "(contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'start') or "
                "contains(., 'Continuer') or contains(., 'Suivant') or contains(., 'Next') or "
                "contains(., 'Continue') or contains(., 'Commencer'))]"
            )
            if any(_is_actionable(el) for el in frame.query_selector_all(btn_xpath)):
                return True
            # Inputs submit/button/image, boutons génériques
            submit_buttons = frame.query_selector_all(
                "input[type='submit'], input[type='button'], input[type='image'], button"
            )
            if any(_is_actionable(el) for el in submit_buttons):
                return True
        except Exception:
            pass
        return False

    # Frame principal
    try:
        if _here(page.main_frame):
            return True
    except Exception:
        pass

    # Iframes (profondeur 2)
    try:
        for frame in page.main_frame.child_frames:
            try:
                if _here(frame):
                    return True
                for subframe in frame.child_frames:
                    try:
                        if _here(subframe):
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass

    return False


def _get_multi_page_state(driver) -> tuple:
    """
    Empreinte de l'état courant d'une page multi-inputs pour la stuck detection.
    Retourne un tuple (nb_questions, textes_questions, états_inputs) :
      - nb_questions  : nombre de labels de questions visibles         (niveau 2)
      - textes        : tuple des textes de questions (100 chars max)  (niveau 3)
      - inputs        : tuple trié des valeurs sélectionnées/cochées   (niveau 4)
    En cas d'erreur, retourne (0, (), ()) pour ne pas bloquer.
    """
    page = driver
    try:
        q_texts = []
        for sel in [
            "fieldset legend",
            "[class*='question'] label",
            "[class*='Question'] label",
            "[role='group'] label",
        ]:
            elems = [e for e in page.query_selector_all(sel) if e.is_visible()]
            if elems:
                q_texts = [
                    (e.inner_text() or "").strip()[:100]
                    for e in elems
                    if (e.inner_text() or "").strip()
                ]
                break

        states = []
        for r in page.query_selector_all("input[type='radio']:checked"):
            states.append("r:" + (r.get_attribute("value") or r.get_attribute("id") or "?"))
        for c in page.query_selector_all("input[type='checkbox']:checked"):
            states.append("c:" + (c.get_attribute("value") or c.get_attribute("id") or "?"))
        for s in page.query_selector_all("select"):
            try:
                v = s.get_attribute("value") or ""
                if v:
                    states.append("s:" + v)
            except Exception:
                pass

        return (len(q_texts), tuple(q_texts), tuple(sorted(states)))
    except Exception:
        return (0, (), ())


def _looks_like_end_screen(driver):
    """
    Détection très simple d'un écran de fin (messages de remerciement/soumission).
    Évite de tourner en rond une fois le questionnaire terminé.
    """
    page = driver
    try:
        page_text = " ".join(
            [
                (el.inner_text() or "").strip()
                for el in page.query_selector_all(
                    "xpath=//body//*[self::h1 or self::h2 or self::p or self::div]"
                )
                if (el.inner_text() or "") and len((el.inner_text() or "").strip()) > 3
            ]
        ).lower()

        end_markers = [
            "merci d'avoir répondu",
            "merci pour votre participation",
            "vos réponses ont été enregistrées",
            "thank you for completing",
            "your responses have been recorded",
            "survey complete",
            "enquête terminée",
            "fin du questionnaire",
        ]
        return any(tok in page_text for tok in end_markers)
    except Exception:
        return False


_NETWORK_ERR_SIGNALS = (
    "err_tunnel_connection_failed",
    "this site can’t be reached",
    "this site can't be reached",
    "err_connection_refused",
    "err_name_not_resolved",
)

# Valeurs de retour de _recover_from_network_error()
_NET_ERR_CLEAN     = "clean"      # page saine, rien à faire
_NET_ERR_RECOVERED = "recovered"  # erreur détectée et récupérée → appelant fait continue
_NET_ERR_EXHAUSTED = "exhausted"  # 2 tentatives épuisées sans succès → appelant fait soft-restart


def _recover_from_network_error(driver) -> str:
    """
    Détecte une erreur réseau Chrome (ERR_TUNNEL_CONNECTION_FAILED, etc.) et tente
    jusqu'à _MAX_ATTEMPTS page.goto() successifs pour récupérer.

    Retourne :
      _NET_ERR_CLEAN     — page saine (chemin rapide, aucun effet de bord)
      _NET_ERR_RECOVERED — erreur récupérée → appelant doit faire `continue`
      _NET_ERR_EXHAUSTED — tentatives épuisées → appelant doit soft-restart
    """
    page = driver
    # -- Détection via page.content() (seul signal fiable sur chrome-error://) --
    try:
        source_lc = (page.content() or "").lower()
    except Exception:
        return _NET_ERR_CLEAN

    if not any(sig in source_lc for sig in _NETWORK_ERR_SIGNALS):
        return _NET_ERR_CLEAN

    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""

    _MAX_ATTEMPTS = 5
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        log_info("NET-ERR", f"Erreur réseau (tentative {attempt}/{_MAX_ATTEMPTS}) → attente 15s")
        time.sleep(15)
        try:
            page.goto(current_url, wait_until="domcontentloaded")
        except Exception as e:
            log_info("NET-ERR", f"page.goto() a échoué (tentative {attempt}/{_MAX_ATTEMPTS}) : {e}")
            continue
        try:
            page.wait_for_load_state("load", timeout=30_000)
        except Exception:
            time.sleep(5)
        try:
            still_error = any(sig in (page.content() or "").lower() for sig in _NETWORK_ERR_SIGNALS)
        except Exception:
            still_error = False
        if not still_error:
            return _NET_ERR_RECOVERED
        if attempt == _MAX_ATTEMPTS:
            log_info("NET-ERR", f"Page toujours en erreur après {_MAX_ATTEMPTS} tentatives → abandon")

    return _NET_ERR_EXHAUSTED


# Valeurs de retour de _recover_from_yougov_app_error()
_YG_ERR_CLEAN     = "clean"
_YG_ERR_RECOVERED = "recovered"
_YG_ERR_EXHAUSTED = "exhausted"

_YG_MAX_ATTEMPTS = 3


def _recover_from_yougov_app_error(driver) -> str:
    """
    Détecte la page d'erreur applicative YouGov (#notification.alert-error visible +
    #main_cont masqué) et tente jusqu'à _YG_MAX_ATTEMPTS page.goto() pour récupérer.
    """
    page = driver
    try:
        notif = page.query_selector("#notification")
        if notif is None:
            return _YG_ERR_CLEAN
        notif_classes = notif.get_attribute("class") or ""
        notif_display = notif.evaluate("(el) => window.getComputedStyle(el).display")
        if "alert-error" not in notif_classes or notif_display == "none":
            return _YG_ERR_CLEAN
        main_cont = page.query_selector("#main_cont")
        if main_cont is None:
            return _YG_ERR_CLEAN
        if main_cont.evaluate("(el) => window.getComputedStyle(el).display") != "none":
            return _YG_ERR_CLEAN
    except Exception:
        return _YG_ERR_CLEAN

    try:
        current_url = page.url or ""
    except Exception:
        current_url = ""

    for attempt in range(1, _YG_MAX_ATTEMPTS + 1):
        log_info("YG-APP-ERR", f"Erreur applicative YouGov (tentative {attempt}/{_YG_MAX_ATTEMPTS}) → attente 10s puis reload")
        time.sleep(10)
        try:
            page.goto(current_url, wait_until="domcontentloaded")
        except Exception as e:
            log_info("YG-APP-ERR", f"page.goto() a échoué (tentative {attempt}) : {e}")
            return _YG_ERR_EXHAUSTED
        try:
            page.wait_for_load_state("load", timeout=30_000)
        except Exception:
            time.sleep(5)
        try:
            notif = page.query_selector("#notification")
            still_error = (
                notif is not None
                and "alert-error" in (notif.get_attribute("class") or "")
                and notif.evaluate("(el) => window.getComputedStyle(el).display") != "none"
            )
        except Exception:
            still_error = False
        if not still_error:
            log_info("YG-APP-ERR", f"Page récupérée après {attempt} tentative(s)")
            return _YG_ERR_RECOVERED

    log_info("YG-APP-ERR", f"Page toujours en erreur après {_YG_MAX_ATTEMPTS} tentatives → abandon")
    return _YG_ERR_EXHAUSTED


# Référence module-level au SurveyContext actif — mis à jour par solve_full_survey()
_current_survey_ctx = None


def get_current_survey_ctx():
    """Retourne le SurveyContext actif, ou None si aucun survey en cours."""
    return _current_survey_ctx


def solve_full_survey(driver, api_key, *, account_id: str, survey_context=None, platform):
    if platform is None:
        raise ValueError("solve_full_survey() exige un paramètre platform non None")
    import Survey.survey_executor
    import Management.guards.runtime_guard
    import Management.guards.survey_difficulty_guard
    from Survey.survey_context import SurveyContext

    """
    Boucle principale de résolution du survey.
    1) Bascule vers l'onglet externe + stabilisation d'URL
    2) Répète : execute_survey_page() — petite pause — test si on continue
    On sort si :
      - plus rien d'actionnable détecté (survey terminé) → soft-restart
      - stuck : réponse acceptée mais page ne bouge pas (Option B) → soft-restart
    """
    page = driver

    # ── Pont BLOC 3a → hors-périmètre ────────────────────────────────────────
    # Shim pour : execute_survey_page (BLOC 3b), detect_strict_survey,
    # platform.handle_post_survey → Survey/functions.py (non migré),
    # try_click_qps_skip_to_survey, solve_recaptcha_v2_auto, snap_uploader.
    # page._page est maintenu en sync avec `page` à chaque changement d'onglet.
    page._survey_account_id = account_id

    print("🧪 [solve_full_survey] Début de traitement du survey...")
    if SNAP_ENABLED.strip() == "1":
        from Management.snap_uploader import new_survey, capture_and_upload
        new_survey()
        capture_and_upload(page, "survey_start")

    _survey_ctx = survey_context or SurveyContext(session_id=account_id, openai_api_key=api_key)
    global _current_survey_ctx
    _current_survey_ctx = _survey_ctx

    # Si plusieurs onglets existent, se positionner sur le dernier
    try:
        all_pages = page.context.pages
        if len(all_pages) > 1:
            page = all_pages[-1]
            print(f"🧭 Focus forcé sur l'onglet actif : {page.url}")
            _publish_live_page(page)
    except Exception as e:
        print("⚠ Impossible de forcer le focus onglet :", e)

    # Bascule vers l'onglet externe si nécessaire
    ext_page = _switch_to_external_tab(page, platform=platform)
    if ext_page is not None and ext_page is not page:
        page = ext_page
        _publish_live_page(page)

    # 1) Attendre que la redirection s'arrête sur une URL stable
    final_url = _wait_for_url_stable(page, max_wait=60)
    print(f" URL finale stabilisée : {final_url}")
    time.sleep(PAUSE_BEFORE_FIRST_SCAN)

    # 2) Boucle d'exécution des actions
    _no_progress_count = 0
    _NO_PROGRESS_THRESHOLD = 8
    last_url = page.url
    last_question_key = ""
    _multi_no_progress_count = 0
    _last_multi_page_state = None
    _cta_fail_count = 0
    guard = Management.guards.runtime_guard.get_guard()

    while True:
        if SNAP_ENABLED.strip() == "1":
            from Management.snap_uploader import capture_and_upload
            capture_and_upload(page, "survey_loop")

        # Préqualification Cint/QPS : passer directement au sondage si disponible
        from Survey.cta_handler import try_click_qps_skip_to_survey
        if try_click_qps_skip_to_survey(page):
            time.sleep(PAUSE_POST_CTA_NAV)
            last_url = page.url
            continue

        # Réinitialise le drapeau de succès côté handlers
        try:
            setattr(page, "last_action_success", False)
        except Exception:
            pass

        # [PATCH] Purge d'un overlay trop ancien (>3s) pour éviter des états collants
        try:
            ov = getattr(page, "_ui_overlay_opened", None)
            if ov and (time.time() - ov.get("ts", 0) > 3.0):
                setattr(page, "_ui_overlay_opened", None)
        except Exception:
            pass

        # --- STRICT GUARD ---
        is_strict, reason = Management.guards.survey_difficulty_guard.detect_strict_survey(page)
        if is_strict:
            if reason == "captcha":
                from config import get_captcha_behavior
                captcha_behavior = get_captcha_behavior()

                if captcha_behavior == "auto_2captcha":
                    print("[CAPTCHA] Tentative de résolution automatique via 2Captcha...")
                    try:
                        _captcha_url_now = page.url or ""
                    except Exception:
                        _captcha_url_now = ""
                    _last_captcha_url = getattr(page, "_auto2captcha_last_url", None)
                    _captcha_attempts = getattr(page, "_auto2captcha_attempts", 0)
                    if _last_captcha_url != _captcha_url_now:
                        _captcha_attempts = 0
                    _captcha_attempts += 1
                    setattr(page, "_auto2captcha_last_url", _captcha_url_now)
                    setattr(page, "_auto2captcha_attempts", _captcha_attempts)
                    if _captcha_attempts > 2:
                        log_info("CAPTCHA", f"Boucle captcha détectée ({_captcha_attempts} résolutions sans navigation) → soft-restart")
                        guard.record_success()
                        guard.signal_strict_survey("captcha_loop_detected")
                        return
                    if not Management.guards.survey_difficulty_guard.is_real_recaptcha_present(page):
                        log_info("CAPTCHA", "Pas de reCAPTCHA Google (iframe/sitekey) détecté → tentative CAPTCHA image-texte (normal_captcha)")
                        try:
                            from captcha.normal_captcha import handle_captcha as handle_normal_captcha
                            normal_handled = handle_normal_captcha(page)
                        except Exception as e:
                            print(f"[CAPTCHA] Erreur inattendue normal_captcha: {e}")
                            normal_handled = False
                        if normal_handled:
                            print("[CAPTCHA] ✅ CAPTCHA image-texte traité — reprise du survey")
                            continue
                        else:
                            print("[CAPTCHA] ❌ Aucun CAPTCHA image-texte trouvé/résolu → abandon survey")
                            guard.record_success()
                            guard.signal_strict_survey("captcha_auto_failed")
                            return

                    try:
                        from captcha.recaptcha_handler import solve_recaptcha_v2_auto
                        resolved = solve_recaptcha_v2_auto(page)
                    except Exception as e:
                        print(f"[CAPTCHA] Erreur inattendue recaptcha_handler: {e}")
                        resolved = False
                    if resolved:
                        print("[CAPTCHA] ✅ reCAPTCHA résolu — reprise du survey")
                        continue
                    else:
                        print("[CAPTCHA] ❌ Échec résolution automatique → abandon survey")
                        guard.record_success()
                        guard.signal_strict_survey("captcha_auto_failed")
                        return

                elif captcha_behavior == "restart":
                    print("[STRICT_SURVEY][MID] Captcha détecté -> restart propre")
                    guard.record_success()
                    guard.signal_strict_survey("strict_mid_captcha")
                    return

                print("[LOCAL][CAPTCHA] ⚠  CAPTCHA détecté → résolution MANUELLE requise")
                try:
                    captcha_url = page.url or ""
                    last_captcha_url = getattr(page, "_last_captcha_pause_url", None)
                    if last_captcha_url == captcha_url:
                        print("[LOCAL][CAPTCHA]   Captcha déjà traité sur cette URL, on continue")
                    else:
                        setattr(page, "_last_captcha_pause_url", captcha_url)
                        from config import should_block_for_input
                        if should_block_for_input():
                            try:
                                input("[LOCAL][PAUSE] 🧩 Résous le CAPTCHA dans le navigateur, puis appuie sur Entrée...\n")
                            except KeyboardInterrupt:
                                print("[LOCAL]   Abandon demandé par l'utilisateur")
                                guard.record_success()
                                guard.signal_strict_survey("captcha_user_abort")
                                return
                        else:
                            print("[LOCAL][CAPTCHA] ⚠  Terminal non-interactif, pas de pause possible")
                            guard.record_success()
                            guard.signal_strict_survey("captcha_no_tty")
                            return

                        print("[LOCAL][CAPTCHA]  Vérification de la disparition du captcha...")
                        deadline = time.time() + 30.0
                        captcha_resolved = False
                        while time.time() < deadline:
                            still_strict, still_reason = Management.guards.survey_difficulty_guard.detect_strict_survey(page)
                            if not still_strict or still_reason != "captcha":
                                print("[LOCAL][CAPTCHA] ✅ Captcha résolu → continuation de l'exécution")
                                captcha_resolved = True
                                break
                            time.sleep(1.0)
                        if not captcha_resolved:
                            print("[LOCAL][CAPTCHA]   Timeout : captcha toujours présent après 30s")
                            guard.record_success()
                            guard.signal_strict_survey("captcha_timeout")
                            return
                        print("[LOCAL][CAPTCHA] 🚀 Reprise de l'exécution du survey")
                except Exception as e:
                    print(f"[LOCAL][CAPTCHA]  Erreur lors de la gestion du captcha : {e}")
                    guard.record_success()
                    guard.signal_strict_survey("captcha_error")
                    return

            else:
                print(f"[STRICT_SURVEY][MID] Détecté en cours de survey ({reason}) -> restart propre")
                guard.record_success()
                guard.signal_strict_survey(f"strict_mid_{reason}")
                return

        # -------------------------------------------------------------------
        # CHECK RETOUR PLATEFORME AVANT execute_survey_page
        # page passé à is_on_platform/handle_post_survey car Survey/functions.py
        # utilise encore l'API Selenium (frontière BLOC 3a → Survey/functions.py).
        # -------------------------------------------------------------------
        try:
            if platform.is_on_platform(page):
                if platform.handle_post_survey(page, account_id):
                    print("[PRE-EXEC] Retour plateforme traité -> arrêt solve_full_survey()")
                    raise TopSurveysReturn()
        except TopSurveysReturn:
            raise
        except Exception as e:
            print(f"[PRE-EXEC] Check plateforme échoué: {e}")

        # --- Récupération erreur réseau Chrome ---
        _net_result = _recover_from_network_error(page)
        if _net_result == _NET_ERR_RECOVERED:
            continue
        if _net_result == _NET_ERR_EXHAUSTED:
            guard.request_survey_restart("net_err_max_attempts")
            return

        # --- Récupération erreur applicative YouGov ---
        _yg_result = _recover_from_yougov_app_error(page)
        if _yg_result == _YG_ERR_RECOVERED:
            continue
        if _yg_result == _YG_ERR_EXHAUSTED:
            guard.request_survey_restart("yougov_app_err_max_attempts")
            return

        # --- Détection page d'erreur applicative (Toluna/Confirmit) ---
        try:
            _error_els = page.query_selector_all(
                "xpath=//*["
                "contains(concat(' ', normalize-space(@class), ' '), ' errorPage ') or "
                "contains(concat(' ', normalize-space(@class), ' '), ' errorpage-wrapper ')"
                "]"
            )
            if _error_els:
                log_info("PLATFORM-ERR", "Page d'erreur applicative détectée (class~='errorpage') → soft-restart.")
                guard.record_success()
                guard.request_survey_restart("platform_error_page")
                return
        except Exception:
            pass

        # --- Détection page d'erreur Decipher/YourSurveyNow (div.survey-error) ---
        try:
            _decipher_err_els = [
                el for el in page.query_selector_all("div.survey-error")
                if el.is_visible()
            ]
            if _decipher_err_els:
                _has_actionable_q = page.query_selector(
                    "div.question input[type='radio'], div.question input[type='checkbox']"
                ) is not None
                if not _has_actionable_q:
                    try:
                        _derr_url = page.url or ""
                        _derr_txt = (_decipher_err_els[0].inner_text() or "").strip()[:200]
                        log_info("PLATFORM-ERR", f"div.survey-error url={_derr_url} texte={_derr_txt!r}")
                    except Exception:
                        pass
                    log_info("PLATFORM-ERR", "Page d'erreur applicative Decipher détectée → soft-restart.")
                    guard.record_success()
                    guard.request_survey_restart("decipher_survey_error")
                    return
        except Exception:
            pass

        # -------------------------------------------------------------------
        # a) Laisser GPT décider de l'action — BLOC 3b (execute_survey_page)
        # Pont BLOC 3a → BLOC 3b : page transmis pour compatibilité Selenium.
        # -------------------------------------------------------------------
        success = Survey.survey_executor.execute_survey_page(page, account_id, api_key, ctx=_survey_ctx)

        if success:
            guard.record_success()
        else:
            guard.record_error()

        if (os.getenv("SURVEY_CTX_DEBUG") or "").strip() == "1":
            dump = _survey_ctx.dump()
            print(
                f"\n[SURVEY_CTX] "
                f"entries={len(dump.get('history', []))} "
                f"summary={dump.get('summary', '')[:120] or '(none yet)'}"
            )
            for i, entry in enumerate(dump.get("history", [])[-5:], 1):
                print(f"  Q{i}: {entry.get('question','')[:80]} → {entry.get('answer','')}")
            print()

        # [PATCH] Mode "overlay ouvert" → recapture rapide
        try:
            overlay = getattr(page, "_ui_overlay_opened", None)
        except Exception:
            overlay = None

        if overlay and overlay.get("type") == "dropdown":
            print("🎯 Dropdown ouvert → recapture immédiate (on saute l'attente/redirection).")
            time.sleep(0.3)
            continue

        # b) Attente chargement page avant d'inspecter le DOM
        try:
            page.wait_for_load_state("load", timeout=30_000)
        except Exception:
            pass
        time.sleep(0.3)

        # c) Attente ADAPTATIVE après action
        try:
            just_succeeded = bool(getattr(page, "last_action_success", False) or success)
        except Exception:
            just_succeeded = bool(success)

        has_more_to_do = _has_actionable_elements(page)

        if just_succeeded and has_more_to_do:
            # -------------------------------------------------------------------
            # Stuck detection — pages multi-inputs (3 niveaux)
            # -------------------------------------------------------------------
            _cur_multi_state = _get_multi_page_state(page)
            if _last_multi_page_state is not None:
                _prev_n_q, _prev_q_texts, _prev_inputs = _last_multi_page_state
                _cur_n_q,  _cur_q_texts,  _cur_inputs  = _cur_multi_state
                if _cur_n_q == _prev_n_q:
                    if _cur_q_texts == _prev_q_texts:
                        if _cur_inputs == _prev_inputs:
                            _multi_no_progress_count += 1
                            log_debug("STUCK-MULTI",
                                f"Aucune progression ({_multi_no_progress_count}/{_NO_PROGRESS_THRESHOLD})"
                                f" — q={_cur_n_q} textes={_cur_q_texts} inputs={_cur_inputs}")
                            if _multi_no_progress_count >= _NO_PROGRESS_THRESHOLD:
                                log_info("STUCK-MULTI",
                                    f"Page multi-inputs inchangée {_NO_PROGRESS_THRESHOLD} fois → soft-restart")
                                guard.record_success()
                                guard.request_survey_restart("solve_no_progress_multi")
                                return
                        else:
                            log_debug("STUCK-MULTI", f"Progression : états inputs modifiés {_prev_inputs} → {_cur_inputs}")
                            _multi_no_progress_count = 0
                    else:
                        log_debug("STUCK-MULTI", f"Progression : textes modifiés ({_prev_n_q}q → {_cur_n_q}q)")
                        _multi_no_progress_count = 0
                else:
                    log_debug("STUCK-MULTI", f"Progression : nb questions modifié {_prev_n_q} → {_cur_n_q}")
                    _multi_no_progress_count = 0
            _last_multi_page_state = _cur_multi_state
            print(" Action en-page réussie et autres éléments visibles → pas d'attente de navigation.")
            try:
                page.wait_for_load_state("load", timeout=10_000)
            except Exception:
                pass
            time.sleep(0.4)
            continue

        # URL check — navigation SPA ou avec redirect ?
        _check_url = page.url

        if _check_url != last_url:
            maxw = 3 if just_succeeded else 8
            current_url = _wait_for_url_stable(page, max_wait=maxw)
        else:
            time.sleep(0.5)
            current_url = page.url

        if current_url != last_url:
            _no_progress_count = 0
            _multi_no_progress_count = 0
            _last_multi_page_state = None
            last_question_key = ""
            _cta_fail_count = 0
            print(f"[solve_full_survey] Changement d'URL {last_url} → {current_url}")
            last_url = current_url

            try:
                page.wait_for_load_state("load", timeout=30_000)
            except Exception:
                pass
            time.sleep(PAUSE_POST_CTA_NAV)

            # Retour plateforme ? (page pour Survey/functions.py)
            try:
                if platform.is_on_platform(page):
                    if platform.handle_post_survey(page, account_id):
                        print("[solve_full_survey] Retour plateforme → arrêt.")
                        raise TopSurveysReturn()
            except TopSurveysReturn:
                raise
            except Exception as e:
                print(f"[solve_full_survey] Hook plateforme échoué : {e}")

            continue

        # -------------------------------------------------------------------
        # Option B — Stuck detection : succès accepté mais page ne bouge pas
        # -------------------------------------------------------------------
        if success and current_url == last_url:
            try:
                import preselection.question_analyzer as _qa
                _html = _qa.extract_popup_html(page)
                _current_q = (_qa.extract_question_text(_html) or "")[:150]
            except Exception:
                _current_q = ""

            if _current_q and _current_q != last_question_key:
                print("[solve_full_survey] Question changée sans changement d'URL → progression détectée")
                last_question_key = _current_q
                _no_progress_count = 0
            else:
                _no_progress_count += 1
                if _no_progress_count >= _NO_PROGRESS_THRESHOLD:
                    print(f"[STUCK] Réponse acceptée {_NO_PROGRESS_THRESHOLD} fois sans avance → soft-restart")
                    guard.record_success()
                    guard.request_survey_restart("solve_no_progress")
                    return
        else:
            _no_progress_count = 0
            try:
                import preselection.question_analyzer as _qa
                _html = _qa.extract_popup_html(page)
                last_question_key = (_qa.extract_question_text(_html) or "")[:150]
            except Exception:
                last_question_key = ""
            if current_url == last_url:
                _cta_fail_count += 1
                if _cta_fail_count >= 3:
                    log_info("STUCK", f"CTA en échec {_cta_fail_count} fois sur la même URL → soft-restart")
                    guard.record_success()
                    guard.request_survey_restart("cta_fail_no_progress")
                    return

        # d) Conditions d'arrêt
        has_actionables = _has_actionable_elements(page)
        if not has_actionables:
            just_succeeded = False
            try:
                just_succeeded = bool(getattr(page, "last_action_success", False) or success)
            except Exception:
                pass

            if just_succeeded:
                print(" Pas encore d'élément actionnable, mais action réussie à l'étape précédente. On continue.")
                time.sleep(1.0)
                continue

            print("[solve_full_survey] Aucun élément actionnable → survey terminé, soft-restart.")
            guard.record_success()
            try:
                _survey_ctx.flush(timeout=5.0)
            except Exception:
                pass
            guard.request_survey_restart("survey_end")
            return

        try:
            _survey_ctx.maybe_update_summary()
        except Exception:
            pass