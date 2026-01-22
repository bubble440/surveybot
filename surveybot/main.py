print("BOOT: container démarré.", flush=True)
import os
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

import sys, json, time, traceback
from preselection.config_loader import load_config
from launch import start_heartbeat_thread, acquire_account_lock_or_exit, mark_bot_running
from launch import install_sigterm_handler, start_runtime_guard, launch_driver_or_fail, init_session_and_enter_surveys
from launch import start_hot_reload_thread, run_main_loop, build_notifier, soft_restart
from Management.guards.runtime_guard import get_guard
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
            url = driver.current_url or ""
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

def _attach_select_tab(driver) -> None:
    """
    Sélection d'onglet en mode attach (LOCAL).

    Objectif: comportement prédictible (pas de pseudo "focus" Selenium).

    Priorités (dans l'ordre):
    1) ATTACH_TAB_URL_CONTAINS           => 1er onglet dont l'URL contient le substring
    2) ATTACH_TAB_TITLE_CONTAINS         => 1er onglet dont document.title contient le substring (case-insensitive)
    3) ATTACH_TAB_DOM_CONTAINS           => 1er onglet dont body.innerText contient le substring (case-insensitive, tronqué)
    4) ATTACH_TAB_SELECTOR:
        - "pick" / "prompt": affiche la liste + demande un index (LOCAL only)
        - "current": no-op (on garde l'onglet courant du driver, si http(s))
        - "last"/"newest": dernier onglet http(s)
        - "best": ancien scoring (inputs + texte)
        - "<index>": index numérique dans window_handles
    Fallback final: last_web (dernier http(s)).
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

    handles = list(getattr(driver, "window_handles", []) or [])
    if not handles:
        return

    def _switch(i: int) -> bool:
        try:
            h = handles[i]
            driver.switch_to.window(h)
            return True
        except Exception:
            return False

    def _safe_url() -> str:
        try:
            return driver.current_url or ""
        except Exception:
            return ""

    def _safe_title() -> str:
        try:
            return driver.title or ""
        except Exception:
            return ""

    def _safe_body_text_prefix(max_chars: int = 8000) -> str:
        try:
            return (
                driver.execute_script(
                    "return (document.body && (document.body.innerText||'')) ? "
                    "(document.body.innerText||'').slice(0, arguments[0]) : '';",
                    int(max_chars),
                )
                or ""
            )
        except Exception:
            return ""

    def _pick_last_web() -> bool:
        last_web = None  # (idx, url)
        for i in range(len(handles)):
            if not _switch(i):
                continue
            u = _safe_url()
            if _attach_is_user_web_url(u):
                last_web = (i, u)
        if last_web is not None:
            i, _ = last_web
            _switch(i)
            print(f"[ATTACH] Tab=last_web idx={i} url={_safe_url()}")
            return True
        return False

    # 1) URL contains (prioritaire)
    if url_contains:
        for i in range(len(handles)):
            if not _switch(i):
                continue
            u = _safe_url()
            if _attach_is_user_web_url(u) and (url_contains in u):
                print(f"[ATTACH] Tab=url_contains idx={i} url={u}")
                return
        print(f"[ATTACH] Tab=url_contains NOT FOUND ({url_contains})")

    # 2) Title contains (utile quand plusieurs onglets ont la même URL mais titres différents)
    if title_contains:
        needle = title_contains.lower()
        for i in range(len(handles)):
            if not _switch(i):
                continue
            u = _safe_url()
            if not _attach_is_user_web_url(u):
                continue
            t = _safe_title().strip().lower()
            if needle and (needle in t):
                print(f"[ATTACH] Tab=title_contains idx={i} title={_safe_title()!r} url={u}")
                return
        print(f"[ATTACH] Tab=title_contains NOT FOUND ({title_contains})")

    # 3) DOM contains (solution robuste pour 3 onglets avec EXACTEMENT la même URL)
    if dom_contains:
        needle = dom_contains.lower()
        for i in range(len(handles)):
            if not _switch(i):
                continue
            u = _safe_url()
            if not _attach_is_user_web_url(u):
                continue
            txt = _safe_body_text_prefix(8000).lower()
            if needle and (needle in txt):
                print(f"[ATTACH] Tab=dom_contains idx={i} url={u}")
                return
        print(f"[ATTACH] Tab=dom_contains NOT FOUND ({dom_contains})")

    # 4) Mode selector
    if mode in ("pick", "prompt", "menu"):
        if not IS_LOCAL:
            # attach est déjà interdit en prod, mais on garde une safety net
            print("[ATTACH] Tab=pick ignored (non-local)")
        else:
            print("[ATTACH] Tabs disponibles (idx | score=(actionables,text) | title | url):")
            for i in range(len(handles)):
                if not _switch(i):
                    continue
                u = _safe_url()
                t = _safe_title().strip().replace("\n", " ")
                if _attach_is_user_web_url(u):
                    sc = _attach_tab_score(driver)
                    print(f"[ATTACH]  {i:02d} | score={sc} | title={t[:80]!r} | url={u}")
                else:
                    print(f"[ATTACH]  {i:02d} | (non-web) | title={t[:80]!r} | url={u}")

            choice = (input("[ATTACH] Choisis l'index d'onglet à utiliser: ") or "").strip()
            if choice.isdigit():
                idx = int(choice)
                idx = max(0, min(idx, len(handles) - 1))
                _switch(idx)
                u = _safe_url()
                if _attach_is_user_web_url(u):
                    print(f"[ATTACH] Tab=pick idx={idx} url={u}")
                    return
                print(f"[ATTACH] Tab=pick idx={idx} non-web url={u} -> fallback last_web")
            else:
                print(f"[ATTACH] Tab=pick invalid={choice!r} -> fallback last_web")

            if _pick_last_web():
                return

    if mode in ("current", "active", "focused"):
        # No-op prédictible: on ne tente PAS de deviner le focus UI.
        u = _safe_url()
        if _attach_is_user_web_url(u):
            print(f"[ATTACH] Tab=current(no-op) url={u}")
            return
        # si on est tombé sur chrome://tab-search etc., on fallback
        if _pick_last_web():
            return
        return

    if mode in ("last", "newest"):
        if _pick_last_web():
            return
        # fallback brut
        _switch(len(handles) - 1)
        print(f"[ATTACH] Tab=last idx={len(handles)-1} url={_safe_url()}")
        return

    if mode == "best":
        _attach_select_best_tab(driver)
        return

    if mode.isdigit():
        idx = int(mode)
        idx = max(0, min(idx, len(handles) - 1))
        _switch(idx)
        u = _safe_url()
        if _attach_is_user_web_url(u):
            print(f"[ATTACH] Tab=index idx={idx} url={u}")
            return
        print(f"[ATTACH] Tab=index idx={idx} non-web url={u} -> fallback last_web")
        if _pick_last_web():
            return
        return

    # Fallback final
    if _pick_last_web():
        return

def run_attach_takeover(driver, *, api_key: str, account_id: str) -> None:
    """
    Mode takeover: on n'ouvre AUCUNE URL, on n'exécute PAS la préselection TopSurveys.
    On agit uniquement sur la page courante (celle que tu as ouverte à la main).
    """
    import time
    import Survey.survey_executor as survey_executor

    _attach_select_tab(driver)

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
