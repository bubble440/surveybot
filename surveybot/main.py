print("BOOT: container démarré.", flush=True)
import os
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

import sys
from preselection.config_loader import load_config
from launch import start_heartbeat_thread, acquire_account_lock_or_exit, mark_bot_running
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

    ctx = {
        "account_id": account_id,
    }

    driver= None  # pour scope

    def _soft_restart(reason):
        soft_restart(ctx, driver, reason)

    if not IS_LOCAL:
        start_runtime_guard(
            account_id=account_id,
            notify_fn=notify_fn,
            on_soft_restart=_soft_restart,
        )

    driver = launch_driver_or_fail(config, account_id)

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
