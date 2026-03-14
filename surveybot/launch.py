import os, random
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"
from config import is_prod_like, should_run_guard_monitor, should_run_hot_reload

from Management.guards.runtime_guard import RuntimeGuard, StopReason, set_guard, get_guard
from State.daily_target import DAILY_TARGET_EUR, ensure_daily_timer_started
import time, sys, logging, threading, traceback, signal, socket, Cash.payout as payout
from preselection.playwright_launcher import launch_browser
from preselection.auth_handler import login, snap
from preselection.survey_navigator import go_to_best_value_survey
from preselection.survey_handler import run_survey
from Management.watchdogs.idle_monitor import start_idle_gain_watch
from Management.notifier import send_telegram
from State.account_state import update_state, load_state, save_state, try_acquire_account_lock
from selenium.common.exceptions import TimeoutException
from preselection.auth_handler import is_session_expired
from Management.pause_policy import PausePolicy
from browser.browser_factory import get_driver

def acquire_account_lock_or_exit(account_id: str, ttl_sec: int = 180):
    task_id = os.getenv("ECS_TASK_ID") or socket.gethostname()
    ok = try_acquire_account_lock(account_id=account_id, owner=task_id, ttl_sec=ttl_sec)
    if not ok:
        print("[LOCK] Account {account_id} dÃ©ja utilisÃ© â†’ exit")
        sys.exit(0)

def safe_get(driver, url):
    """
    Navigation sÃ©curisÃ©e : s'assure qu'un driver valide existe.
    - Ajoute un timeout pour Ã©viter les hangs infinis en ECS.
    - Fallback: stoppe le chargement et continue.
    """
    if driver is None:
        raise RuntimeError("SAFE_GET appelÃ© avec driver=None")

    try:
        if not hasattr(driver, "window_handles") or not driver.window_handles:
            raise RuntimeError("Aucune fenÃªtre active")

        driver.switch_to.window(driver.window_handles[-1])

        # ðŸ”’ Ã©vite blocage infini
        driver.set_page_load_timeout(70)

        try:
            print(f"[SAFE_GET] start get: {url}")
            driver.get(url)
            if is_session_expired(driver):
                print("ðŸ›‘ Session expirÃ©e dÃ©tectÃ©e (24h). Pause longue.")
                get_guard().pause(
                    PausePolicy.LONG_COOLDOWN,
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
    Handler SIGTERM (ECS) : marque l'arrÃªt demandÃ© dans l'Ã©tat du compte.
    - On capture 'aid' via closure pour Ã©viter les variables globales non dÃ©finies.
    """
    def _handle_sigterm(signum, frame):
        ts = int(time.time())
        print(f"ðŸ›‘ SIGTERM reÃ§u depuis ECS | account_id={aid}")

        try:
            update_state(aid, lambda st: (
                st.__setitem__("ecs_stop_requested", True),
                st.__setitem__("ecs_stop_ts", ts),
                st.__setitem__("ecs_stop_notified", False),  # reset anti-spam Ã  chaque SIGTERM
                st.__setitem__("status", "idle"),
                st.__setitem__("lock_owner", ""),
                st.__setitem__("lock_until_ts", 0)
            ))
        except Exception as e:
            print("[SIGTERM][WARN] update_state Ã©chouÃ©:", e)
        
        finally:
            print("ðŸ§¨ SIGTERM traitÃ© â†’ exit immÃ©diat")
            raise SystemExit("ecs_sigterm")

    return _handle_sigterm

def build_notifier(config):
    tg_token = config.get("telegram_bot_token")
    tg_chat  = config.get("telegram_chat_id")
    ok = False

    def _notify(msg: str):
        # Console (toujours)
        print(f"[WATCHDOG] {msg}")
        # Telegram si configurÃ©
        if tg_token and tg_chat:
            ok = False
            try:
                ok = send_telegram(msg, tg_token, tg_chat)
            except Exception as e:
                print(f"[WATCHDOG][WARN] Telegram a Ã©chouÃ©: {e}")
        else:
            if ok:
                print("[WATCHDOG] telegram non configurÃ©.")
            else:
                print("[WATCHDOG][WARN] Telegram a rÃ©pondu 'not ok'.")

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
    PrÃ©pare un soft restart.
    IMPORTANT : se replacer sur la page APP (app.topsurveys.app) avant la logique payout,
    sinon la lecture du solde Ã©choue sur la landing marketing.
    """
    from Survey.survey_solver import _close_other_tabs_in_current_session
    _close_other_tabs_in_current_session(driver)

    # Plus fiable que la landing + clic CTA
    try:
        safe_get(driver, "https://app.topsurveys.app/surveys")
    except Exception as e:
        print(f"[SOFT_RESTART][WARN] Ã©chec accÃ¨s app /surveys: {e}")
        # Fallback best-effort : on retente la landing (au pire, le flow suivant rÃ©cupÃ¨re)
        safe_get(driver, "https://www.topsurveys.app")

def soft_restart_payout(ctx, driver):
    """
    Encaissement best-effort.
    En local / ctx minimal, on peut ne pas avoir payout_name/tag â†’ on skip proprement.
    """
    payout_name = (ctx.get("payout_name") or "").strip()
    payout_tag  = (ctx.get("payout_revolut_tag") or "").strip()

    payout.check_and_cashout_if_needed(
        driver,
        account_id=ctx["account_id"],
        min_amount_eur=DAILY_TARGET_EUR,
        cashout_order=("revolut", "paypal"),
        revolut_fullname=payout_name,
        revolut_tag=payout_tag,
    )

def soft_restart_resume(ctx, driver):
    from Survey.survey_context import SurveyContext

    survey_ctx = SurveyContext(session_id=ctx["account_id"], openai_api_key=ctx["api_key"])
    go_to_best_value_survey(driver)
    run_survey(driver, ctx["api_key"], account_id=ctx["account_id"], ctx=survey_ctx)

def soft_restart(ctx, driver, reason):
    print(f"[SOFT_RESTART] {reason}")

    soft_restart_cleanup(driver)
    time.sleep(3)

    try:
        soft_restart_payout(ctx, driver)
    except Exception as e:
        print("[SOFT_RESTART][PAYOUT][WARN]", e)

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

    set_guard(guard)
    guard.start()

    _start_ts = int(time.time())

    def _mark_start(st):
        st["last_start_ts"] = _start_ts
        ensure_daily_timer_started(st, now_ts=_start_ts)

    update_state(account_id, _mark_start)

    return guard

_HEARTBEAT_STARTED = False

def _heartbeat():
        # FrÃ©quence heartbeat (coÃ»t) vs TTL (robustesse)
        # - interval: toutes les 30s par dÃ©faut (divise les writes par 2 vs 15s)
        # - jitter: Ã©vite que 100 bots heartbeat exactement en mÃªme temps (pics WCU)
        interval = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "60") or "60")
        jitter = float(os.getenv("HEARTBEAT_JITTER_SEC", "3") or "3")

        while True:
            try:
                get_guard().heartbeat()
            except Exception:
                # Heartbeat best-effort : ne doit jamais tuer le bot
                pass

            # Jitter alÃ©atoire [0..jitter] pour lisser la charge en prod
            sleep_s = interval + (random.random() * jitter if jitter > 0 else 0.0)
            time.sleep(sleep_s)

def start_heartbeat_thread():
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

    log.info("BOOT: surveybot starting")  # âœ… maintenant log est dÃ©fini

    # 3) loguer les exceptions non-captÃ©es (sinon elles tuent la task en silence)
    def _excepthook(exc_type, exc, tb):
        logging.getLogger("uncaught").exception("UNCAUGHT EXCEPTION", exc_info=(exc_type, exc, tb))
    sys.excepthook = _excepthook

def mark_bot_running(account_id: str):
    print(f"ðŸš€ DÃ©marrage surveybot pour account_id={account_id}")
    update_state(account_id, lambda st: (
        st.__setitem__("status", "running"),
        st.__setitem__("last_boot_ts", int(time.time()))
    ))

def launch_driver_or_fail(config, account_id: str):
    try:
        # driver = launch_browser(config) Ancien launcher Playwright
        driver = get_driver()  # Nouveau launcher Selenium
        if driver is None:
            raise RuntimeError("launch_browser() a retournÃ© None")
        if should_run_guard_monitor():
            get_guard().attach_driver(driver)
        return driver
    except Exception as e:
        print("[LAUNCH][FATAL] Impossible de lancer le navigateur :", e)
        traceback.print_exc()

        if is_prod_like():
        # ðŸ”´ Ã©tat propre pour le scheduler
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
    print("ðŸš€ Brave lancÃ©.")
    login(driver, email, password)

    try:
        payout.check_and_cashout_if_needed(
            driver,
            account_id=account_id,
            min_amount_eur=DAILY_TARGET_EUR,
            cashout_order=("revolut", "paypal"),
            revolut_fullname=payout_name,
            revolut_tag=payout_revolut_tag,
        )

    except Exception as e:
        print(f"[PAYOUT][WARN] Encaissement automatique: {e}")

    try:
        start_idle_gain_watch(
            driver,
            threshold_sec=1200,   # 15 minutes
            check_every=300,       # lecture toutes les 900 s
            notify_fn= notify_fn,
        )
        print("ðŸ‘€ Watchdog gains: actif (15 min sans hausse â†’ alerte).")
    except Exception as e:
        print(f"[WATCHDOG][WARN] Impossible de dÃ©marrer le watchdog: {e}")

    time.sleep(15)
    snap(driver, "after_login")
    go_to_best_value_survey(driver)
    snap(driver, "after_navigate_best_value")

    return api_key, payout_name, payout_revolut_tag

def start_hot_reload_thread():
    global _HOT_RELOAD_STARTED
    if not should_run_hot_reload():
        print("[HOT_RELOAD] IgnorÃ© en environnement mode unattended ou non-local.")
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
                "Management.guards.url_guard",
            ],
            poll_interval=0.5,
        )

        def _on_change(reloaded):
            nonlocal _se
            if "Survey.survey_executor" in reloaded:
                _se = reloaded["Survey.survey_executor"]
            print("ðŸ” Modules rechargÃ©s:", ", ".join(reloaded.keys()))

        threading.Thread(
            target=reloader.watch_loop,
            args=(_on_change,),
            daemon=True,
        ).start()
    else:
        print("[HOT_RELOAD] IgnorÃ© en environnement non-local.")
        
_HOT_RELOAD_STARTED = False

def run_main_loop(driver, api_key: str, account_id: str):
    from Survey.survey_context import SurveyContext

    survey_ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
    run_survey(driver, api_key, account_id=account_id, ctx=survey_ctx)
    print("ðŸ§© Script terminÃ©. Navigateur maintenu ouvert pour inspection.")
    while True:
        time.sleep(999)
