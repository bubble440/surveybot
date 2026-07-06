import os, random
from config import is_attach_mode, is_prod_like, should_run_guard_monitor, should_run_hot_reload

# SNAP_ENABLED est une variable GLOBAL_CONFIG : en build compilé (Nuitka), elle provient
# exclusivement de global_config.py, jamais de l'environnement du process (cf. config.py).
# En dev/attach (global_config.py absent du projet), fallback os.getenv.
try:
    from global_config import SNAP_ENABLED  # type: ignore
except ImportError:
    SNAP_ENABLED = os.getenv("SNAP_ENABLED", "")

# ---------- PID file (bare-metal Windows) ----------

def _pid_path(account_id: str) -> str:
    """Retourne le chemin du fichier PID pour ce bot (pids\bot_<id>.pid)."""
    base = os.path.dirname(os.path.abspath(__file__))
    pid_dir = os.path.join(base, "pids")
    os.makedirs(pid_dir, exist_ok=True)
    return os.path.join(pid_dir, f"bot_{account_id}.pid")

def write_pid_file(account_id: str) -> None:
    """Écrit le PID courant dans pids\bot_<account_id>.pid."""
    if is_attach_mode():
        return
    try:
        path = _pid_path(account_id)
        with open(path, "w") as f:
            f.write(str(os.getpid()))
        print(f"[PID] Fichier écrit : {path} (pid={os.getpid()})")
    except Exception as e:
        print(f"[PID][WARN] Impossible d'écrire le fichier PID : {e}")

def delete_pid_file(account_id: str) -> None:
    """Supprime pids\bot_<account_id>.pid à l'arrêt propre."""
    if is_attach_mode():
        return
    try:
        path = _pid_path(account_id)
        if os.path.exists(path):
            os.remove(path)
            print(f"[PID] Fichier supprimé : {path}")
    except Exception as e:
        print(f"[PID][WARN] Impossible de supprimer le fichier PID : {e}")


from Management.guards.runtime_guard import RuntimeGuard, StopReason, set_guard, get_guard
from State.daily_target import DAILY_TARGET_EUR, ensure_daily_timer_started
from Cash.payout import MIN_CASHOUT_EUR
import time, sys, logging, threading, traceback, signal, Cash.payout as payout
from preselection.playwright_launcher import launch_browser_playwright
from preselection.auth_handler import login
from preselection.survey_navigator import go_to_best_value_survey
from preselection.survey_handler import run_survey
from Management.notifier import send_telegram
from State.account_state import update_state, load_state, try_acquire_cooldown_slot, _now, load_datadome_cookies, load_cookies
from preselection.auth_handler import is_session_expired, handle_proxy_error_page_if_needed
from Management.pause_policy import PausePolicy
import subprocess
from Cash.payout import _payout_and_check_daily_stop


def acquire_account_lock_or_exit(account_id: str, ttl_sec: int = 240):
    ok = try_acquire_cooldown_slot(account_id=account_id, ttl_sec=ttl_sec)
    if not ok:
        print(f"[COOLDOWN] Account {account_id} en cooldown ou déjà actif → exit")
        sys.exit(0)

def safe_get(driver, url, base_delay=4):
    """
    Navigation sécurisée : s'assure qu'un driver valide existe.
    - Timeout 70s pour éviter les hangs infinis en ECS.
    - Sur PlaywrightTimeoutError : window.stop() + chargement partiel accepté.
    - Sur toute autre exception : log + re-raise.
    """
    if driver is None:
        raise RuntimeError("SAFE_GET appelé avec driver=None")

    page = driver
    try:
        try:
            page.goto(url, timeout=70_000, wait_until="domcontentloaded")
            handle_proxy_error_page_if_needed(driver)
            if is_session_expired(driver):
                msg = "🔐 Session expirée — ré-authentification manuelle requise."
                print(msg)
                try:
                    get_guard().notify_fn(msg)
                except Exception:
                    pass
                get_guard().pause(
                    PausePolicy.UNTIL_MANUAL,
                    StopReason.SESSION_EXPIRED,
                )
                raise SystemExit("session_expired")
            print(f"[SAFE_GET] done get: {url}")
            return
        except SystemExit:
            raise
        except Exception as e:
            if type(e).__name__ == "TimeoutError":
                print(f"[SAFE_GET][WARN] Timeout page load vers {url} -> window.stop()")
                try:
                    page.evaluate("window.stop()")
                except Exception:
                    pass
            else:
                raise
    except SystemExit:
        raise
    except Exception as e:
        print(f"[SAFE_GET] Navigation impossible vers {url}: {e}")
        raise

def install_sigusr1_handler():
    """
    Handler SIGUSR1 : dump terminal du SurveyContext actif sans interrompre le bot.
    Usage : kill -SIGUSR1 <pid>
    Non disponible sur Windows — ignoré silencieusement.
    """
    if not hasattr(signal, "SIGUSR1"):
        print("[SIGUSR1] Non supporté sur cette plateforme (Windows?), ignoré.")
        return

    def _handle_sigusr1(signum, frame):
        from Survey.survey_solver import get_current_survey_ctx
        ctx = get_current_survey_ctx()
        if ctx is None:
            print("[SIGUSR1] Aucun SurveyContext actif.")
        else:
            ctx.print_debug()

    signal.signal(signal.SIGUSR1, _handle_sigusr1)
    print("[SIGUSR1] Handler installé. Dump via : kill -SIGUSR1", os.getpid())

def install_sigterm_handler(account_id: str):
    signal.signal(signal.SIGTERM, _make_stop_handler(account_id, sig_name="SIGTERM"))

def install_sigint_handler(account_id: str):
    """
    Handler SIGINT (Ctrl+C / Windows bare-metal).
    Même comportement que SIGTERM : libère le slot Postgres, supprime le PID, exit propre.
    """
    signal.signal(signal.SIGINT, _make_stop_handler(account_id, sig_name="SIGINT"))

def _make_stop_handler(aid: str, sig_name: str = "SIGTERM"):
    """
    Fabrique un handler d'arrêt propre pour SIGTERM ou SIGINT.
    Libère le slot Postgres, supprime le fichier PID, stoppe le heartbeat, puis exit.
    """
    def _handle(signum, frame):
        print(f"🛑 {sig_name} reçu | account_id={aid}")

        try:
            update_state(aid, lambda st: (
                st.__setitem__("ecs_stop_requested", True),
                st.__setitem__("ecs_stop_ts", _now()),
                st.__setitem__("ecs_stop_notified", False),
                st.__setitem__("status", "idle"),
                st.__setitem__("cooldown_until_ts", "1970-01-01T00:00:00"),
            ))
        except Exception as e:
            print(f"[{sig_name}][WARN] update_state échoué:", e)

        finally:
            stop_heartbeat_thread()
            delete_pid_file(aid)
            print(f"{sig_name} traité → exit immédiat")
            raise SystemExit(f"{sig_name.lower()}_received")

    return _handle

def build_notifier(config):
    tg_token = os.getenv("telegram_bot_token", "").strip()
    tg_chat  = os.getenv("telegram_chat_id", "").strip()

    def _notify(msg: str):
        # Console (toujours)
        print(f"[WATCHDOG] {msg}")
        # Telegram si configuré
        if tg_token and tg_chat:
            try:
                ok = send_telegram(msg, tg_token, tg_chat)
                if not ok:
                    print("[WATCHDOG][WARN] Telegram a répondu 'not ok'.")
            except Exception as e:
                print(f"[WATCHDOG][WARN] Telegram a échoué: {e}")
        else:
            print("[WATCHDOG] Telegram non configuré, notification console uniquement.")

        # Petit bip Windows si possible (facultatif)
        try:
            import sys
            if sys.platform.startswith("win"):
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        
    return _notify

def soft_restart_cleanup(driver, platform=None):
    """
    Prépare un soft restart.
    IMPORTANT : se replacer sur la page APP avant la logique payout,
    sinon la lecture du solde échoue sur la landing marketing.
    """
    _home = platform.get_home_url() if platform else "https://app.topsurveys.app/surveys"
    try:
        safe_get(driver, _home)
    except Exception as e:
        print(f"[SOFT_RESTART][WARN] échec accès {_home}: {e}")


def soft_restart_resume(ctx, driver, platform=None):
    from Survey.survey_context import SurveyContext

    # Détection de redirection silencieuse vers la landing/login page.
    # safe_get() ne la détecte pas (pas d'erreur HTTP), on la sonde via ses sélecteurs DOM.
    # Deux interfaces de login possibles :
    #   topsurveys.app     → check-email-field-input
    #   app.topsurveys.app → app-page-email-field-input
    from preselection.auth_handler import LOGIN_PAGE_SELECTORS
    _page = driver
    _on_login_page = any(
        _page.query_selector(sel)
        for sel in LOGIN_PAGE_SELECTORS
    )
    if _on_login_page:
        print("[SOFT_RESTART] session expirée détectée → re-login")
        if platform:
            platform.login(driver, {"Email": ctx["email"], "Password": ctx["password"]})
        else:
            login(driver, ctx["email"], ctx["password"])
        if any(_page.query_selector(sel) for sel in LOGIN_PAGE_SELECTORS):
            raise RuntimeError("soft_restart_resume: re-login échoué, page de login toujours présente")

    survey_ctx = SurveyContext(session_id=ctx["account_id"], openai_api_key=ctx["api_key"])
    if platform:
        platform.select_survey(driver)
    else:
        go_to_best_value_survey(driver)
    run_survey(
        driver,
        ctx["api_key"],
        account_id=ctx["account_id"],
        ctx=survey_ctx,
        payout_name=ctx.get("payout_name", ""),
        payout_revolut_tag=ctx.get("payout_revolut_tag", ""),
        platform=platform,
    )

def soft_restart(ctx, driver, reason, platform=None):
    print(f"[SOFT_RESTART] {reason}")

    soft_restart_cleanup(driver, platform=platform)
    time.sleep(1)

    # DAILY STOP : si l'objectif journalier (1€) est atteint, on s'arrête
    from Management.guards.runtime_guard import get_guard, StopReason
    from Management.pause_policy import PausePolicy
    guard = get_guard()
    # FIX-C: le try/except AttributeError était du dead code en prod (RuntimeGuard a
    # toujours state.earnings_today_eur). On utilise getattr pour gérer proprement le
    # cas _NullGuard (pas de .state) sans branche redondante — Fix-B garantit
    # que guard.state.earnings_today_eur est déjà hydraté depuis Postgres au démarrage.
    earnings = float(getattr(getattr(guard, "state", None), "earnings_today_eur", 0.0))
    if earnings >= DAILY_TARGET_EUR:
        print(f"[DAILY_STOP] {earnings:.2f}€ >= {DAILY_TARGET_EUR}€ → arrêt journalier")
        guard.pause(PausePolicy.DAILY_RESET, StopReason.DAILY_TARGET_REACHED)
        return  # jamais atteint (pause lève SystemExit)

    soft_restart_resume(ctx, driver, platform=platform)

def start_runtime_guard(account_id: str, notify_fn, on_soft_restart):
    state = load_state(account_id)

    guard = RuntimeGuard(
        account_id=account_id,
        idle_timeout_sec=120,
        restart_cooldown_sec=60,
        max_errors_in_row=5,
        max_runtime_sec=2 * 3600,
        daily_target_eur=DAILY_TARGET_EUR,
        notify_fn=notify_fn,
        on_soft_restart=on_soft_restart,
    )

    # FIX-B: réhydrater les gains du jour depuis Postgres avant de démarrer le guard.
    # Sans ce patch, guard.state.earnings_today_eur démarrait systématiquement à 0.0,
    # même si une session précédente (même jour) avait déjà atteint le daily target.
    # Conséquence : la protection DAILY_TARGET_REACHED du _monitor_loop était aveugle
    # aux gains des sessions antérieures → le bot pouvait tourner au-delà du plafond.
    # Le fallback AttributeError dans soft_restart / survey_solver était également
    # du dead code car guard.state.earnings_today_eur est toujours accessible (= 0.0).
    try:
        persisted_earnings = float(state.get("earnings_today_eur") or 0.0)
        if persisted_earnings > 0.0:
            guard.state.earnings_today_eur = persisted_earnings
            print(f"[RUNTIME_GUARD] earnings_today_eur restauré depuis Postgres: {persisted_earnings:.2f}€")
    except Exception as _e:
        print(f"[RUNTIME_GUARD][WARN] Impossible de restaurer earnings_today_eur: {_e}")

    set_guard(guard)
    guard.start()

    _start_ts = _now()

    def _mark_start(st):
        st["last_start_ts"] = _start_ts
        ensure_daily_timer_started(st, now_ts=_start_ts)

    update_state(account_id, _mark_start)

    return guard

_HEARTBEAT_STARTED = False
# H5: event pour arrêt propre du thread heartbeat
_HEARTBEAT_STOP = threading.Event()

def _heartbeat():
        # Fréquence heartbeat (coût) vs TTL (robustesse)
        # - interval: toutes les 60s par défaut
        # - jitter: évite que 100 bots heartbeat exactement en même temps (pics WCU)
        interval = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "60") or "60")
        jitter = float(os.getenv("HEARTBEAT_JITTER_SEC", "3") or "3")

        while not _HEARTBEAT_STOP.is_set():
            try:
                get_guard().heartbeat()
            except Exception:
                # Heartbeat best-effort : ne doit jamais tuer le bot
                pass

            # Jitter aléatoire [0..jitter] pour lisser la charge en prod
            sleep_s = interval + (random.random() * jitter if jitter > 0 else 0.0)
            # H5: utiliser wait() au lieu de sleep() pour répondre au stop event
            _HEARTBEAT_STOP.wait(timeout=sleep_s)

def stop_heartbeat_thread():
    """Arrête proprement le thread heartbeat (appelé avant SystemExit propre)."""
    _HEARTBEAT_STOP.set()

def start_heartbeat_thread():
    global _HEARTBEAT_STARTED
    if _HEARTBEAT_STARTED:
        return
    _HEARTBEAT_STARTED = True
    _HEARTBEAT_STOP.clear()
    threading.Thread(target=_heartbeat, name="heartbeat", daemon=True).start()

def setup_logging():
    # 2) niveau depuis l'env (default INFO)
    _level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, _level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("surveybot")

    log.info("BOOT: surveybot starting")  # ✅ maintenant log est défini

    # 3) loguer les exceptions non-captées (sinon elles tuent la task en silence)
    def _excepthook(exc_type, exc, tb):
        logging.getLogger("uncaught").exception("UNCAUGHT EXCEPTION", exc_info=(exc_type, exc, tb))
    sys.excepthook = _excepthook

def mark_bot_running(account_id: str, email):
    print(f"🚀 Démarrage surveybot pour account_id={account_id}, EMAIL={email}")
    write_pid_file(account_id)
    update_state(account_id, lambda st: (
        st.__setitem__("status", "running"),
        st.__setitem__("last_boot_ts", _now())
    ))

def restore_session_cookies(driver, account_id: str) -> None:
    """
    Restaure tous les cookies de session depuis cookie_store via CDP.
    Appelé après le lancement de Chrome, avant le premier chargement de page.
    Les cookies expirés sont filtrés. Les échecs par cookie sont loggés et ignorés.
    Ne bloque jamais le démarrage du bot.
    """
    import time as _time
    from Survey.log_utils import log_info, log_debug
    _TAG = "SESSION_RESTORE"
    try:
        all_cookies = load_cookies(account_id)
    except Exception as e:
        log_info(_TAG, f"load_cookies() a échoué, démarrage sans cookies: {e}")
        return
    if not all_cookies:
        return
    for domain, cookies in all_cookies.items():
        restored = 0
        for cookie in cookies:
            try:
                expires = cookie.get("expires")
                if expires is not None and expires != -1 and expires < _time.time():
                    log_debug(_TAG, f"Cookie expiré ignoré: name={cookie.get('name')} domain={domain}")
                    continue
                params = {"name": cookie["name"], "value": cookie["value"], "domain": domain}
                if "path" in cookie:
                    params["path"] = cookie["path"]
                if "secure" in cookie:
                    params["secure"] = cookie["secure"]
                if "httpOnly" in cookie:
                    params["httpOnly"] = cookie["httpOnly"]
                if expires is not None and expires != -1:
                    params["expires"] = expires
                if "sameSite" in cookie:
                    params["sameSite"] = cookie["sameSite"]
                driver.execute_cdp_cmd("Network.setCookie", params)
                restored += 1
            except Exception as e:
                log_info(_TAG, f"Cookie ignoré: name={cookie.get('name')} domain={domain}: {e}")
        log_info(_TAG, f"{restored} cookie(s) restauré(s) pour domaine={domain}")


def restore_datadome_cookies(driver, account_id: str) -> None:
    """
    Restaure les cookies DataDome persistés dans le navigateur via CDP.
    Appelé après le lancement de Chrome, avant le premier chargement de page.
    Les échecs par cookie sont loggés et ignorés — ne bloque jamais le démarrage.
    """
    from Survey.log_utils import log_info, log_debug
    _TAG = "DATADOME_RESTORE"
    cookies = load_datadome_cookies(account_id)
    if not cookies:
        return
    log_info(_TAG, f"{len(cookies)} cookie(s) DataDome à restaurer")
    for domain, cookie_value in cookies.items():
        try:
            driver.execute_cdp_cmd("Network.setCookie", {
                "name": "datadome",
                "value": cookie_value,
                "domain": domain,
                "path": "/",
            })
            log_info(_TAG, f"Cookie restauré pour domaine={domain}")
        except Exception as e:
            log_info(_TAG, f"Restauration ignorée pour domaine={domain}: {e}")


def launch_driver_or_fail(config, account_id: str):
    try:
        driver = launch_browser_playwright(config)
        if driver is None:
            raise RuntimeError("launch_browser_playwright() a retourné None")
        if should_run_guard_monitor():
            get_guard().attach_driver(driver)
        return driver
    except Exception as e:
        print("[LAUNCH][FATAL] Impossible de lancer le navigateur :", e)
        traceback.print_exc()

        if is_prod_like():
        # 🔴 état propre pour le scheduler
            update_state(account_id, lambda st: (
                st.__setitem__("status", "idle"),
                st.__setitem__("cooldown_until_ts", "1970-01-01T00:00:00"),
                st.__setitem__("last_stop_reason", "browser_launch_failed"),
            ))
        delete_pid_file(account_id)
        raise SystemExit("browser_launch_failed")

def start_debug_http_server(survey_ctx_getter):
    """
    Serveur HTTP de debug accessible sur chrome_port + 1000.
    Exemple : bot sur port 9222 → http://localhost:10222/ctx
    Uniquement en mode attach — ignoré en prod.
    """
    if not is_attach_mode():
        return

    attach_port = int(os.getenv("ATTACH_DEBUGGER_ADDRESS", ":0").split(":")[-1] or 0)
    if not attach_port:
        return

    debug_port = attach_port + 1000
    import http.server, threading, io
    from contextlib import redirect_stdout

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ctx = survey_ctx_getter()
            buf = io.StringIO()
            if ctx is None:
                buf.write("Aucun SurveyContext actif.\n")
            else:
                with redirect_stdout(buf):
                    ctx.print_debug()
            body = buf.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # Silence les logs HTTP dans le terminal du bot

    server = http.server.HTTPServer(("127.0.0.1", debug_port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[DEBUG_HTTP] Serveur actif → http://localhost:{debug_port}/ctx")
    
def init_session_and_enter_surveys(driver, config, account_id: str, notify_fn, platform=None):
    api_key = config.get("openai_api_key")
    payout_name = config.get("payout_name")
    payout_revolut_tag = config.get("payout_revolut_tag")

    _home_url = platform.get_home_url() if platform else "https://www.topsurveys.app"
    safe_get(driver, _home_url)
    print("🚀 Brave lancé.")

    _SESSION_SEL = "[data-test-id='surveys-nav']"
    _page = driver
    _session_active = False
    try:
        _page.wait_for_selector(_SESSION_SEL, state="attached", timeout=8_000)
        _session_active = True
    except Exception:
        pass

    if _session_active:
        print("[INIT] session active détectée — login ignoré")
        if SNAP_ENABLED.strip() == "1":
            from Management.snap_uploader import new_survey, capture_and_upload
            new_survey()
            capture_and_upload(driver, "survey_account")
    else:
        if platform:
            platform.login(driver, config)
        else:
            email = config.get("Email")
            password = config.get("Password")
            login(driver, email, password)
        # Après login, attendre que la page soit hydratée avant de continuer.
        try:
            _page.wait_for_selector(_SESSION_SEL, state="attached", timeout=30_000)
            print("[LOGIN] surveys-nav détecté post-login — page prête.")
        except Exception:
            print("[LOGIN][WARN] surveys-nav non détecté après 30 s — on continue quand même.")

    # try:
    #     _payout_and_check_daily_stop(driver, account_id, email=config.get("Email", ""))  # retrait + DAILY STOP
    # except Exception as e:
    #     print(f"[PAYOUT][WARN] Encaissement automatique: {e}")

    # Attente que la page soit pleinement chargée et hydratée avant de chercher un survey.
    # On réutilise _SESSION_SEL ([data-test-id='surveys-nav']) : il est présent dès
    # que l'app Vue est loggée et rendue, sans dépendre de la disponibilité de surveys.
    # Timeout généreux (30 s) pour absorber les démarrages lents en prod headless.
    # Si le sélecteur n'apparaît pas dans le délai, on continue quand même (best-effort).
    try:
        _page.wait_for_selector(_SESSION_SEL, state="attached", timeout=30_000)
        print("[INIT] surveys-nav détecté — page prête, lancement select_survey.")
    except Exception:
        print("[INIT][WARN] surveys-nav non détecté après 30 s — select_survey lancé quand même.")

    if platform:
        platform.select_survey(driver)
    else:
        go_to_best_value_survey(driver)

    return api_key, payout_name, payout_revolut_tag

def start_hot_reload_thread():
    global _HOT_RELOAD_STARTED
    if not should_run_hot_reload():
        print("[HOT_RELOAD] Ignoré hors mode attach.")
        return
    if _HOT_RELOAD_STARTED:
        return
    _HOT_RELOAD_STARTED = True

    import Survey.survey_executor as _se
    from hot_reload.hot_reload import ModuleReloader

    reloader = ModuleReloader(
        [
            "captcha.captcha_solver",
            "captcha.datadome_handler",
            "captcha.normal_captcha",
            "captcha.recaptcha_handler",
            "captcha.recaptcha_utils",
            "captcha.tencent_handler",
            "Survey.action_dispatcher",
            "Survey.action_types",
            "Survey.batch_response_parser",
            "Survey.cta_handler",
            "Survey.dom_analyzer",
            "Survey.dom_classifier",
            "Survey.dom_context_mapper",
            "Survey.dom_extractors_areyounet",
            "Survey.dom_extractors_decipher",
            "Survey.dom_extractors_misc",
            "Survey.dom_frame_selector",
            "Survey.dom_question_extractor",
            "Survey.dom_registry",
            "Survey.dom_selection_rules",
            "Survey.dom_utils",
            "Survey.dropdown_block_resolver",
            "Survey.frame_utils",
            "Survey.input_checkbox",
            "Survey.input_dropdown",
            "Survey.input_frame",
            "Survey.input_handler",
            "Survey.input_matrix",
            "Survey.input_radio",
            "Survey.input_slider",
            "Survey.input_text",
            "Survey.input_utils",
            "Survey.page_snapshot",
            "Survey.prompt_builder",
            "Survey.question_block_analyzer",
            "Survey.question_block_resolver",
            "Survey.survey_executor",
            "Survey.survey_solver",
            "preselection.question_analyzer",
            "preselection.question_validation",
            "preselection.response_executor",
            "preselection.survey_handler",
            "Management.pause_policy",
            "Management.redirect_watcher",
            "Management.guards.runtime_guard",
            "Management.guards.survey_difficulty_guard",
        ],
        poll_interval=0.5,
    )

    def _on_change(reloaded):
        nonlocal _se
        if "Survey.survey_executor" in reloaded:
            _se = reloaded["Survey.survey_executor"]
        print(" Modules rechargés:", ", ".join(reloaded.keys()))

    threading.Thread(
        target=reloader.watch_loop,
        args=(_on_change,),
        daemon=True,
    ).start()

_HOT_RELOAD_STARTED = False

def run_main_loop(driver, api_key: str, account_id: str, payout_name: str = "", payout_revolut_tag: str = "", platform=None):
    from Survey.survey_context import SurveyContext

    survey_ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
    run_survey(
        driver,
        api_key,
        account_id=account_id,
        ctx=survey_ctx,
        payout_name=payout_name,
        payout_revolut_tag=payout_revolut_tag,
        platform=platform,
    )

    # Vérification mise à jour du code au retour au listing (entre deux cycles).
    # No-op si UPDATE_CHECK_ENABLED != "1" ou si git est inaccessible.
    # Si une mise à jour est disponible : git pull + os.execv() (ne retourne pas).
    from update_checker import check_and_apply
    check_and_apply(account_id)

    # H1: en prod le bot doit quitter proprement (pas bloquer Chrome indéfiniment)
    if is_attach_mode():
        print("Script terminé. Navigateur maintenu ouvert pour inspection.")
        while True:
            time.sleep(999)