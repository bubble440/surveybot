print("BOOT: container démarré.", flush=True)
import os
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

from Management.runtime_guard import RuntimeGuard, set_guard, get_guard
import base64, time, Cash.payout as payout, sys, logging, threading
from cProfile import label
from preselection.config_loader import load_config
from preselection.playwright_launcher import launch_browser
from preselection.auth_handler import login, snap
from preselection.survey_navigator import go_to_best_paid_survey
from preselection.survey_handler import run_survey
from hot_reload.hot_reload import ModuleReloader
if not IS_LOCAL:
    from selenium.webdriver.chrome.options import Options
from Management.idle_monitor import start_idle_gain_watch
from Management.notifier import send_telegram
import signal
from State.account_state import update_state

# --- LOGGING DE BASE POUR CLOUDWATCH via stdout ---

# 1) stdout en line-buffering si dispo (Python 3.7+)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

# 2) niveau depuis l'env (default INFO)
_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _level, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("surveybot")

# 3) loguer les exceptions non-captées (sinon elles tuent la task en silence)
def _excepthook(exc_type, exc, tb):
    logging.getLogger("uncaught").exception("UNCAUGHT EXCEPTION", exc_info=(exc_type, exc, tb))
sys.excepthook = _excepthook

# 4) heartbeat pour prouver que le process vit même en idle
def _heartbeat():
    while True:
        try:
            get_guard().heartbeat()
        except Exception:
            pass
        time.sleep(15)

log.info("BOOT: surveybot starting")  # ✅ maintenant log est défini

def main():
    config = load_config()
    from State.account_state import load_state, update_state, save_state
    from pathlib import Path
    import json

    def _read_account_id_from_state_file() -> str | None:
        """
        Fallback local: lit account_id depuis State/account_state.json.
        On met plusieurs candidats pour être robuste (local/containers).
        """
        base_dir = Path(__file__).resolve().parent
        candidates = [
            base_dir / "State" / "account_state.json",   # cas normal (ton projet)
            base_dir / "account_state.json",             # fallback si le fichier est à la racine
            base_dir.parent / "State" / "account_state.json",
        ]

        for p in candidates:
            try:
                if p.is_file():
                    data = json.loads(p.read_text(encoding="utf-8"))
                    aid = data.get("account_id")
                    if isinstance(aid, str) and aid.strip():
                        return aid.strip()
            except Exception:
                pass
        return None

    account_id = (
        os.getenv("ACCOUNT_ID")
        or config.get("account_id")
        or config.get("Email")
        or _read_account_id_from_state_file()
    )

    if not account_id:
        raise RuntimeError("ACCOUNT_ID introuvable (ENV / config / State/account_state.json)")

    print(f"🚀 Démarrage surveybot pour account_id={account_id}")
    update_state(account_id, lambda st: (
        st.__setitem__("status", "running"),
        st.__setitem__("lock_owner", ""),
        st.__setitem__("lock_until_ts", 0),
        st.__setitem__("last_boot_ts", int(time.time()))
    ))

    # --- ECS SIGTERM HANDLER (après calcul account_id) -----------------

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

        return _handle_sigterm

    signal.signal(signal.SIGTERM, _make_sigterm_handler(account_id))
    # -------------------------------------------------------------------

    state = load_state(account_id)

    # === Watchdog "30 min sans gains" =========================
    # Si tu disposes de ces clés dans ton config.json, on enverra aussi une notif Telegram
    tg_token = config.get("telegram_bot_token")
    tg_chat  = config.get("telegram_chat_id")

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
                print("[WATCHDOG] Notification Telegram envoyée.")
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
    # ===========================================================
    def soft_restart(reason: str):
        print(f"[SOFT_RESTART] {reason}")

        try:
            # 🔒 Nettoyage onglets
            try:
                from Survey.survey_solver import _close_other_tabs_in_current_session
                _close_other_tabs_in_current_session(driver)
            except Exception:
                pass

            # 🌐 Retour TopSurveys
            driver.get("https://www.topsurveys.app")
            time.sleep(3)

            # 💰 Vérif encaissement
            payout.check_and_cashout_if_needed(
                driver,
                account_id=account_id,
                min_amount_eur=5.0,
                cashout_order=("paypal", "revolut"),
                revolut_fullname=payout_name,
                revolut_tag=payout_revolut_tag,
            )

            # 🎯 Reprise normale
            go_to_best_paid_survey(driver)
            run_survey(driver, api_key)

        except Exception as e:
            print("[SOFT_RESTART][FATAL]", e)
            raise SystemExit(e)
# === RUNTIME GUARD & BOUCLE PRINCIPALE =====================
    email = config.get("Email")
    password = config.get("Password")
    api_key = config.get("openai_api_key")
    payout_name = config.get("payout_name")
    payout_revolut_tag = config.get("payout_revolut_tag")
    guard = RuntimeGuard(
        account_id=account_id,
        idle_timeout_sec=120,
        restart_cooldown_sec=900,
        max_errors_in_row=5,
        max_runtime_sec=2 * 3600,
        daily_target_eur=5.0,
        notify_fn=_notify,
        on_soft_restart=soft_restart,
    )
    set_guard(guard)      # ✅ rend le guard accessible partout
    state["last_start_ts"] = int(time.time())
    save_state(state)

    guard.start()

    # ✅ maintenant seulement, on démarre le heartbeat
    threading.Thread(target=_heartbeat, name="heartbeat", daemon=True).start()

    try:
        driver = launch_browser()
        get_guard().attach_driver(driver)
    except Exception as e:
        import traceback
        print("[LAUNCH][ERROR]", e)
        traceback.print_exc()
        time.sleep(300)  # laisse la tâche vivante pour ECS Exec/diagnostic

    def safe_get(driver, url):
        """
        Navigation sécurisée : s'assure qu'un handle valide existe.
        """
        try:
            if not driver.window_handles:
                raise RuntimeError("Aucune fenêtre active")
            driver.switch_to.window(driver.window_handles[-1])
            driver.get(url)
        except Exception as e:
            print(f"[SAFE_GET] Navigation impossible vers {url}: {e}")
            raise
    
    safe_get(driver, "https://www.topsurveys.app")

    print(f"[DEBUG] DISPLAY={os.environ.get('DISPLAY')}")
    print("🚀 Brave lancé.")
    login(driver, email, password)
    # --- Encaissement automatique AVANT les surveys (PayPal prioritaire, Revolut en secours)
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
            threshold_sec=1800,   # 30 minutes
            check_every=60,       # lecture toutes les 60 s
            notify_fn=_notify,
        )
        print("👀 Watchdog gains: actif (30 min sans hausse → alerte).")
    except Exception as e:
        print(f"[WATCHDOG][WARN] Impossible de démarrer le watchdog: {e}")
    # ===========================================================
    snap(driver, "after_login")
    go_to_best_paid_survey(driver)


    # --- HOT RELOAD EN MODE LIVE (toujours actif) ---
    print("♻️ Hot-reload actif en mode LIVE (forcé).")
    import Survey.survey_executor as _se
    
    reloader = ModuleReloader(
        [
            "Survey.action_dispatcher",
            "Survey.input_handler",
            "Survey.survey_executor",
            "Survey.screenshot_analyzer",
            "preselection.survey_handler",
            "preselection.question_analyzer",
            "Survey.survey_solver",
            "Management.url_guard",
            "Management.survey_difficulty_guard"
        ],
        poll_interval=0.5,
    )
    
    def _on_change(reloaded):
        nonlocal _se
        if "survey_executor" in reloaded:
            _se = reloaded["survey_executor"]
        print("🔁 Modules rechargés:", ", ".join(reloaded.keys()))
    
    # Démarre la surveillance dans un thread unique
    threading.Thread(
        target=reloader.watch_loop, args=(_on_change,), daemon=True
    ).start()

    run_survey(driver, api_key, account_id=account_id)

    # Maintient le navigateur ouvert pour éviter fermeture auto
    print("🧩 Script terminé. Navigateur maintenu ouvert pour inspection.")
    while True:
        time.sleep(999)

if __name__ == "__main__":
    main()
