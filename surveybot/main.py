print("BOOT: container démarré.", flush=True)
import os
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

import sys
from preselection.config_loader import load_config
from launch import start_heartbeat_thread, acquire_account_lock_or_exit, mark_bot_running
from launch import install_sigterm_handler, start_runtime_guard, launch_driver_or_fail, init_session_and_enter_surveys
from launch import start_hot_reload_thread, run_main_loop, build_notifier, soft_restart
from Management.guards.runtime_guard import get_guard
import time
import traceback
from config import is_attach_mode, RUN_ENV, RUN_MODE, BROWSER_MODE

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

def _attach_tab_score(driver) -> tuple[int, int]:
    """Score simple: nb d'éléments actionnables + taille texte."""
    try:
        actionable = driver.execute_script("""
            try {
              const sel = "input,select,textarea,button,[role='button'],[role='radio'],[role='checkbox'],label[for]";
              return document.querySelectorAll(sel).length || 0;
            } catch(e) { return 0; }
        """)
    except Exception:
        actionable = 0

    try:
        text_len = driver.execute_script("""
            try { return (document.body && (document.body.innerText||'').length) || 0; }
            catch(e) { return 0; }
        """)
    except Exception:
        text_len = 0

    return int(actionable), int(text_len)

def _attach_select_best_tab(driver) -> None:
    """
    Selenium ne sait pas 'prendre l'onglet actif' de Chrome de façon fiable.
    Donc: on parcourt tous les onglets et on choisit celui qui ressemble le plus
    à une page testable (beaucoup d'inputs/texte).
    """
    best = None  # (score_tuple, handle, url)
    for h in list(getattr(driver, "window_handles", []) or []):
        try:
            driver.switch_to.window(h)
            url = driver.current_url or ""
            score = _attach_tab_score(driver)
            if (best is None) or (score > best[0]):
                best = (score, h, url)
        except Exception:
            continue

    if best:
        score, h, url = best
        try:
            driver.switch_to.window(h)
        except Exception:
            pass
        print(f"[ATTACH] Tab sélectionné score={score} url={url}")

def run_attach_takeover(driver, *, api_key: str, account_id: str) -> None:
    """
    Mode takeover: on n'ouvre AUCUNE URL, on n'exécute PAS la préselection TopSurveys.
    On agit uniquement sur la page courante (celle que tu as ouverte à la main).
    """
    import time
    import Survey.survey_executor as survey_executor

    _attach_select_best_tab(driver)

    max_steps = int(os.getenv("ATTACH_MAX_STEPS", "10"))
    print(f"[ATTACH] takeover loop start (max_steps={max_steps}) url={getattr(driver,'current_url','')}")
    for i in range(1, max_steps + 1):
        try:
            ok = survey_executor.execute_survey_page(driver, api_key)
            print(f"[ATTACH] step={i}/{max_steps} ok={ok} url={driver.current_url}")
        except Exception as e:
            print(f"[ATTACH][ERROR] step={i} {type(e).__name__}: {e}")
            break

        time.sleep(0.6)  # mini respiration DOM

    print("[ATTACH] takeover loop end (process exit, sans fermer Chrome).")

def main():
    config = load_config()

    print(
        f"[BOOT] RUN_ENV={RUN_ENV} RUN_MODE={RUN_MODE} BROWSER_MODE={BROWSER_MODE} attach={is_attach_mode()}",
        flush=True,
    )

    # 🔒 Fail-fast : même si quelqu'un force des env vars en prod, attach ne doit jamais tourner
    if is_attach_mode() and (not IS_LOCAL):
        raise SystemExit("attach_forbidden_in_prod")

    account_id = (
        os.getenv("ACCOUNT_ID")
        or config.get("account_id")
        or config.get("Email")
    )

    if not account_id:
        raise RuntimeError("ACCOUNT_ID introuvable")

    if is_attach_mode():
        # ⚠️ ATTACH = LOCAL DEBUG TAKEOVER
        # - pas de lock DynamoDB
        # - pas de navigation TopSurveys
        # - pas de quit() (sinon tu fermes ton Chrome)
        driver = launch_driver_or_fail(config, account_id)

        api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENAI_API_KEY_LOCAL")
            or config.get("openai_api_key")
            or config.get("api_key")
            or config.get("OPENAI_API_KEY")
        )
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY introuvable (nécessaire en attach)")

        run_attach_takeover(driver, api_key=api_key, account_id=account_id)
        return

    acquire_account_lock_or_exit(account_id)
    mark_bot_running(account_id)
    install_sigterm_handler(account_id)

    notify_fn = build_notifier(config)

    # Proxy-lock retiré : en prod on a 1 bot par proxy, donc lock proxy redondant
    runtime_ctx = {
        "driver": None,
        "session": {},
    }

    guard = None
    heartbeat_started = False
    hot_reload_started = False

    max_cycles = int(os.getenv("MAX_MAIN_CYCLES", "3") or "3")
    cycle = 0

    while cycle < max_cycles:
        cycle += 1
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
                    reason,
                )

            if not IS_LOCAL:
                if guard is None:
                    guard = start_runtime_guard(
                        account_id=account_id,
                        notify_fn=notify_fn,
                        on_soft_restart=_soft_restart,
                    )
                get_guard().attach_driver(driver)

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
            print(f"[MAIN][ERROR] cycle={cycle}/{max_cycles} {type(e).__name__}: {e}")
            traceback.print_exc()
            try:
                if driver and (not is_attach_mode()):
                    driver.quit()
            except Exception:
                pass
            time.sleep(2)
            continue

    # Si on sort de la boucle, on stoppe proprement (ECS relancera via scheduler)
    raise SystemExit("max_main_cycles_reached")
        
if __name__ == "__main__":
    main()
