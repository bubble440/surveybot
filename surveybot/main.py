print("BOOT: container démarré.", flush=True)
import os
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

import time, sys
from preselection.config_loader import load_config
from State.account_state import  load_state
from launch import _read_account_id_from_state_file, start_heartbeat_thread, acquire_account_lock_or_exit, mark_bot_running
from launch import install_sigterm_handler, start_runtime_guard, acquire_proxy_lock_or_exit, launch_driver_or_fail, init_session_and_enter_surveys
from launch import start_hot_reload_thread, run_main_loop, build_notifier, soft_restart
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

# def main():
#     config = load_config()
#     account_id = (
#         os.getenv("ACCOUNT_ID")
#         or config.get("account_id")
#         or config.get("Email")
#         or _read_account_id_from_state_file()
#     )

#     acquire_account_lock_or_exit(account_id)

#     if not account_id:
#         raise RuntimeError("ACCOUNT_ID introuvable (ENV / config / State/account_state.json)")

#     mark_bot_running(account_id)

#     # --- ECS SIGTERM HANDLER (après calcul account_id) -----------------

#     install_sigterm_handler(account_id)

#     state = load_state(account_id)
    
#     if IS_LOCAL:
#         print("🧪 MODE LOCAL — account_state désactivé")
#     else:
#         # ici seulement on accepte que le state soit utilisé
#         start_runtime_guard(account_id)
#         acquire_proxy_lock_or_exit()

#     launch_driver_or_fail()

#     time.sleep(10)

#     init_session_and_enter_surveys()


#     # --- HOT RELOAD EN MODE LIVE (toujours actif) ---
#     print("♻️ Hot-reload actif en mode LIVE (forcé).")
    
#     start_hot_reload_thread()

#     run_main_loop(driver, api_key, account_id)

def main():
    config = load_config()

    account_id = (
        os.getenv("ACCOUNT_ID")
        or config.get("account_id")
        or config.get("Email")
        or _read_account_id_from_state_file()
    )

    if not account_id:
        raise RuntimeError("ACCOUNT_ID introuvable")

    acquire_account_lock_or_exit(account_id)
    mark_bot_running(account_id)
    install_sigterm_handler(account_id)

    notify_fn = build_notifier(config)

    if not IS_LOCAL:
        acquire_proxy_lock_or_exit(account_id)

    driver = launch_driver_or_fail(config, account_id)

    ctx = {
        "account_id": account_id,
    }

    def _soft_restart(reason):
        soft_restart(ctx, driver, reason)

    if not IS_LOCAL:
        start_runtime_guard(
            account_id=account_id,
            notify_fn=notify_fn,
            on_soft_restart=_soft_restart,
        )

    api_key, payout_name, payout_revolut_tag = init_session_and_enter_surveys(
        driver,
        config,
        account_id,
        notify_fn,
    )

    ctx.update({
        "api_key": api_key,
        "payout_name": payout_name,
        "payout_revolut_tag": payout_revolut_tag,
    })

    start_heartbeat_thread()
    start_hot_reload_thread()

    run_main_loop(driver, api_key, account_id)

if __name__ == "__main__":
    main()
