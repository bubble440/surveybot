import os, random
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"
from config import is_prod_like, should_run_guard_monitor, should_run_hot_reload

from Management.guards.runtime_guard import RuntimeGuard, StopReason, set_guard, get_guard
from State.daily_target import DAILY_TARGET_EUR, ensure_daily_timer_started
from Cash.payout import MIN_CASHOUT_EUR
import time, sys, logging, threading, traceback, signal, socket, Cash.payout as payout
from preselection.playwright_launcher import launch_browser
from preselection.auth_handler import login, snap
from preselection.survey_navigator import go_to_best_value_survey
from preselection.survey_handler import run_survey
from Management.notifier import send_telegram
from State.account_state import update_state, load_state, save_state, try_acquire_account_lock, _now
from selenium.common.exceptions import TimeoutException
from preselection.auth_handler import is_session_expired
from Management.pause_policy import PausePolicy
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def acquire_account_lock_or_exit(account_id: str, ttl_sec: int = 240):
    task_id = os.getenv("ECS_TASK_ID") or socket.gethostname()
    ok = try_acquire_account_lock(account_id=account_id, owner=task_id, ttl_sec=ttl_sec)
    if not ok:
        print(f"[LOCK] Account {account_id} déjà utilisé → exit")
        sys.exit(0)

def safe_get(driver, url):
    """
    Navigation sécurisée : s'assure qu'un driver valide existe.
    - Ajoute un timeout pour éviter les hangs infinis en ECS.
    - Fallback: stoppe le chargement et continue.
    """
    if driver is None:
        raise RuntimeError("SAFE_GET appelé avec driver=None")

    try:
        if not hasattr(driver, "window_handles") or not driver.window_handles:
            raise RuntimeError("Aucune fenêtre active")

        driver.switch_to.window(driver.window_handles[-1])

        # 🔒 évite blocage infini
        driver.set_page_load_timeout(70)

        try:
            print(f"[SAFE_GET] start get: {url}")
            driver.get(url)
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
        except TimeoutException:
            print(f"[SAFE_GET][WARN] Timeout page load vers {url} -> window.stop()")
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass

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
    signal.signal(signal.SIGTERM, _make_sigterm_handler(account_id))

def _make_sigterm_handler(aid: str):
    """
    Handler SIGTERM (ECS) : marque l'arrêt demandé dans l'état du compte.
    - On capture 'aid' via closure pour éviter les variables globales non définies.
    """
    def _handle_sigterm(signum, frame):
        print(f"🛑 SIGTERM reçu depuis ECS | account_id={aid}")

        try:
            update_state(aid, lambda st: (
                st.__setitem__("ecs_stop_requested", True),
                st.__setitem__("ecs_stop_ts", _now()),
                st.__setitem__("ecs_stop_notified", False),  # reset anti-spam à chaque SIGTERM
                st.__setitem__("status", "idle"),
                st.__setitem__("lock_owner", ""),
                st.__setitem__("lock_until_ts", "1970-01-01T00:00:00")
            ))
        except Exception as e:
            print("[SIGTERM][WARN] update_state échoué:", e)
        
        finally:
            stop_heartbeat_thread()
            print("SIGTERM traité → exit immédiat")
            raise SystemExit("ecs_sigterm")

    return _handle_sigterm

def build_notifier(config):
    tg_token = config.get("telegram_bot_token")
    tg_chat  = config.get("telegram_chat_id")

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

def soft_restart_cleanup(driver):
    """
    Prépare un soft restart.
    IMPORTANT : se replacer sur la page APP (app.topsurveys.app) avant la logique payout,
    sinon la lecture du solde échoue sur la landing marketing.
    """
    from Survey.survey_solver import _close_other_tabs_in_current_session
    _close_other_tabs_in_current_session(driver)

    # Plus fiable que la landing + clic CTA
    try:
        safe_get(driver, "https://app.topsurveys.app/surveys")
    except Exception as e:
        print(f"[SOFT_RESTART][WARN] échec accès app /surveys: {e}")
        # Fallback best-effort : on retente la landing (au pire, le flow suivant récupère)
        safe_get(driver, "https://www.topsurveys.app")

def soft_restart_payout(ctx, driver):
    """
    Encaissement best-effort.
    En local / ctx minimal, on peut ne pas avoir payout_name/tag → on skip proprement.
    """
    payout_name = (ctx.get("payout_name") or "").strip()
    payout_tag  = (ctx.get("payout_revolut_tag") or "").strip()

    payout.check_and_cashout_if_needed(
        driver,
        account_id=ctx["account_id"],
        min_amount_eur=MIN_CASHOUT_EUR,
        cashout_order=("revolut", "paypal"),
        revolut_fullname=payout_name,
        revolut_tag=payout_tag,
    )

def soft_restart_resume(ctx, driver):
    from Survey.survey_context import SurveyContext

    survey_ctx = SurveyContext(session_id=ctx["account_id"], openai_api_key=ctx["api_key"])
    go_to_best_value_survey(driver)
    run_survey(
        driver,
        ctx["api_key"],
        account_id=ctx["account_id"],
        ctx=survey_ctx,
        payout_name=ctx.get("payout_name", ""),
        payout_revolut_tag=ctx.get("payout_revolut_tag", ""),
    )

def soft_restart(ctx, driver, reason):
    print(f"[SOFT_RESTART] {reason}")

    soft_restart_cleanup(driver)
    time.sleep(3)

    try:
        soft_restart_payout(ctx, driver)
    except Exception as e:
        print("[SOFT_RESTART][PAYOUT][WARN]", e)

    # DAILY STOP : si l'objectif journalier (1€) est atteint, on s'arrête
    from Management.guards.runtime_guard import get_guard, StopReason
    from Management.pause_policy import PausePolicy
    guard = get_guard()
    # FIX-C: le try/except AttributeError était du dead code en prod (RuntimeGuard a
    # toujours state.earnings_today_eur). On utilise getattr pour gérer proprement le
    # cas _NullGuard (pas de .state) sans branche DynamoDB redondante — Fix-B garantit
    # que guard.state.earnings_today_eur est déjà hydraté depuis DynamoDB au démarrage.
    earnings = float(getattr(getattr(guard, "state", None), "earnings_today_eur", 0.0))
    if earnings >= DAILY_TARGET_EUR:
        print(f"[DAILY_STOP] {earnings:.2f}€ >= {DAILY_TARGET_EUR}€ → arrêt journalier")
        guard.pause(PausePolicy.DAILY_RESET, StopReason.DAILY_TARGET_REACHED)
        return  # jamais atteint (pause lève SystemExit)

    soft_restart_resume(ctx, driver)

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

    # FIX-B: réhydrater les gains du jour depuis DynamoDB avant de démarrer le guard.
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
            print(f"[RUNTIME_GUARD] earnings_today_eur restauré depuis DynamoDB: {persisted_earnings:.2f}€")
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

def mark_bot_running(account_id: str):
    print(f"🚀 Démarrage surveybot pour account_id={account_id}")
    update_state(account_id, lambda st: (
        st.__setitem__("status", "running"),
        st.__setitem__("last_boot_ts", _now())
    ))

def _create_driver():
    attach_addr = os.getenv("ATTACH_DEBUGGER_ADDRESS", "").strip()
    options = Options()
    if attach_addr:
        # Mode local : attach à un Chrome existant (géré par run_tabs.ps1)
        options.add_experimental_option("debuggerAddress", attach_addr)
        print(f"⚠️ ATTACH MODE → {attach_addr}")
    else:
        # Mode prod/Docker : nouveau Chrome
        options.add_argument("--headless=new")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        print("🟢 LAUNCHED NEW CHROME SESSION")
    return webdriver.Chrome(options=options)

def launch_driver_or_fail(config, account_id: str):
    try:
        driver = launch_browser(config)
        if driver is None:
            raise RuntimeError("launch_browser() a retourné None")
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
                st.__setitem__("lock_owner", ""),
                st.__setitem__("lock_until_ts", 0),
                st.__setitem__("last_stop_reason", "browser_launch_failed"),
            ))
        raise SystemExit("browser_launch_failed")

def start_debug_http_server(survey_ctx_getter):
    """
    Serveur HTTP de debug accessible sur chrome_port + 1000.
    Exemple : bot sur port 9222 → http://localhost:10222/ctx
    Uniquement en local — ignoré en prod.
    """
    if not IS_LOCAL:
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
    
def init_session_and_enter_surveys(driver, config, account_id: str, notify_fn):
    email = config.get("Email")
    password = config.get("Password")
    api_key = config.get("openai_api_key")
    payout_name = config.get("payout_name")
    payout_revolut_tag = config.get("payout_revolut_tag")

    safe_get(driver, "https://www.topsurveys.app")
    print("🚀 Brave lancé.")
    login(driver, email, password)

    try:
        payout.check_and_cashout_if_needed(
            driver,
            account_id=account_id,
            min_amount_eur=MIN_CASHOUT_EUR,
            cashout_order=("revolut", "paypal"),
            revolut_fullname=payout_name,
            revolut_tag=payout_revolut_tag,
        )

    except Exception as e:
        print(f"[PAYOUT][WARN] Encaissement automatique: {e}")

    time.sleep(15)
    snap(driver, "after_login")
    go_to_best_value_survey(driver)
    snap(driver, "after_navigate_best_value")

    return api_key, payout_name, payout_revolut_tag

def start_hot_reload_thread():
    global _HOT_RELOAD_STARTED
    if not should_run_hot_reload():
        print("[HOT_RELOAD] Ignoré en environnement mode unattended ou non-local.")
        return
    if _HOT_RELOAD_STARTED:
        return
    _HOT_RELOAD_STARTED = True

    if IS_LOCAL:        
        import Survey.survey_executor as _se
        from hot_reload.hot_reload import ModuleReloader

        reloader = ModuleReloader(
            [
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
                "Survey.dom_metrics",
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
                "Survey.screenshot_analyzer",
                "Survey.survey_executor",
                "Survey.survey_solver",
                "preselection.question_analyzer",
                "preselection.question_validation",
                "preselection.response_executor",
                "preselection.survey_handler",
                "Management.pause_policy",
                "Management.redirect_watcher",
                "Management.guards.runtime_guard",
                "Management.guards.sensitive_question_guard",
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
    else:
        print("[HOT_RELOAD] Ignoré en environnement non-local.")
        
_HOT_RELOAD_STARTED = False

def run_main_loop(driver, api_key: str, account_id: str, payout_name: str = "", payout_revolut_tag: str = ""):
    from Survey.survey_context import SurveyContext

    survey_ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
    run_survey(
        driver,
        api_key,
        account_id=account_id,
        ctx=survey_ctx,
        payout_name=payout_name,
        payout_revolut_tag=payout_revolut_tag,
    )
    # H1: en prod le bot doit quitter proprement (pas bloquer Chrome indéfiniment)
    if IS_LOCAL:
        print("Script terminé. Navigateur maintenu ouvert pour inspection.")
        while True:
            time.sleep(999)
