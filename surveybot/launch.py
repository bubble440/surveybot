import os, random
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

from Management.guards.runtime_guard import RuntimeGuard, StopReason, set_guard, get_guard
import time, sys, logging, threading, traceback, signal, socket, Cash.payout as payout
from preselection.playwright_launcher import launch_browser
from preselection.auth_handler import login, snap
from preselection.survey_navigator import go_to_best_paid_survey
from preselection.survey_handler import run_survey
from Management.watchdogs.idle_monitor import start_idle_gain_watch
from Management.notifier import send_telegram
from State.account_state import update_state, load_state, save_state, try_acquire_account_lock
from selenium.common.exceptions import TimeoutException
from preselection.auth_handler import is_session_expired
from Management.pause_policy import PausePolicy

def acquire_account_lock_or_exit(account_id: str, ttl_sec: int = 180):
    task_id = os.getenv("ECS_TASK_ID") or socket.gethostname()
    ok = try_acquire_account_lock(account_id=account_id, owner=task_id, ttl_sec=ttl_sec)
    if not ok:
        print("[LOCK] Account {account_id} déja utilisé → exit")
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
                print("🛑 Session expirée détectée (24h). Pause longue.")
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

def install_sigterm_handler(account_id: str):
    signal.signal(signal.SIGTERM, _make_sigterm_handler(account_id))

def _make_sigterm_handler(aid: str):
    """
    Handler SIGTERM (ECS) : marque l'arrêt demandé dans l'état du compte.
    - On capture 'aid' via closure pour éviter les variables globales non définies.
    """
    def _handle_sigterm(signum, frame):
        ts = int(time.time())
        print(f"🛑 SIGTERM reçu depuis ECS | account_id={aid}")

        try:
            update_state(aid, lambda st: (
                st.__setitem__("ecs_stop_requested", True),
                st.__setitem__("ecs_stop_ts", ts),
                st.__setitem__("ecs_stop_notified", False),  # reset anti-spam à chaque SIGTERM
                st.__setitem__("status", "idle"),
                st.__setitem__("lock_owner", ""),
                st.__setitem__("lock_until_ts", 0)
            ))
        except Exception as e:
            print("[SIGTERM][WARN] update_state échoué:", e)
        
        finally:
            print("🧨 SIGTERM traité → exit immédiat")
            raise SystemExit("ecs_sigterm")

    return _handle_sigterm

def build_notifier(config):
    tg_token = config.get("telegram_bot_token")
    tg_chat  = config.get("telegram_chat_id")
    ok = False

    def _notify(msg: str):
        # Console (toujours)
        print(f"[WATCHDOG] {msg}")
        # Telegram si configuré
        if tg_token and tg_chat:
            ok = False
            try:
                ok = send_telegram(msg, tg_token, tg_chat)
            except Exception as e:
                print(f"[WATCHDOG][WARN] Telegram a échoué: {e}")
        else:
            if ok:
                print("[WATCHDOG] telegram non configuré.")
            else:
                print("[WATCHDOG][WARN] Telegram a répondu 'not ok'.")

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
    from Survey.survey_solver import _close_other_tabs_in_current_session
    _close_other_tabs_in_current_session(driver)
    driver.get("https://www.topsurveys.app")

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
        min_amount_eur=5.0,
        cashout_order=("paypal", "revolut"),
        revolut_fullname=payout_name,
        revolut_tag=payout_tag,
    )

def soft_restart_resume(ctx, driver):
    go_to_best_paid_survey(driver)
    run_survey(driver, ctx["api_key"], account_id=ctx["account_id"])

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
        daily_target_eur=5.0,
        notify_fn=notify_fn,
        on_soft_restart=on_soft_restart,
    )

    set_guard(guard)
    guard.start()

    state["last_start_ts"] = int(time.time())
    save_state(state)

    return guard

_HEARTBEAT_STARTED = False

def _heartbeat():
        # Fréquence heartbeat (coût) vs TTL (robustesse)
        # - interval: toutes les 30s par défaut (divise les writes par 2 vs 15s)
        # - jitter: évite que 100 bots heartbeat exactement en même temps (pics WCU)
        interval = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "30") or "30")
        jitter = float(os.getenv("HEARTBEAT_JITTER_SEC", "3") or "3")

        while True:
            try:
                get_guard().heartbeat()
            except Exception:
                # Heartbeat best-effort : ne doit jamais tuer le bot
                pass

            # Jitter aléatoire [0..jitter] pour lisser la charge en prod
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

    log.info("BOOT: surveybot starting")  # ✅ maintenant log est défini

    # 3) loguer les exceptions non-captées (sinon elles tuent la task en silence)
    def _excepthook(exc_type, exc, tb):
        logging.getLogger("uncaught").exception("UNCAUGHT EXCEPTION", exc_info=(exc_type, exc, tb))
    sys.excepthook = _excepthook

def mark_bot_running(account_id: str):
    print(f"🚀 Démarrage surveybot pour account_id={account_id}")
    update_state(account_id, lambda st: (
        st.__setitem__("status", "running"),
        st.__setitem__("last_boot_ts", int(time.time()))
    ))

def launch_driver_or_fail(config, account_id: str):
    try:
        driver = launch_browser(config)
        if driver is None:
            raise RuntimeError("launch_browser() a retourné None")
        if not IS_LOCAL:
            get_guard().attach_driver(driver)
        return driver
    except Exception as e:
        print("[LAUNCH][FATAL] Impossible de lancer le navigateur :", e)
        traceback.print_exc()

        if not IS_LOCAL:
        # 🔴 état propre pour le scheduler
            update_state(account_id, lambda st: (
                st.__setitem__("status", "idle"),
                st.__setitem__("lock_owner", ""),
                st.__setitem__("lock_until_ts", 0),
                st.__setitem__("last_stop_reason", "browser_launch_failed"),
            ))
        raise SystemExit("browser_launch_failed")

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
            min_amount_eur=5.0,
            cashout_order=("paypal", "revolut"),
            revolut_fullname=payout_name,
            revolut_tag=payout_revolut_tag,
        )

    except Exception as e:
        print(f"[PAYOUT][WARN] Encaissement automatique: {e}")

    try:
        start_idle_gain_watch(
            driver,
            threshold_sec=900,   # 15 minutes
            check_every=900,       # lecture toutes les 900 s
            notify_fn= notify_fn,
        )
        print("👀 Watchdog gains: actif (15 min sans hausse → alerte).")
    except Exception as e:
        print(f"[WATCHDOG][WARN] Impossible de démarrer le watchdog: {e}")

    time.sleep(30)
    snap(driver, "after_login")
    go_to_best_paid_survey(driver)
    snap(driver, "after_navigate_best_paid")

    return api_key, payout_name, payout_revolut_tag

def start_hot_reload_thread():
    global _HOT_RELOAD_STARTED
    if not IS_LOCAL:
        print("[HOT_RELOAD] Ignoré en environnement non-local.")
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
                "Survey.dom_analyzer",
                "Survey.dom_classifier",
                "Survey.dom_context_mapper",
                "Survey.dom_metrics",
                "Survey.dom_registry",
                "Survey.dropdown_block_resolver",
                "Survey.input_handler",
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
            print("🔁 Modules rechargés:", ", ".join(reloaded.keys()))

        threading.Thread(
            target=reloader.watch_loop,
            args=(_on_change,),
            daemon=True,
        ).start()
    else:
        print("[HOT_RELOAD] Ignoré en environnement non-local.")
        
_HOT_RELOAD_STARTED = False

def run_main_loop(driver, api_key: str, account_id: str):
    run_survey(driver, api_key, account_id=account_id)
    print("🧩 Script terminé. Navigateur maintenu ouvert pour inspection.")
    while True:
        time.sleep(999)
