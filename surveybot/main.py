print("BOOT: container démarré.", flush=True)
import os
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

import sys
from preselection.config_loader import load_config
from launch import start_heartbeat_thread, acquire_account_lock_or_exit, mark_bot_running
from launch import install_sigterm_handler, start_runtime_guard, acquire_proxy_lock_or_exit, launch_driver_or_fail, init_session_and_enter_surveys
from launch import start_hot_reload_thread, run_main_loop, build_notifier, soft_restart
from Management.guards.runtime_guard import get_guard
import time
import traceback

if IS_LOCAL:
    ACCOUNT_ID = "local_debug"
else:
    ACCOUNT_ID = os.getenv("ACCOUNT_ID")
    if not ACCOUNT_ID:
        raise RuntimeError("ACCOUNT_ID manquant en environnement non-local")

# 1) stdout en line-buffering si dispo (Python 3.7+)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

def main():
    config = load_config()

    account_id = (
        os.getenv("ACCOUNT_ID")
        or config.get("account_id")
        or config.get("Email")
    )

    if not account_id:
        raise RuntimeError("ACCOUNT_ID introuvable")

    acquire_account_lock_or_exit(account_id)
    mark_bot_running(account_id)
    install_sigterm_handler(account_id)

    notify_fn = build_notifier(config)

    if not IS_LOCAL:
        acquire_proxy_lock_or_exit(account_id)
    runtime_ctx = {
        "driver": None,
        "session": {},
    }

    guard = None
    heartbeat_started = False
    hot_reload_started = False

    while True:
        driver = None
        try:
            driver = launch_driver_or_fail(config, account_id)
            api_key, payout_name, payout_revolut_tag = init_session_and_enter_surveys(driver, config, account_id, notify_fn)

            runtime_ctx["driver"] = driver
            runtime_ctx["session"] = {
                "account_id": account_id,
                "api_key": api_key,
                "payout_name": payout_name,
                "payout_revolut_tag": payout_revolut_tag,
            }

            def _soft_restart(reason):
                return soft_restart(
                runtime_ctx["session"],
                runtime_ctx["driver"],
                reason,)

            if not IS_LOCAL:
                if guard is None:
                    guard = start_runtime_guard(
                        account_id=account_id,
                        notify_fn=notify_fn,
                        on_soft_restart=_soft_restart,
                    )
                get_guard().attach_driver(driver)

            # ✅ IMPORTANT : démarrer les threads AVANT d'entrer dans une boucle bloquante
            if IS_LOCAL and not hot_reload_started:
                start_hot_reload_thread()
                hot_reload_started = True

            if (not IS_LOCAL) and (not heartbeat_started):
                start_heartbeat_thread()
                heartbeat_started = True

            run_main_loop(driver, api_key, account_id)

        except SystemExit:
            raise

        except Exception as e:
            print(f"[MAIN][ERROR] {type(e).__name__}: {e}")
            traceback.print_exc()
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass
            time.sleep(2)
            continue
        
if __name__ == "__main__":
    main()
