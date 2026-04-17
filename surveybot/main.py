print("BOOT: container démarré.", flush=True)
import os
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

import sys, json, time, traceback
from urllib.parse import urlparse
from preselection.config_loader import load_config
from launch import start_heartbeat_thread, acquire_account_lock_or_exit, mark_bot_running
from launch import install_sigterm_handler, start_runtime_guard, launch_driver_or_fail, init_session_and_enter_surveys, install_sigusr1_handler, restore_session_cookies
from launch import start_hot_reload_thread, run_main_loop, build_notifier, soft_restart, start_debug_http_server
from Management.guards.runtime_guard import get_guard
from config import is_attach_mode, RUN_ENV, RUN_MODE, BROWSER_MODE, is_prod_like, should_run_guard_monitor, should_run_heartbeat, should_run_hot_reload, log_config_summary

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
        print(f"[ATTACH] Tab sélectionné score={score} url={_attach_display_url(url)}")

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

def _attach_display_url(url: str) -> str:
    """Affiche uniquement le schéma + host (jusqu'au TLD), sans path/query."""
    u = _attach__strip_url(url)
    if not u:
        return ""

    lu = u.lower()
    if not (lu.startswith("http://") or lu.startswith("https://")):
        return u

    try:
        parsed = urlparse(u)
    except Exception:
        return u

    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return ""

    port = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{host}{port}"

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
            print(f"[ATTACH] Tab=last_web idx={i} url={_attach_display_url(_safe_url())}")
            return True
        return False

    # 1) URL contains (prioritaire)
    if url_contains:
        for i in range(len(handles)):
            if not _switch(i):
                continue
            u = _safe_url()
            if _attach_is_user_web_url(u) and (url_contains in u):
                print(f"[ATTACH] Tab=url_contains idx={i} url={_attach_display_url(u)}")
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
                print(f"[ATTACH] Tab=title_contains idx={i} title={_safe_title()!r} url={_attach_display_url(u)}")
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
                print(f"[ATTACH] Tab=dom_contains idx={i} url={_attach_display_url(u)}")
                return
        print(f"[ATTACH] Tab=dom_contains NOT FOUND ({dom_contains})")

    # 4) Mode selector
    if mode in ("pick", "prompt", "menu"):
        if not IS_LOCAL:
            # attach est déja interdit en prod, mais on garde une safety net
            print("[ATTACH] Tab=pick ignored (non-local)")
        else:
            web_handles = []  # mapping: display_index -> real handles[] index
            for i in range(len(handles)):
                if not _switch(i):
                    continue
                u = _safe_url()
                if _attach_is_user_web_url(u):
                    web_handles.append((i, u, _safe_title().strip().replace("\n", " "), _attach_tab_score(driver)))
            print("[ATTACH] Tabs disponibles (idx | score=(actionables,text) | title | url):")
            for d, (i, u, t, sc) in enumerate(web_handles):
                print(f"[ATTACH]  {d:02d} | score={sc} | title={t[:80]!r} | url={_attach_display_url(u)}")

            choice = (input("[ATTACH] Choisis l'index d'onglet à utiliser: ") or "").strip()
            if choice.isdigit():
                didx = int(choice)
                if 0 <= didx < len(web_handles):
                    idx = web_handles[didx][0]
                    _switch(idx)
                    u = _safe_url()
                    if _attach_is_user_web_url(u):
                        print(f"[ATTACH] Tab=pick didx={didx} idx={idx} url={_attach_display_url(u)}")
                        return
                    print(f"[ATTACH] Tab=pick didx={didx} idx={idx} non-web url={_attach_display_url(u)} -> fallback last_web")
                else:
                    print(f"[ATTACH] Tab=pick out-of-range={didx!r} -> fallback last_web")
            else:
                print(f"[ATTACH] Tab=pick invalid={choice!r} -> fallback last_web")

            if _pick_last_web():
                return

    if mode in ("current", "active", "focused"):
        # No-op prédictible: on ne tente PAS de deviner le focus UI.
        u = _safe_url()
        if _attach_is_user_web_url(u):
            print(f"[ATTACH] Tab=current(no-op) url={_attach_display_url(u)}")
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
        print(f"[ATTACH] Tab=last idx={len(handles)-1} url={_attach_display_url(_safe_url())}")
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
            print(f"[ATTACH] Tab=index idx={idx} url={_attach_display_url(u)}")
            return
        print(f"[ATTACH] Tab=index idx={idx} non-web url={_attach_display_url(u)} -> fallback last_web")
        if _pick_last_web():
            return
        return

    # Fallback final
    if _pick_last_web():
        return

def run_attach_takeover(driver, *, api_key: str, account_id: str) -> None:
    """
    Mode takeover: on n'ouvre AUCUNE URL, on n'exécute PAS la présélection TopSurveys.
    On agit uniquement sur la page courante (celle que tu as ouverte à la main).
    """
    import time
    import Survey.survey_executor as survey_executor
    import Management.guards.survey_difficulty_guard as difficulty_guard
    from Survey.survey_context import SurveyContext
    import Survey.survey_solver as survey_solver

    # Instancier et exposer le contexte dès le début du takeover
    _ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
    survey_solver._current_survey_ctx = _ctx

    _attach_select_tab(driver)

    max_steps = int(os.getenv("ATTACH_MAX_STEPS", "100"))
    print(f"[ATTACH] takeover loop start (max_steps={max_steps}) url={_attach_display_url(getattr(driver,'current_url',''))}")
    for i in range(1, max_steps + 1):
        try:
            # === STRICT GUARD CHECK ===
            # Détecte les pages non supportées (image_evaluation, drag_drop, etc.)
            is_strict, reason = difficulty_guard.detect_strict_survey(driver)
            if is_strict:
                if reason == "drag_drop":
                    print("[ATTACH][STRICT] drag_drop strict reason ignored -> continue")
                    continue

                # Captcha : tentative de résolution automatique via 2Captcha
                if reason == "captcha":
                    from config import get_captcha_behavior
                    captcha_behavior = get_captcha_behavior()

                    if captcha_behavior == "auto_2captcha":
                        print("[ATTACH][CAPTCHA] reCAPTCHA détecté → tentative 2Captcha...")
                        try:
                            from captcha.recaptcha_handler import solve_recaptcha_v2_auto
                            resolved = solve_recaptcha_v2_auto(driver)
                        except Exception as e:
                            print(f"[ATTACH][CAPTCHA] Erreur recaptcha_handler: {e}")
                            resolved = False

                        if resolved:
                            # CRITIQUE : le callback CMIX a bien mis pass=true, MAIS
                            # [data-sitekey] reste dans le DOM même après résolution.
                            # Si on fait continue() directement, detect_strict_survey()
                            # re-détecte le captcha → boucle infinie + coût 2Captcha.
                            # Solution : cliquer le CTA et ATTENDRE que l'URL change
                            # (confirmation que la page suivante est chargée) avant continue.
                            print("[ATTACH][CAPTCHA] ✅ pass=true set → clic CTA pour avancer la page...")
                            try:
                                from Survey.cta_handler import try_click_navigation_cta_any_context
                                cta_clicked = try_click_navigation_cta_any_context(driver)
                                if cta_clicked:
                                    print("[ATTACH][CAPTCHA] ✅ CTA cliqué → attente changement URL...")
                                    # Attente active : l'URL doit changer (max 10s).
                                    # Sleep fixe insuffisant — Decipher peut prendre 3-8s
                                    # pour soumettre le formulaire et charger la page suivante.
                                    # On attend le changement d'URL plutôt qu'un délai arbitraire.
                                    _url_before = driver.current_url
                                    from selenium.webdriver.support.ui import WebDriverWait
                                    try:
                                        WebDriverWait(driver, 10).until(
                                            lambda d: d.current_url != _url_before
                                        )
                                        print(f"[ATTACH][CAPTCHA] ✅ URL changée → {driver.current_url[:60]}")
                                    except Exception:
                                        # Pas de changement d'URL dans les 10s — on continue quand même
                                        # (certaines plateformes rechargent la même URL sans captcha)
                                        print("[ATTACH][CAPTCHA] ⚠️ URL inchangée après 10s — on continue")
                                    time.sleep(0.5)  # micro-pause DOM post-navigation
                                else:
                                    print("[ATTACH][CAPTCHA] ⚠️ Aucun CTA trouvé (non bloquant)")
                            except Exception as e:
                                print(f"[ATTACH][CAPTCHA] CTA click erreur (non bloquant): {e}")
                            continue  # reprend la boucle — page suivante sans [data-sitekey]
                        else:
                            print("[ATTACH][CAPTCHA] ❌ Échec → abandon du survey")
                            break

                    elif captcha_behavior == "pause":
                        # LOCAL interactif : pause manuelle (anti-boucle sur même URL)
                        captcha_url = driver.current_url or ""
                        last_captcha_url = getattr(driver, "_last_captcha_pause_url", None)
                        if last_captcha_url == captcha_url:
                            print("[ATTACH][CAPTCHA] Déjà traité sur cette URL → continue")
                        else:
                            setattr(driver, "_last_captcha_pause_url", captcha_url)
                            try:
                                input("[ATTACH][PAUSE] Résous le CAPTCHA dans le navigateur, puis appuie sur Entrée...\n")
                            except (KeyboardInterrupt, EOFError):
                                print("[ATTACH] Abandon demandé")
                                break
                        continue

                    else:  # "restart" = pas de clé 2Captcha
                        print("[ATTACH][STRICT] captcha détecté → abandon (pas de clé 2Captcha)")
                        break

                else:
                    # Autres raisons (image_evaluation, hold_button...) → abandon immédiat
                    print(f"[ATTACH][STRICT] Détecté: {reason} -> abandon du survey")
                    print(f"[ATTACH][STRICT] Ce type de survey n'est pas supporté en V1")
                    break

        ### Résultat attendu dans les logs avec clé 2Captcha configurée
            
            ok = survey_executor.execute_survey_page(driver, account_id, api_key, ctx=_ctx)
            _ctx.maybe_update_summary()                                           # ← ajouter cette ligne
            print(f"[ATTACH] step={i}/{max_steps} ok={ok} url={_attach_display_url(driver.current_url)}")
        except Exception as e:
            print(f"[ATTACH][ERROR] step={i} {type(e).__name__}: {e}")
            break

        time.sleep(0.6)  # mini respiration DOM

    print("[ATTACH] takeover loop end (process exit, sans fermer Chrome).")



def _get_attach_route() -> str:
    if os.getenv("ATTACH_ROUTE_PROMPT") != "1":
        return "resolution"

    print("[ATTACH] Choisis la route de takeover :")
    print("  1) preselection")
    print("  2) resolution")
    choice = (input("Choix [1/2, défaut=2]: ") or "").strip().lower()

    if choice in {"1", "preselection"}:
        return "preselection"

    if choice not in {"", "2", "resolution"}:
        print(f"[ATTACH] choix invalide={choice!r} -> fallback resolution")
    return "resolution"


def run_attach_preselection_takeover(driver, *, api_key: str, account_id: str) -> None:
    """Attach takeover dédié au popup de présélection TopSurveys déjà affiché."""
    import Survey.survey_executor as survey_executor
    import Survey.survey_solver as survey_solver
    from Survey.survey_context import SurveyContext
    from preselection.survey_handler import run_attach_preselection_takeover as run_preselection_takeover

    _ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
    survey_solver._current_survey_ctx = _ctx

    _attach_select_tab(driver)

    max_rounds = int(os.getenv("ATTACH_PRESELECTION_MAX_ROUNDS", "15"))
    transition_timeout_s = int(os.getenv("ATTACH_PRESELECTION_TRANSITION_TIMEOUT_S", "45"))
    ok, reason = run_preselection_takeover(
        driver,
        api_key,
        max_rounds=max_rounds,
        transition_timeout_s=transition_timeout_s,
        ctx=_ctx,
    )

    if not ok:
        print(f"[ATTACH][PRESEL] abandon contrôlé: reason={reason}")
        return

    print("[ATTACH][PRESEL] présélection terminée -> bascule en résolution survey")

    max_steps = int(os.getenv("ATTACH_MAX_STEPS", "100"))
    for i in range(1, max_steps + 1):
        try:
            done = survey_executor.execute_survey_page(driver, account_id, api_key, ctx=_ctx)
            _ctx.maybe_update_summary()
            print(f"[ATTACH][PRESEL->RES] step={i}/{max_steps} ok={done} url={_attach_display_url(driver.current_url)}")
        except Exception as e:
            print(f"[ATTACH][PRESEL->RES][ERROR] step={i} {type(e).__name__}: {e}")
            break
        time.sleep(0.6)

    print("[ATTACH][PRESEL] route terminée.")

def main():
    config = load_config()

    print(
        f"[BOOT] RUN_ENV={RUN_ENV} RUN_MODE={RUN_MODE} BROWSER_MODE={BROWSER_MODE} attach={is_attach_mode()}",
        flush=True,
    )

    #  Fail-fast : même si quelqu'un force des env vars en prod, attach ne doit jamais tourner
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
        # ⚠ ATTACH = LOCAL DEBUG TAKEOVER
        # - pas de lock Postgres
        # - pas de navigation TopSurveys
        # - pas de quit() (sinon tu fermes ton Chrome)
        driver = launch_driver_or_fail(config, account_id)
        from Survey.survey_solver import get_current_survey_ctx
        start_debug_http_server(get_current_survey_ctx)

        api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENAI_API_KEY_LOCAL")
            or config.get("openai_api_key")
            or config.get("api_key")
            or config.get("OPENAI_API_KEY")
        )
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY introuvable (nécessaire en attach)")

        if should_run_hot_reload():
            start_hot_reload_thread()

        attach_route = _get_attach_route()
        print(f"[ATTACH] route={attach_route}")
        if attach_route == "preselection":
            run_attach_preselection_takeover(driver, api_key=api_key, account_id=account_id)
        else:
            run_attach_takeover(driver, api_key=api_key, account_id=account_id)
        return

    # FIX-A: install_sigterm_handler AVANT acquire_account_lock_or_exit.
    # Auparavant, un SIGTERM arrivant entre acquire et install_sigterm_handler
    # terminait le processus sans remettre cooldown_until_ts à zéro en Postgres,
    # forçant le scheduler à attendre l'expiration du TTL avant de relancer.
    install_sigterm_handler(account_id)
    install_sigusr1_handler()

    acquire_account_lock_or_exit(account_id)
    mark_bot_running(account_id)
    from State.account_state import update_state as _update_state, _now as _now_ts
    from State.daily_target import ensure_daily_timer_started as _ensure_timer
    _boot_ts = _now_ts()
    _update_state(account_id, lambda st: (
        st.__setitem__("last_start_ts", _boot_ts),
        _ensure_timer(st, now_ts=_boot_ts),
    ))

    from Survey.survey_solver import get_current_survey_ctx
    start_debug_http_server(get_current_survey_ctx)

    notify_fn = build_notifier(config)

    # Proxy-lock retiré : en prod on a 1 bot par proxy, donc lock proxy redondant
    runtime_ctx = {
        "driver": None,
        "session": {},
    }

    guard = None
    hot_reload_started = False

    if should_run_heartbeat():
        start_heartbeat_thread()
    heartbeat_started = True

    max_cycles = int(os.getenv("MAX_MAIN_CYCLES", "3") or "3")
    cycle = 0

    while cycle < max_cycles:
        cycle += 1
        driver = None

        try:
            driver = launch_driver_or_fail(config, account_id)

            runtime_ctx["driver"] = driver
            # PATCH: Stocker account_id sur driver pour acces dans survey_executor
            driver._survey_account_id = account_id

            restore_session_cookies(driver, account_id)

            def _soft_restart(reason):
                return soft_restart(
                    runtime_ctx["session"],
                    runtime_ctx["driver"],
                    reason,
                )

            # FIX: guard initialisé AVANT init_session_and_enter_surveys pour que
            # get_guard().pause() (ex: no_survey_available) écrive bien cooldown_until_ts en DB.
            if should_run_guard_monitor():
                if guard is None:
                    guard = start_runtime_guard(
                        account_id=account_id,
                        notify_fn=notify_fn,
                        on_soft_restart=_soft_restart,
                    )
                get_guard().attach_driver(driver)

            api_key, payout_name, payout_revolut_tag = init_session_and_enter_surveys(driver, config, account_id, notify_fn)

            runtime_ctx["session"] = {
                "account_id": account_id,
                "api_key": api_key,
                "payout_name": payout_name,
                "payout_revolut_tag": payout_revolut_tag,
                "email": config.get("Email", ""),
                "password": config.get("Password", ""),
            }

            if should_run_hot_reload() and not hot_reload_started:
                start_hot_reload_thread()
                hot_reload_started = True

            run_main_loop(driver, api_key, account_id, payout_name=payout_name, payout_revolut_tag=payout_revolut_tag)

        except SystemExit:
            raise

        except Exception as e:
            print(f"[MAIN][ERROR] cycle={cycle}/{max_cycles} {type(e).__name__}: {e}")
            traceback.print_exc()
            # FIX-B2 (partie catch): libération lock en cas de crash Exception
            if not IS_LOCAL:
                try:
                    from State.account_state import update_state
                    update_state(account_id, lambda st: (
                        st.__setitem__("status", "idle"),
                        st.__setitem__("cooldown_until_ts", "1970-01-01T00:00:00"),
                        st.__setitem__("last_stop_reason", f"crash_{type(e).__name__}"),
                    ))
                except Exception as _le:
                    print(f"[MAIN][WARN] Impossible de libérer le lock après crash: {_le}")
            time.sleep(2)
            continue

        finally:
            # FIX-B2: driver.quit() garanti sur toute sortie (Exception, KeyboardInterrupt, etc.)
            # SystemExit propagera naturellement après ce bloc.
            # FIX-B3: ne pas référencer 'e' ici (Python 3 le supprime en fin de bloc except)
            # FIX-B4: pas de 'continue' ici — supprime les SystemExit et empêche l'arrêt propre
            try:
                if driver and (not is_attach_mode()):
                    # Sauvegarder le profil Chrome avant de quitter (si profil persistant)
                    _acct = os.getenv("ACCOUNT_ID", "").strip()
                    _db   = os.getenv("DATABASE_URL", "").strip()
                    if _acct and _db and hasattr(driver, "_chrome_user_data_dir") and driver._chrome_user_data_dir:
                        from preselection.chrome_profile_store import save_profile
                        save_profile(_acct, driver._chrome_user_data_dir)
                    # Terminer le processus Chrome lancé par subprocess.Popen
                    if hasattr(driver, '_chrome_proc') and driver._chrome_proc:
                        try:
                            driver._chrome_proc.terminate()
                        except Exception:
                            pass
                    # Terminer le relay pproxy si présent
                    if hasattr(driver, '_proxy_relay_proc') and driver._proxy_relay_proc:
                        try:
                            driver._proxy_relay_proc.terminate()
                        except Exception:
                            pass
                    driver.quit()
            except Exception:
                pass

    # Si on sort de la boucle, on stoppe proprement (ECS relancera via scheduler)
    if not IS_LOCAL:
        try:
            from State.account_state import update_state
            update_state(account_id, lambda st: (
                st.__setitem__("status", "idle"),
                st.__setitem__("cooldown_until_ts", "1970-01-01T00:00:00"),
                st.__setitem__("last_stop_reason", "max_main_cycles_reached"),
            ))
        except Exception as _le:
            print(f"[MAIN][WARN] Impossible de libérer le lock en fin de cycles: {_le}")
    raise SystemExit("max_main_cycles_reached")
        
if __name__ == "__main__":
    main()
