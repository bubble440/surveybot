import sys, os

# ── Mode CLI --query-cooldown (invoqué par wake_scheduler.ps1) ───────────────
# Point d'entrée dédié : sortie JSON du statut cooldown par compte, sans aucune
# initialisation bot (ni load_config, ni check_license, ni navigateur, ni lock).
# account_state.load_state() n'a besoin que de global_config et _license_config,
# tous deux compilés dans le binaire Nuitka — aucune dépendance externe requise.
if len(sys.argv) >= 2 and sys.argv[1] == "--query-cooldown":
    import json as _json, time as _time
    from State.account_state import load_state as _load_state, _ts_to_unix
    _now = int(_time.time())
    _results = []
    for _aid in sys.argv[2:]:
        try:
            _st = _load_state(_aid)
            _cu = _st.get("cooldown_until_ts", "1970-01-01T00:00:00")
            _results.append({
                "account_id": _aid,
                "cooldown_until_ts": _cu,
                "is_expired": _ts_to_unix(_cu) < _now,
            })
        except Exception as _e:
            _results.append({"account_id": _aid, "error": str(_e), "is_expired": False})
    print(_json.dumps(_results))
    sys.exit(0)

# ── Mode CLI --selftest-tz (diagnostic embarquement tzdata / Nuitka) ─────────
# Point d'entrée dédié : vérifie que ZoneInfo("Europe/Paris") se résout dans CE
# binaire, sans navigateur/licence/lock/Postgres. Ajouté le 24/07/2026 suite au
# diagnostic tzdata absent de requirements.txt et de nuitka_build_release.ps1
# (voir Utils/DEPLOIEMENT_BAREMETAL_DECISIONS.md section 4) — conservé en
# permanence comme mode de diagnostic réutilisable après tout changement de
# dépendances liées aux fuseaux horaires.
if len(sys.argv) >= 2 and sys.argv[1] == "--selftest-tz":
    from Management.pause_policy import PausePolicy, resolve_pause_seconds
    try:
        secs = resolve_pause_seconds(PausePolicy.DAILY_RESET)
        print(f"TZ_SELFTEST_OK seconds_until_midnight_europe_paris={secs}")
        sys.exit(0)
    except Exception as e:
        print(f"TZ_SELFTEST_FAIL {type(e).__name__}: {e}")
        sys.exit(1)

print("BOOT: container démarré.", flush=True)

# ⚠ Doit s'exécuter AVANT tout import qui lit une constante d'environnement au
# niveau module (config.py: RUN_ENV/BROWSER_MODE, State/account_state.py via
# launch.py: DATABASE_URL/STATE_BACKEND/STATE_TABLE, license_guard.py appelé
# ci-dessous). Réinjecte la config globale dans os.environ sans écraser une
# valeur déjà présente (accounts.json, script de lancement, secrets Fly.io).
from preselection.config_loader import load_config

# En mode attach, toutes les variables d'environnement nécessaires sont déjà
# injectées par le script de lancement PowerShell (attach_tab.ps1) avant
# l'exécution de ce script : le chargement du fichier de configuration prod
# (receiver_config.json, secrets) est donc inutile et est sauté. On détecte
# le mode attach directement via la variable d'environnement BROWSER_MODE,
# sans importer config.py (pas encore garanti chargeable à ce stade).
if os.getenv("BROWSER_MODE", "").strip().lower() != "attach":
    load_config()

from config import is_attach_mode, RUN_ENV, BROWSER_MODE, is_prod_like, should_run_guard_monitor, should_run_heartbeat, log_config_summary

if is_attach_mode():
    ACCOUNT_ID = "local_debug"
else:
    ACCOUNT_ID = os.getenv("ACCOUNT_ID")
    if not ACCOUNT_ID:
        raise RuntimeError("ACCOUNT_ID manquant en prod (BROWSER_MODE != attach)")

# Vérification du seuil de redémarrages automatiques (crash-loop) placée AVANT
# check_license_or_exit() ci-dessous : check_license_or_exit() peut sys.exit()
# très tôt (Postgres injoignable, licence désactivée, quota atteint) et ces
# arrêts précoces doivent être comptés comme n'importe quel autre crash, sinon
# seul NSSM (AppExit Default -> Restart) pilote la boucle de redémarrage sans
# jamais déclencher EXIT_FATAL. Ne pas dupliquer cet appel plus bas dans main() :
# une 2e lecture dans le même run verrait le sentinel EXIT_CRASH que la 1re
# vient d'écrire et fausserait le compteur.
if not is_attach_mode():
    from bot_supervisor import check_and_record_start, record_exit, EXIT_FATAL, clear_manual_stop_marker
    from launch import build_notifier
    # Ce démarrage (nssm start explicite ou redémarrage machine) vaut reprise :
    # lève le marqueur posé par stop_bot_manual.ps1, voir clear_manual_stop_marker().
    clear_manual_stop_marker(ACCOUNT_ID)
    _should_abort, _restart_count = check_and_record_start(ACCOUNT_ID)
    if _should_abort:
        _abort_msg = (
            f"🚨 BOT {ACCOUNT_ID} : seuil de redémarrages automatiques dépassé "
            f"({_restart_count} redémarrages en fenêtre courte). "
            "Arrêt FATAL — vérifier le proxy, le compte ou la plateforme."
        )
        print(_abort_msg)
        try:
            build_notifier(None)(_abort_msg)
        except Exception:
            pass
        record_exit(ACCOUNT_ID, EXIT_FATAL, "restart_threshold_exceeded")
        sys.exit(EXIT_FATAL)

from preselection.license_guard import check_license_or_exit
if not is_attach_mode():
    check_license_or_exit()

import sys, json, time, traceback
from urllib.parse import urlparse
from launch import start_heartbeat_thread, acquire_account_lock_or_exit, mark_bot_running
from launch import install_sigterm_handler, install_sigint_handler, start_runtime_guard, launch_driver_or_fail, init_session_and_enter_surveys, install_sigusr1_handler
from launch import run_main_loop, build_notifier, soft_restart, start_debug_http_server, setup_logging
from platforms import get_platform
from Management.guards.runtime_guard import get_guard

# 1) stdout en line-buffering si dispo (Python 3.7+)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass



def _attach_is_user_web_url(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return False
    # On exclut volontairement les onglets internes Chrome (chrome://, devtools://, etc.)
    return u.startswith("http://") or u.startswith("https://")

def _attach__is_disabled_token(s: str) -> bool:
    return (s or "").strip().lower() in ("none", "null", "false", "0", "off")

def _attach__strip_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    # ignore fragment
    u = u.split("#", 1)[0].strip()
    # normalize trailing slash (soft)
    if u.endswith("/") and len(u) > 8:
        u = u[:-1]
    return u

def _attach_display_url(url: str) -> str:
    """Affiche uniquement le schéma + host (jusqu'au TLD), sans path/query."""
    u = _attach__strip_url(url)
    if not u:
        return ""

    lu = u.lower()
    if not (lu.startswith("http://") or lu.startswith("https://")):
        return u

    try:
        parsed = urlparse(u)
    except Exception:
        return u

    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return ""

    port = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{host}{port}"

def _attach_urls_equiv(a: str, b: str) -> bool:
    aa = _attach__strip_url(a)
    bb = _attach__strip_url(b)
    return bool(aa and bb and aa == bb)

def _attach_pick_ui_active_tab(driver, handles):
    """
    Tente de retrouver l'onglet UI réellement actif (celui que tu vois).
    Heuristique stable:
      - on ne considère que les URLs http(s)
      - on préfère visibilityState='visible' et document.hasFocus()==True
      - si focus indisponible, on prend au moins visibilityState='visible'
    Retour: tuple (idx, handle, url, vis, has_focus) ou None
    """
    best_visible = None  # (has_focus, idx, handle, url, vis)

    for idx, h in enumerate(list(handles) or []):
        try:
            driver.switch_to.window(h)
        except Exception:
            continue

        try:
            url = driver.url or ""
        except Exception:
            url = ""

        if not _attach_is_user_web_url(url):
            continue

        vis = ""
        has_focus = False

        try:
            vis = (driver.execute_script("return (document.visibilityState || '') + ''") or "").strip().lower()
        except Exception:
            vis = ""

        try:
            has_focus = bool(driver.execute_script("return !!(document.hasFocus && document.hasFocus());"))
        except Exception:
            has_focus = False

        # Meilleur cas: visible + focus => c'est l'onglet actif UI
        if vis == "visible" and has_focus:
            return (idx, h, url, vis, has_focus)

        # Sinon on garde le meilleur "visible"
        if vis == "visible":
            cand = (has_focus, idx, h, url, vis)
            if (best_visible is None) or (cand[0] > best_visible[0]):
                best_visible = cand

    if best_visible is not None:
        has_focus, idx, h, url, vis = best_visible
        return (idx, h, url, vis, has_focus)

    return None


def run_attach_takeover(driver, *, api_key: str, account_id: str, platform=None) -> None:
    """
    Mode takeover: on n'ouvre AUCUNE URL, on n'exécute PAS la présélection TopSurveys.
    On agit uniquement sur la page courante (celle que tu as ouverte à la main).
    """
    import time
    import Survey.survey_executor as survey_executor
    import Management.guards.survey_difficulty_guard as difficulty_guard
    from Survey.survey_context import SurveyContext
    import Survey.survey_solver as survey_solver

    # Instancier et exposer le contexte dès le début du takeover
    _ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
    survey_solver._current_survey_ctx = _ctx

    driver._survey_account_id = account_id

    from Survey.functions import _handle_topsurveys_exclusion_popup

    _is_topsurveys = platform is None or platform.get_platform_name() == "topsurveys"

    max_steps = int(os.getenv("ATTACH_MAX_STEPS", "100"))
    print(f"[ATTACH] takeover loop start (max_steps={max_steps}) url={_attach_display_url(getattr(driver,'url',''))}")
    for i in range(1, max_steps + 1):
        try:
            # Préqualification Cint/QPS : passer directement au sondage si disponible
            from Survey.cta_handler import try_click_qps_skip_to_survey
            if try_click_qps_skip_to_survey(driver):
                time.sleep(2.0)
                continue

            if _is_topsurveys:
                # === RETOUR TOPSURVEYS ? === (chemin existant — inchangé)
                try:
                    _cur_url = (driver.url or "").lower()
                    if "topsurveys.app" in _cur_url:
                        # Écran "Courte pause" (vérification téléphone/PIN) : laisser
                        # execute_survey_page() le traiter via ses handlers dédiés.
                        _has_phone_screen = bool(driver.evaluate("() => !!document.querySelector(\'div.phone-verification-container\')"))
                        if not _has_phone_screen:
                            _handle_topsurveys_exclusion_popup(driver, account_id)
                            print(f"[ATTACH] Retour TopSurveys détecté step={i} → sortie boucle.")
                            break
                except Exception as _e:
                    print(f"[ATTACH][TOPSURVEYS_CHECK] erreur: {_e}")
                    break
            else:
                # Stratégie additive : plateforme configurée != TopSurveys → détection
                # de retour plateforme via l'interface Platform (même pattern déjà en
                # place et fonctionnel dans Survey/survey_solver.py::solve_full_survey()),
                # plutôt qu'une vérification d'URL câblée en dur.
                try:
                    if platform.is_on_platform(driver):
                        if platform.handle_post_survey(driver, account_id):
                            print(
                                f"[ATTACH] Retour plateforme ({platform.get_platform_name()}) "
                                f"détecté step={i} → sortie boucle."
                            )
                            break
                except Exception as _e:
                    print(f"[ATTACH][PLATFORM_CHECK] erreur: {_e}")
                    break

            # === STRICT GUARD CHECK ===
            # Détecte les pages non supportées (image_evaluation, drag_drop, etc.)
            is_strict, reason = difficulty_guard.detect_strict_survey(driver)
            if is_strict:
                if reason == "drag_drop":
                    print("[ATTACH][STRICT] drag_drop strict reason ignored -> continue")
                    continue

                # Captcha : tentative de résolution automatique via 2Captcha
                if reason == "captcha":
                    from config import get_captcha_behavior
                    captcha_behavior = get_captcha_behavior()

                    if captcha_behavior == "auto_2captcha":
                        if not difficulty_guard.is_real_recaptcha_present(driver):
                            print("[ATTACH][CAPTCHA] Pas de reCAPTCHA Google (iframe/sitekey) détecté → tentative CAPTCHA image-texte (normal_captcha)")
                            try:
                                from captcha.normal_captcha import handle_captcha as handle_normal_captcha
                                normal_handled = handle_normal_captcha(driver)
                            except Exception as e:
                                print(f"[ATTACH][CAPTCHA] Erreur inattendue normal_captcha: {e}")
                                normal_handled = False
                            if normal_handled:
                                print("[ATTACH][CAPTCHA] ✅ CAPTCHA image-texte traité — reprise de la boucle")
                                continue
                            else:
                                print("[ATTACH][CAPTCHA] ❌ Aucun CAPTCHA image-texte trouvé/résolu → abandon du survey")
                                break

                        print("[ATTACH][CAPTCHA] reCAPTCHA détecté → tentative 2Captcha...")
                        try:
                            from captcha.recaptcha_handler import solve_recaptcha_v2_auto
                            resolved = solve_recaptcha_v2_auto(driver)
                        except Exception as e:
                            print(f"[ATTACH][CAPTCHA] Erreur recaptcha_handler: {e}")
                            resolved = False

                        if resolved:
                            # CRITIQUE : le callback CMIX a bien mis pass=true, MAIS
                            # [data-sitekey] reste dans le DOM même après résolution.
                            # Si on fait continue() directement, detect_strict_survey()
                            # re-détecte le captcha → boucle infinie + coût 2Captcha.
                            # Solution : cliquer le CTA et ATTENDRE que l'URL change
                            # (confirmation que la page suivante est chargée) avant continue.
                            print("[ATTACH][CAPTCHA] ✅ pass=true set → clic CTA pour avancer la page...")
                            try:
                                from Survey.cta_handler import try_click_navigation_cta_any_context
                                cta_clicked = try_click_navigation_cta_any_context(driver)
                                if cta_clicked:
                                    print("[ATTACH][CAPTCHA] ✅ CTA cliqué → attente changement URL...")
                                    # Attente active : l'URL doit changer (max 10s).
                                    # Sleep fixe insuffisant — Decipher peut prendre 3-8s
                                    # pour soumettre le formulaire et charger la page suivante.
                                    # On attend le changement d'URL plutôt qu'un délai arbitraire.
                                    _url_before = driver.url
                                    try:
                                        driver.wait_for_url(lambda url: url != _url_before, timeout=10000)
                                        print(f"[ATTACH][CAPTCHA] ✅ URL changée → {driver.url[:60]}")
                                    except Exception:
                                        print("[ATTACH][CAPTCHA] ⚠️ URL inchangée après 10s — on continue")
                                    time.sleep(0.5)  # micro-pause DOM post-navigation
                                else:
                                    print("[ATTACH][CAPTCHA] ⚠️ Aucun CTA trouvé (non bloquant)")
                            except Exception as e:
                                print(f"[ATTACH][CAPTCHA] CTA click erreur (non bloquant): {e}")
                            continue  # reprend la boucle — page suivante sans [data-sitekey]
                        else:
                            print("[ATTACH][CAPTCHA] ❌ Échec → abandon du survey")
                            break

                    elif captcha_behavior == "pause":
                        # LOCAL interactif : pause manuelle (anti-boucle sur même URL)
                        captcha_url = driver.url or ""
                        last_captcha_url = getattr(driver, "_last_captcha_pause_url", None)
                        if last_captcha_url == captcha_url:
                            print("[ATTACH][CAPTCHA] Déjà traité sur cette URL → continue")
                        else:
                            setattr(driver, "_last_captcha_pause_url", captcha_url)
                            try:
                                input("[ATTACH][PAUSE] Résous le CAPTCHA dans le navigateur, puis appuie sur Entrée...\n")
                            except (KeyboardInterrupt, EOFError):
                                print("[ATTACH] Abandon demandé")
                                break
                        continue

                    else:  # "restart" = pas de clé 2Captcha
                        print("[ATTACH][STRICT] captcha détecté → abandon (pas de clé 2Captcha)")
                        break

                else:
                    # Autres raisons (image_evaluation, hold_button...) → abandon immédiat
                    print(f"[ATTACH][STRICT] Détecté: {reason} -> abandon du survey")
                    print(f"[ATTACH][STRICT] Ce type de survey n'est pas supporté en V1")
                    break

        ### Résultat attendu dans les logs avec clé 2Captcha configurée
            
            # --- Récupération erreur applicative YouGov (#notification.alert-error visible) ---
            _yg_result = survey_solver._recover_from_yougov_app_error(driver)
            if _yg_result == survey_solver._YG_ERR_RECOVERED:
                continue
            if _yg_result == survey_solver._YG_ERR_EXHAUSTED:
                print(f"[YG-APP-ERR] Erreur applicative YouGov non récupérée après budget step={i} → sortie boucle.")
                break

            # --- Détection page d'erreur applicative (Toluna: div.errorPage, Confirmit: div.errorpage-wrapper) ---
            try:
                _error_els = driver.query_selector_all(
                    "xpath=//*["
                    "contains(concat(' ', normalize-space(@class), ' '), ' errorPage ') or "
                    "contains(concat(' ', normalize-space(@class), ' '), ' errorpage-wrapper ')"
                    "]"
                )
                if _error_els:
                    print(f"[PLATFORM-ERR] Page d'erreur applicative détectée (class~='errorpage') step={i} url={_attach_display_url(driver.url)} → sortie boucle.")
                    break
            except Exception:
                pass

            # --- Détection page d'erreur applicative Decipher/YourSurveyNow (div.survey-error visible) ---
            try:
                _decipher_err_els = [
                    el for el in driver.query_selector_all("div.survey-error")
                    if el.is_visible()
                ]
                if _decipher_err_els:
                    _has_actionable_q = driver.query_selector(
                        "div.question input[type='radio'], div.question input[type='checkbox']"
                    ) is not None
                    if not _has_actionable_q:
                        try:
                            _derr_txt = (_decipher_err_els[0].inner_text() or "").strip()[:200]
                        except Exception:
                            _derr_txt = ""
                        print(f"[PLATFORM-ERR] Page d'erreur Decipher (div.survey-error) step={i} url={_attach_display_url(driver.url)} texte={_derr_txt!r} → sortie boucle.")
                        break
            except Exception:
                pass

            ok = survey_executor.execute_survey_page(driver, account_id, api_key, ctx=_ctx)
            _ctx.maybe_update_summary()                                           # ← ajouter cette ligne
            print(f"[ATTACH] step={i}/{max_steps} ok={ok} url={_attach_display_url(driver.url)}")

            if not ok and survey_executor._attach_disq_stop_requested:
                print(f"[ATTACH][DISQ] Page de disqualification détectée → arrêt immédiat boucle step={i}.")
                break

            if not ok:
                try:
                    _is_isd_gate = bool(driver.evaluate(
                        """() => {
                        const isVisible = (el) => {
                          if (!el) return false;
                          const s = window.getComputedStyle(el);
                          if (!s || s.display === 'none' || s.visibility === 'hidden') return false;
                          const r = el.getBoundingClientRect();
                          return !!(r && r.width > 0 && r.height > 0);
                        };
                        const isdRoot = document.querySelector('#ISD, [id^="rootDiv_"]');
                        if (!isdRoot) return false;
                        const video = isdRoot.querySelector('video');
                        return !!(video && isVisible(video));
                        }"""
                    ))
                except Exception:
                    _is_isd_gate = False
                if _is_isd_gate:
                    print(f"[ATTACH][VIDEO_GATE] Page vidéo ISD non résolvable détectée step={i} → sortie boucle.")
                    break
        except Exception as e:
            # Diagnostic additif : ne change pas le comportement (le break reste
            # inconditionnel) — capture la stack trace complète et l'état des
            # threads actifs pour identifier, à la prochaine occurrence, l'appel
            # Playwright précis en cause et un éventuel accès concurrent au même
            # driver/page depuis un autre thread (API sync Playwright non thread-safe).
            import traceback as _traceback, threading as _threading
            from Survey.log_utils import log_debug as _log_debug
            _other_threads = [
                t.name for t in _threading.enumerate()
                if t is not _threading.current_thread()
            ]
            _log_debug(
                "[ATTACH][ERROR_TRACE]",
                f"step={i} thread={_threading.current_thread().name} "
                f"other_active_threads={_other_threads}\n{_traceback.format_exc()}",
            )
            print(f"[ATTACH][ERROR] step={i} {type(e).__name__}: {e}")
            break

        time.sleep(0.6)  # mini respiration DOM

    print("[ATTACH] takeover loop end (process exit, sans fermer Chrome).")



def _get_attach_route() -> str:
    """
    Résolution de la route attach, par ordre de priorité :
      1) ATTACH_ROUTE env var (valeur persistante : "preselection" | "resolution" | "login")
         → définie par l'utilisateur dans le script de lancement, jamais redemandée.
      2) ATTACH_ROUTE_PROMPT=1 → prompt interactif dans le terminal.
         Le choix est écrit dans ATTACH_ROUTE via os.environ pour que les relances
         dans le même processus héritent de la valeur sans redemander.
      3) Défaut silencieux : "resolution".
    """
    # Priorité 1 : valeur déjà fixée (env de lancement ou prompt précédent)
    fixed = (os.getenv("ATTACH_ROUTE") or "").strip().lower()
    if fixed in {"preselection", "resolution", "login"}:
        return fixed

    # Priorité 2 : prompt interactif si demandé
    if os.getenv("ATTACH_ROUTE_PROMPT") == "1":
        print("[ATTACH] Choisis la route de takeover :")
        print("  1) preselection  (popup déjà affiché)")
        print("  2) resolution    (déjà sur la page survey)")
        print("  3) login         (login + sélection survey complète — BLOC 1 natif Playwright)")
        choice = (input("Choix [1/2/3, défaut=2]: ") or "").strip().lower()

        if choice in {"1", "preselection"}:
            route = "preselection"
        elif choice in {"3", "login"}:
            route = "login"
        else:
            if choice not in {"", "2", "resolution"}:
                print(f"[ATTACH] choix invalide={choice!r} -> fallback resolution")
            route = "resolution"

        # Mémoriser dans l'env du processus pour les relances dans la même session
        os.environ["ATTACH_ROUTE"] = route
        print(f"[ATTACH] route={route!r} mémorisée (ATTACH_ROUTE). Modifier la var env pour changer.")
        return route

    # Priorité 3 : défaut silencieux
    return "resolution"



def _attach_select_tab_pw(context, *, exclude_url_pred=None):
    """
    Sélection d'onglet en mode attach Playwright natif.
    Retourne la Page sélectionnée (Playwright native, pas un shim).
    Même logique de priorité que _attach_select_tab mais sans API Selenium.
    """
    url_contains = (os.getenv("ATTACH_TAB_URL_CONTAINS") or "").strip()
    if _attach__is_disabled_token(url_contains):
        url_contains = ""

    title_contains = (os.getenv("ATTACH_TAB_TITLE_CONTAINS") or "").strip()
    if _attach__is_disabled_token(title_contains):
        title_contains = ""

    dom_contains = (os.getenv("ATTACH_TAB_DOM_CONTAINS") or "").strip()
    if _attach__is_disabled_token(dom_contains):
        dom_contains = ""

    mode = (os.getenv("ATTACH_TAB_SELECTOR", "current") or "current").strip().lower()

    pages = context.pages
    if not pages:
        return context.new_page()

    def _is_candidate(p) -> bool:
        u = (p.url or "").lower()
        if not (u.startswith("http://") or u.startswith("https://")):
            return False
        return not (exclude_url_pred and exclude_url_pred(p.url))

    def _safe_body_text(p, max_chars: int = 8000) -> str:
        try:
            return (
                p.evaluate(
                    f"() => (document.body && document.body.innerText || '').slice(0, {max_chars})"
                ) or ""
            )
        except Exception:
            return ""

    def _score(p) -> tuple:
        try:
            actionable = p.evaluate(
                "() => { try { return document.querySelectorAll("
                "\"input,select,textarea,button,[role='button'],[role='radio'],[role='checkbox'],label[for]\""
                ").length || 0; } catch(e) { return 0; } }"
            ) or 0
        except Exception:
            actionable = 0
        try:
            text_len = p.evaluate(
                "() => { try { return (document.body && (document.body.innerText||'').length) || 0; } catch(e) { return 0; } }"
            ) or 0
        except Exception:
            text_len = 0
        return int(actionable), int(text_len)

    def _last_web():
        for p in reversed(pages):
            if _is_candidate(p):
                print(f"[ATTACH_PW] Tab=last_web url={_attach_display_url(p.url)}")
                return p
        return None

    def _is_page_ready(p) -> bool:
        try:
            return p.evaluate("() => document.readyState") == "complete"
        except Exception:
            return False

    def _last_web_ready(timeout_s: float = 2.0, poll_s: float = 0.15):
        """
        Variante additive de _last_web() : parmi les onglets candidats (mêmes
        critères que _is_candidate), ne retient que ceux dont document.readyState
        vaut "complete" au moment de la lecture, avec budget borné pour laisser le
        temps à un onglet encore en chaîne de redirection (ex: panel -> domaine
        racine -> page survey finale) de se stabiliser plutôt que d'être retenu sur
        une URL transitoire qui satisfait _is_candidate sans porter le contenu réel.

        Cause confirmée (attach CDP fraîche, route=resolution) : _last_web() lit
        p.url en direct sans vérifier l'état de chargement ; un onglet encore en
        transit peut transitoirement passer le filtre URL de _is_candidate et être
        choisi à la place de l'onglet réellement affiché et chargé. _last_web()
        elle-même n'est pas modifiée ; cette variante est appelée à la place aux
        points d'appel concernés par ce bug.
        """
        import time as _time_lw

        deadline = _time_lw.time() + max(0.0, timeout_s)
        last_seen = None
        while _time_lw.time() < deadline:
            for p in reversed(pages):
                if not _is_candidate(p):
                    continue
                last_seen = last_seen or p
                if _is_page_ready(p):
                    print(f"[ATTACH_PW] Tab=last_web_ready url={_attach_display_url(p.url)}")
                    return p
            _time_lw.sleep(poll_s)
        if last_seen is not None:
            print(
                f"[ATTACH_PW] Tab=last_web_ready timeout={timeout_s}s "
                f"fallback last_seen url={_attach_display_url(last_seen.url)}"
            )
        return last_seen

    # 1) URL contains
    if url_contains:
        for p in pages:
            if _is_candidate(p) and url_contains in (p.url or ""):
                print(f"[ATTACH_PW] Tab=url_contains url={_attach_display_url(p.url)}")
                return p
        print(f"[ATTACH_PW] Tab=url_contains NOT FOUND ({url_contains})")

    # 2) Title contains
    if title_contains:
        needle = title_contains.lower()
        for p in pages:
            if _is_candidate(p) and needle in (p.title() or "").lower():
                print(f"[ATTACH_PW] Tab=title_contains url={_attach_display_url(p.url)}")
                return p
        print(f"[ATTACH_PW] Tab=title_contains NOT FOUND ({title_contains})")

    # 3) DOM contains
    if dom_contains:
        needle = dom_contains.lower()
        for p in pages:
            if not _is_candidate(p):
                continue
            if needle in _safe_body_text(p).lower():
                print(f"[ATTACH_PW] Tab=dom_contains url={_attach_display_url(p.url)}")
                return p
        print(f"[ATTACH_PW] Tab=dom_contains NOT FOUND ({dom_contains})")

    candidates = [p for p in pages if _is_candidate(p)]

    # 4a) pick/prompt
    if mode in ("pick", "prompt", "menu") and is_attach_mode():
        print("[ATTACH_PW] Tabs disponibles:")
        for i, p in enumerate(candidates):
            print(f"  {i:02d} | {p.url}")
        choice = (input("[ATTACH_PW] Index: ") or "").strip()
        if choice.isdigit():
            idx = int(choice)
            if 0 <= idx < len(candidates):
                print(f"[ATTACH_PW] Tab=pick idx={idx} url={_attach_display_url(candidates[idx].url)}")
                return candidates[idx]
        lw = _last_web_ready()
        if lw:
            return lw

    # 4b) current / active
    if mode in ("current", "active", "focused"):
        if candidates:
            print(f"[ATTACH_PW] Tab=current url={_attach_display_url(candidates[0].url)}")
            return candidates[0]
        lw = _last_web()
        if lw:
            return lw
        return pages[0]

    # 4c) last / newest
    if mode in ("last", "newest"):
        lw = _last_web()
        if lw:
            return lw
        print(f"[ATTACH_PW] Tab=last idx={len(pages)-1} url={_attach_display_url(pages[-1].url)}")
        return pages[-1]

    # 4d) best (score)
    if mode == "best":
        best_page, best_score = None, (0, 0)
        for p in pages:
            if not _is_candidate(p):
                continue
            sc = _score(p)
            if sc > best_score:
                best_score, best_page = sc, p
        if best_page:
            print(f"[ATTACH_PW] Tab=best score={best_score} url={_attach_display_url(best_page.url)}")
            return best_page

    # 4e) index numérique
    if mode.isdigit():
        idx = max(0, min(int(mode), len(candidates) - 1))
        if candidates:
            print(f"[ATTACH_PW] Tab=index idx={idx} url={_attach_display_url(candidates[idx].url)}")
            return candidates[idx]

    # Fallback final
    lw = _last_web_ready()
    if lw:
        return lw
    print("[ATTACH_PW] Tab=pages[0] (fallback absolu)")
    return pages[0]


def run_attach_login_takeover(page, pw, *, api_key: str, account_id: str, config: dict, platform=None) -> None:
    """
    Route 'login' (BLOC 1 natif Playwright) :
      1. Login si page de connexion détectée (auth_handler.login — natif Playwright ;
         ou platform.login() pour une plateforme configurée autre que TopSurveys)
      2. Navigation + sélection du survey (survey_navigator — natif Playwright ;
         ou platform.select_survey())
      3. Pont BLOC 1 → BLOC 2 : wrap page native en shim
      4. Résolution présélection + survey (BLOC 2/3 via shim)
    """
    import time as _time
    import Survey.survey_executor as survey_executor
    import Survey.survey_solver as survey_solver
    from Survey.survey_context import SurveyContext
    from preselection.auth_handler import handle_proxy_error_page_if_needed

    handle_proxy_error_page_if_needed(page)

    _is_topsurveys = platform is None or platform.get_platform_name() == "topsurveys"

    if _is_topsurveys:
        # Chemin TopSurveys existant — inchangé.
        from preselection.auth_handler import LOGIN_PAGE_SELECTORS
        from preselection.auth_handler import login as _do_login
        from preselection.survey_navigator import go_to_best_value_survey

        # Login si page de connexion détectée
        try:
            _on_login = any(page.query_selector(sel) for sel in LOGIN_PAGE_SELECTORS)
        except Exception:
            _on_login = False

        if _on_login:
            print("[ATTACH][LOGIN] Page de connexion détectée → login")
            _do_login(
                page,
                os.getenv("EMAIL") or config.get("Email", ""),
                os.getenv("PASSWORD") or config.get("Password", ""),
            )
            _time.sleep(2)

        # Sélection du survey (navigue vers l'onglet Sondages, choisit la meilleure carte)
        go_to_best_value_survey(page)
    else:
        # Stratégie additive : plateforme configurée != TopSurveys → routage via
        # l'interface Platform (login/select_survey), déjà implémentée pour ySense.
        print(
            f"[ATTACH][LOGIN] plateforme={platform.get_platform_name()} — "
            "routage via Platform.login()/select_survey()"
        )
        _login_config = {
            "Email": os.getenv("EMAIL") or config.get("Email", ""),
            "Password": os.getenv("PASSWORD") or config.get("Password", ""),
        }
        try:
            _session_expired = platform.is_session_expired(page)
        except Exception:
            _session_expired = True

        if _session_expired:
            print("[ATTACH][LOGIN] session absente/expirée → platform.login()")
            platform.login(page, _login_config)
            _time.sleep(2)

        platform.select_survey(page)

        # Repli propre : le pont BLOC 1 → BLOC 2 ci-dessous délègue au moteur de
        # présélection popup spécifique à TopSurveys (preselection.survey_handler),
        # sans équivalent implémenté pour les autres plateformes. On s'arrête donc
        # ici plutôt que de le forcer sur un DOM qui ne peut pas correspondre.
        print(
            f"[ATTACH][LOGIN] plateforme={platform.get_platform_name()} — survey "
            "sélectionné, pas de moteur de présélection générique disponible → arrêt contrôlé."
        )
        return

    # ── Pont BLOC 1 → BLOC 2 ─────────────────────────────────────────────────
    # Le popup de présélection est désormais ouvert. On enveloppe la Page native
    # dans le shim pour que survey_handler (BLOC 2) puisse consommer l'API façon Selenium.
    page._survey_account_id = account_id

    _ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
    survey_solver._current_survey_ctx = _ctx

    max_rounds = int(os.getenv("ATTACH_PRESELECTION_MAX_ROUNDS", "15"))
    transition_timeout_s = int(os.getenv("ATTACH_PRESELECTION_TRANSITION_TIMEOUT_S", "45"))

    from preselection.survey_handler import run_attach_preselection_takeover as _run_presel
    ok, reason = _run_presel(
        page,
        api_key,
        max_rounds=max_rounds,
        transition_timeout_s=transition_timeout_s,
        ctx=_ctx,
    )

    if not ok:
        print(f"[ATTACH][LOGIN] abandon présélection: reason={reason}")
        return

    print("[ATTACH][LOGIN] présélection terminée → résolution survey")
    max_steps = int(os.getenv("ATTACH_MAX_STEPS", "100"))
    for i in range(1, max_steps + 1):
        try:
            done = survey_executor.execute_survey_page(page, account_id, api_key, ctx=_ctx)
            _ctx.maybe_update_summary()
            print(f"[ATTACH][LOGIN→RES] step={i}/{max_steps} ok={done} url={_attach_display_url(page.url)}")
            if not done and survey_executor._attach_disq_stop_requested:
                print(f"[ATTACH][LOGIN→RES][DISQ] Page de disqualification détectée → arrêt immédiat boucle step={i}.")
                break
        except Exception as e:
            print(f"[ATTACH][LOGIN→RES][ERROR] step={i} {type(e).__name__}: {e}")
            break
        _time.sleep(0.6)

    print("[ATTACH][LOGIN] route terminée.")


def run_attach_preselection_takeover(driver, *, api_key: str, account_id: str, platform=None) -> None:
    """Attach takeover dédié au popup de présélection TopSurveys déjà affiché."""
    _is_topsurveys = platform is None or platform.get_platform_name() == "topsurveys"
    if not _is_topsurveys:
        # Repli propre : ce moteur (preselection.survey_handler) est spécifique au
        # popup de présélection TopSurveys ("popup de présélection TopSurveys déjà
        # affiché"), sans équivalent implémenté pour les autres plateformes —
        # abandon contrôlé plutôt que de le forcer sur un DOM qui ne peut pas
        # correspondre.
        print(
            f"[ATTACH][PRESEL] plateforme={platform.get_platform_name()} — moteur de "
            "présélection TopSurveys non applicable → abandon contrôlé."
        )
        return

    import Survey.survey_executor as survey_executor
    import Survey.survey_solver as survey_solver
    from Survey.survey_context import SurveyContext
    from preselection.survey_handler import run_attach_preselection_takeover as run_preselection_takeover

    _ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
    survey_solver._current_survey_ctx = _ctx

    driver._survey_account_id = account_id

    max_rounds = int(os.getenv("ATTACH_PRESELECTION_MAX_ROUNDS", "15"))
    transition_timeout_s = int(os.getenv("ATTACH_PRESELECTION_TRANSITION_TIMEOUT_S", "45"))
    ok, reason = run_preselection_takeover(
        driver,
        api_key,
        max_rounds=max_rounds,
        transition_timeout_s=transition_timeout_s,
        ctx=_ctx,
    )

    if not ok:
        print(f"[ATTACH][PRESEL] abandon contrôlé: reason={reason}")
        return

    print("[ATTACH][PRESEL] présélection terminée -> bascule en résolution survey")

    max_steps = int(os.getenv("ATTACH_MAX_STEPS", "100"))
    for i in range(1, max_steps + 1):
        try:
            done = survey_executor.execute_survey_page(driver, account_id, api_key, ctx=_ctx)
            _ctx.maybe_update_summary()
            print(f"[ATTACH][PRESEL->RES] step={i}/{max_steps} ok={done} url={_attach_display_url(driver.url)}")
            if not done and survey_executor._attach_disq_stop_requested:
                print(f"[ATTACH][PRESEL->RES][DISQ] Page de disqualification détectée → arrêt immédiat boucle step={i}.")
                break
        except Exception as e:
            print(f"[ATTACH][PRESEL->RES][ERROR] step={i} {type(e).__name__}: {e}")
            break
        time.sleep(0.6)

    print("[ATTACH][PRESEL] route terminée.")

def main():
    # Même garde qu'à l'import du module (cf. plus haut) : en mode attach,
    # l'environnement est déjà entièrement fourni par attach_tab.ps1, donc pas
    # besoin de relire receiver_config.json ici non plus.
    if os.getenv("BROWSER_MODE", "").strip().lower() != "attach":
        config = load_config()
    else:
        config = {}
    platform = get_platform()

    print(
        f"[BOOT] RUN_ENV={RUN_ENV} BROWSER_MODE={BROWSER_MODE} attach={is_attach_mode()}",
        flush=True,
    )

    # Note : attach est désormais contrôlé uniquement par BROWSER_MODE=attach,
    # indépendamment de RUN_ENV. Pas de garde supplémentaire nécessaire.

    account_id = (
        os.getenv("ACCOUNT_ID")
        or config.get("account_id")
    )
    email = (
        os.getenv("EMAIL") 
        or config.get("Email")
    )

    if not account_id:
        raise RuntimeError("ACCOUNT_ID introuvable")

    if is_attach_mode():
        # ⚠ ATTACH = LOCAL DEBUG TAKEOVER — Playwright natif (BLOC 1)
        # - pas de lock Postgres
        # - attachement CDP à Chrome déjà lancé (ATTACH_DEBUGGER_ADDRESS)
        # - pas de quit() (sinon tu fermes ton Chrome)
        attach_addr = os.getenv("ATTACH_DEBUGGER_ADDRESS", "").strip()
        if not attach_addr:
            raise RuntimeError("ATTACH_DEBUGGER_ADDRESS manquant en mode attach")

        # Résoudre la route AVANT de sélectionner l'onglet :
        #   - en mode "resolution", on exclut topsurveys.app de la liste affichée
        #   - le prompt (si ATTACH_ROUTE_PROMPT=1) doit précéder l'affichage des tabs
        attach_route = _get_attach_route()
        print(f"[ATTACH] route={attach_route}")

        from preselection.playwright_launcher import attach_browser_playwright
        _pw, _browser = attach_browser_playwright(attach_addr)
        _context = _browser.contexts[0]

        # En mode résolution, exclure les onglets de la plateforme configurée de
        # la sélection — dérivé de platform.get_domains() plutôt que câblé en dur
        # sur topsurveys.app (identique à l'existant pour TopSurveys : get_domains()
        # y retourne exactement ["topsurveys.app"]).
        _exclude = None
        if attach_route == "resolution":
            _exclude_domains = platform.get_domains() if platform else ["topsurveys.app"]
            _exclude = lambda url, _d=_exclude_domains: any(d in (url or "").lower() for d in _d)

        # Sélection de l'onglet actif (Playwright natif)
        page = _attach_select_tab_pw(_context, exclude_url_pred=_exclude)
        print(f"[ATTACH] Page sélectionnée url={_attach_display_url(page.url)}")

        # Bascule visuelle vers l'onglet sélectionné : certaines pages nécessitent
        # d'être au premier plan (focus/visibility) pour fonctionner correctement.
        # Additif uniquement : ne modifie pas la logique de sélection ci-dessus.
        try:
            page.bring_to_front()
            print("[ATTACH] Tab bring_to_front OK")
        except Exception as e:
            print(f"[ATTACH] Tab bring_to_front impossible: {e}")

        from Survey.survey_solver import get_current_survey_ctx
        start_debug_http_server(get_current_survey_ctx)

        api_key = (
            os.getenv("OPENAI_API_KEY")
            or config.get("openai_api_key")
            or config.get("OPENAI_API_KEY")
        )
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY introuvable (nécessaire en attach)")

        if attach_route == "login":
            # Route BLOC 1 complète : login + sélection survey + présélection + résolution
            run_attach_login_takeover(page, _pw, api_key=api_key, account_id=account_id, config=config, platform=platform)
        else:
            # Routes preselection / resolution : pont BLOC 1 → BLOC 2/3 immédiat via shim
            page._survey_account_id = account_id
            if attach_route == "preselection":
                run_attach_preselection_takeover(page, api_key=api_key, account_id=account_id, platform=platform)
            else:
                run_attach_takeover(page, api_key=api_key, account_id=account_id, platform=platform)
        return

    # setup_logging() attache un handler stdout au logger racine ("logging" stdlib).
    # ajout du paramètre account_id pour permettre la purge par compte:
    # sans handler configuré, logging.getLogger(...).info/debug (update_checker.py,
    # module "logging" stdlib) est avalé silencieusement par le handler de secours
    # Python (seuil WARNING), et warning/error y échappent vers stderr — un fichier
    # de log distinct de celui consulté par l'opérateur (bot_*_stdout.log vs
    # bot_*_stderr.log, cf. nssm_setup_bot.ps1). D'où l'absence totale de trace de
    # la vérification de mise à jour, quel que soit son issue.
    # Doit précéder check_and_apply() ci-dessous, seul appelant concerné par ce bug.
    setup_logging(account_id=account_id)

    # Vérification et application d'une mise à jour binaire avant tout démarrage.
    # No-op si UPDATE_CHECK_ENABLED != "1". Si une mise à jour est appliquée,
    # os.execv() remplace le processus et cette ligne ne retourne jamais.
    # Placé ici : account_id résolu, aucun lock ni driver acquis → relance propre.
    from update_checker import check_and_apply as _check_and_apply
    _check_and_apply(account_id)

    # FIX-A: install_sigterm_handler AVANT acquire_account_lock_or_exit.
    # Auparavant, un SIGTERM arrivant entre acquire et install_sigterm_handler
    # terminait le processus sans remettre cooldown_until_ts à zéro en Postgres,
    # forçant le scheduler à attendre l'expiration du TTL avant de relancer.
    install_sigterm_handler(account_id)
    install_sigint_handler(account_id)   # Ctrl+C / Windows bare-metal
    install_sigusr1_handler()

    acquire_account_lock_or_exit(account_id)
    mark_bot_running(account_id, email)
    from State.account_state import update_state as _update_state, _now as _now_ts
    from State.daily_target import ensure_daily_timer_started as _ensure_timer
    _boot_ts = _now_ts()
    _update_state(account_id, lambda st: (
        st.__setitem__("last_start_ts", _boot_ts),
        _ensure_timer(st, now_ts=_boot_ts),
    ))

    from Survey.survey_solver import get_current_survey_ctx
    start_debug_http_server(get_current_survey_ctx)

    notify_fn = build_notifier(config)

    # Vérification du seuil de redémarrages automatiques (crash-loop) : tourne
    # désormais au niveau module, avant check_license_or_exit() — voir le
    # commentaire correspondant en tête de fichier. Ne pas la réintroduire ici :
    # un second appel à check_and_record_start() dans le même run lirait le
    # sentinel EXIT_CRASH que le premier appel vient d'écrire et fausserait le
    # compteur (incrément à tort dès le premier démarrage sain).

    # Proxy-lock retiré : en prod on a 1 bot par proxy, donc lock proxy redondant
    runtime_ctx = {
        "driver": None,
        "session": {},
    }

    guard = None
    hot_reload_started = False

    if should_run_heartbeat():
        start_heartbeat_thread()
    heartbeat_started = True

    max_cycles = int(os.getenv("MAX_MAIN_CYCLES", "3") or "3")
    cycle = 0

    while cycle < max_cycles:
        cycle += 1
        driver = None
        _autosave_stop_event = None

        try:
            driver = launch_driver_or_fail(config, account_id)
            driver._survey_account_id = account_id
            
            runtime_ctx["driver"] = driver
            # PATCH: Stocker account_id sur driver pour acces dans survey_executor
            driver._survey_account_id = account_id

            _acct_env = os.getenv("ACCOUNT_ID", "").strip()

            def _soft_restart(reason):
                # 🔎 FIX : runtime_ctx["driver"] est figé au lancement initial et n'est
                # jamais mis à jour après un switch d'onglet interne (cf. survey_handler.py
                # ::_resync_live_page, qui republie désormais la page vivante vers le
                # RuntimeGuard à chaque resync). On préfère donc self.driver du guard
                # quand il est disponible et vivant — c'est la copie la plus fraîche.
                # Sans ce fix, soft_restart_cleanup()/safe_get() échouait systématiquement
                # avec "Target page, context or browser has been closed" dès qu'un
                # restart survenait après un survey ayant fait un switch d'onglet.
                _driver_for_restart = runtime_ctx["driver"]
                try:
                    _guard_driver = get_guard().driver
                    if _guard_driver is not None and not _guard_driver.is_closed():
                        _driver_for_restart = _guard_driver
                except Exception:
                    pass
                return soft_restart(
                    runtime_ctx["session"],
                    _driver_for_restart,
                    reason,
                    platform=platform,
                )

            # FIX: guard initialisé AVANT init_session_and_enter_surveys pour que
            # get_guard().pause() (ex: no_survey_available) écrive bien cooldown_until_ts en DB.
            if should_run_guard_monitor():
                if guard is None:
                    guard = start_runtime_guard(
                        account_id=account_id,
                        notify_fn=notify_fn,
                        on_soft_restart=_soft_restart,
                    )
                get_guard().attach_driver(driver)

            api_key, payout_name, payout_revolut_tag = init_session_and_enter_surveys(driver, config, account_id, notify_fn, platform=platform)

            runtime_ctx["session"] = {
                "account_id": account_id,
                "api_key": api_key,
                "payout_name": payout_name,
                "payout_revolut_tag": payout_revolut_tag,
                "email": config.get("Email", ""),
                "password": config.get("Password", ""),
            }

            run_main_loop(driver, api_key, account_id, payout_name=payout_name, payout_revolut_tag=payout_revolut_tag, platform=platform)

        except SystemExit:
            raise

        except Exception as e:
            print(f"[MAIN][ERROR] cycle={cycle}/{max_cycles} {type(e).__name__}: {e}")
            traceback.print_exc()
            # FIX-B2 (partie catch): libération lock en cas de crash Exception
            if not is_attach_mode():
                try:
                    from State.account_state import update_state
                    update_state(account_id, lambda st: (
                        st.__setitem__("status", "idle"),
                        st.__setitem__("cooldown_until_ts", "1970-01-01T00:00:00"),
                        st.__setitem__("last_stop_reason", f"crash_{type(e).__name__}"),
                    ))
                except Exception as _le:
                    print(f"[MAIN][WARN] Impossible de libérer le lock après crash: {_le}")
            time.sleep(2)
            continue

        finally:
            # FIX-B2: driver.quit() garanti sur toute sortie (Exception, KeyboardInterrupt, etc.)
            # SystemExit propagera naturellement après ce bloc.
            # FIX-B3: ne pas référencer 'e' ici (Python 3 le supprime en fin de bloc except)
            # FIX-B4: pas de 'continue' ici — supprime les SystemExit et empêche l'arrêt propre
            try:
                if driver and (not is_attach_mode()):
                    # Terminer le processus Chrome lancé par subprocess.Popen
                    if hasattr(driver, '_chrome_proc') and driver._chrome_proc:
                        try:
                            driver._chrome_proc.terminate()
                        except Exception:
                            pass
                    # Terminer le relay pproxy si présent
                    if hasattr(driver, '_proxy_relay_proc') and driver._proxy_relay_proc:
                        try:
                            driver._proxy_relay_proc.terminate()
                        except Exception:
                            pass
                    # FIX: driver est une Page Playwright (launch_browser_playwright),
                    # pas un driver Selenium — pas de méthode quit(). L'appeler levait
                    # un AttributeError silencieusement avalé ici, donc context.close()
                    # et l'arrêt de la connexion Playwright (driver._pw.stop()) n'étaient
                    # jamais exécutés. La connexion Playwright du cycle précédent restait
                    # active, et le rappel de sync_playwright().start() au cycle suivant
                    # (même process/thread) échouait avec "Sync API inside the asyncio
                    # loop". context.close() ferme le BrowserContext persistant (et le
                    # process Chrome sous-jacent) ; pw.stop() libère la connexion.
                    try:
                        driver.context.close()
                    except Exception:
                        pass
                    try:
                        if getattr(driver, "_pw", None):
                            driver._pw.stop()
                    except Exception:
                        pass
            except Exception:
                pass

    # Si on sort de la boucle, libérer le slot Postgres (le scheduler relancera)
    if not is_attach_mode():
        try:
            from State.account_state import update_state
            update_state(account_id, lambda st: (
                st.__setitem__("status", "idle"),
                st.__setitem__("cooldown_until_ts", "1970-01-01T00:00:00"),
                st.__setitem__("last_stop_reason", "max_main_cycles_reached"),
            ))
        except Exception as _le:
            print(f"[MAIN][WARN] Impossible de libérer le lock en fin de cycles: {_le}")
        # Recyclage volontaire et sain (pas un crash) : sans cet appel, le sentinel
        # EXIT_CRASH écrit par check_and_record_start() au début de CE run reste en
        # place (ce chemin ne passait jusqu'ici jamais par record_exit()) — le
        # prochain démarrage le lirait comme un crash et incrémenterait à tort
        # restart_count, pouvant faire atteindre le seuil EXIT_FATAL après plusieurs
        # recyclages sains consécutifs (voir Utils/AUDIT_ARRET_RELANCE_BOTS.md, Observation 5b).
        from bot_supervisor import record_exit, EXIT_VOLUNTARY
        record_exit(account_id, EXIT_VOLUNTARY, "max_main_cycles_reached")
    raise SystemExit("max_main_cycles_reached")
        
if __name__ == "__main__":
    main()