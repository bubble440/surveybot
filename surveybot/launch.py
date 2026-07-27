import os, random
from config import is_attach_mode, is_prod_like, should_run_guard_monitor

# SNAP_ENABLED est une variable GLOBAL_CONFIG : en build compilé (Nuitka), elle provient
# exclusivement de global_config.py, jamais de l'environnement du process (cf. config.py).
# En dev/attach (global_config.py absent du projet), fallback os.getenv.
try:
    from global_config import SNAP_ENABLED  # type: ignore
except ImportError:
    SNAP_ENABLED = os.getenv("SNAP_ENABLED", "")

# ---------- PID file (bare-metal Windows) ----------

def _pid_path(account_id: str) -> str:
    """
    Retourne le chemin du fichier PID pour ce bot (pids\bot_<id>.pid).
    Réutilise _pids_dir() de bot_supervisor.py (résolution via _bot_root_dirs())
    pour pointer vers le même dossier racine que le fichier .state du bot,
    plutôt que le dossier du module launch.py (qui peut différer : sous-dossier
    "code", ou dossier d'extraction temporaire Nuitka onefile).
    """
    from bot_supervisor import _pids_dir
    return os.path.join(_pids_dir(), f"bot_{account_id}.pid")

def write_pid_file(account_id: str) -> None:
    """
    Écrit pids\bot_<account_id>.pid - sauf si launch_all.ps1 y a déjà écrit ce
    même PID juste avant (format "PID|StartTicks" : PID + heure de démarrage du
    process, utilisés ensemble côté ps1 pour détecter un PID Windows recyclé par
    un autre process après la fin du bot). Cette écriture "double sécurité" ne
    doit pas dégrader ce couple en un simple PID nu.
    """
    if is_attach_mode():
        return
    my_pid = os.getpid()
    path = _pid_path(account_id)
    try:
        if os.path.exists(path):
            existing_pid = open(path, "r").read().strip().split("|", 1)[0]
            if existing_pid == str(my_pid):
                return
        with open(path, "w") as f:
            f.write(str(my_pid))
        print(f"[PID] Fichier écrit : {path} (pid={my_pid})")
    except Exception as e:
        print(f"[PID][WARN] Impossible d'écrire le fichier PID : {e}")

def delete_pid_file(account_id: str) -> None:
    """Supprime pids\bot_<account_id>.pid à l'arrêt propre."""
    if is_attach_mode():
        return
    try:
        path = _pid_path(account_id)
        if os.path.exists(path):
            os.remove(path)
            print(f"[PID] Fichier supprimé : {path}")
    except Exception as e:
        print(f"[PID][WARN] Impossible de supprimer le fichier PID : {e}")


from Management.guards.runtime_guard import RuntimeGuard, StopReason, set_guard, get_guard
from State.daily_target import DAILY_TARGET_EUR, ensure_daily_timer_started
from Cash.payout import MIN_CASHOUT_EUR
import time, sys, logging, threading, traceback, signal, Cash.payout as payout
from preselection.playwright_launcher import launch_browser_playwright
from preselection.auth_handler import login
from preselection.survey_navigator import go_to_best_value_survey
from preselection.survey_handler import run_survey
from Management.notifier import send_telegram
from State.account_state import update_state, load_state, try_acquire_cooldown_slot, _now
from preselection.auth_handler import is_session_expired, handle_proxy_error_page_if_needed
from Management.pause_policy import PausePolicy
import subprocess
from Cash.payout import _payout_and_check_daily_stop


def acquire_account_lock_or_exit(account_id: str, ttl_sec: int = 240):
    ok = try_acquire_cooldown_slot(account_id=account_id, ttl_sec=ttl_sec)
    if not ok:
        print(f"[COOLDOWN] Account {account_id} en cooldown ou déjà actif → exit")
        # Même convention que RuntimeGuard.pause() (runtime_guard.py) pour un arrêt
        # volontaire : sans cet enregistrement, last_exit_code restait bloqué sur le
        # sentinel EXIT_CRASH écrit par check_and_record_start() au démarrage précédent,
        # faisant compter à tort ce cooldown répété comme une crash-loop.
        from bot_supervisor import record_exit, EXIT_VOLUNTARY
        record_exit(account_id, EXIT_VOLUNTARY, "cooldown_active")
        sys.exit(0)

def safe_get(driver, url, base_delay=4):
    """
    Navigation sécurisée : s'assure qu'un driver valide existe.
    - Timeout 70s pour éviter les hangs infinis en ECS.
    - Sur PlaywrightTimeoutError : window.stop() + chargement partiel accepté.
    - Sur toute autre exception : log + re-raise.
    """
    if driver is None:
        raise RuntimeError("SAFE_GET appelé avec driver=None")

    page = driver
    try:
        try:
            page.goto(url, timeout=70_000, wait_until="domcontentloaded")
            handle_proxy_error_page_if_needed(driver)
            if is_session_expired(driver):
                # account_id inclus dans le message : même convention que le message
                # PROXY_EXPIRED (auth_handler.py::handle_proxy_error_page_if_needed) —
                # sans lui, l'alerte Telegram ne permet pas de savoir quel compte,
                # parmi les services NSSM du parc, nécessite la ré-authentification.
                _guard = get_guard()
                _account_id = getattr(_guard, "account_id", "unknown")
                msg = f"🔐 Session expirée — ré-authentification manuelle requise. | account={_account_id}"
                print(msg)
                try:
                    _guard.notify_fn(msg)
                except Exception:
                    pass
                _guard.pause(
                    PausePolicy.UNTIL_MANUAL,
                    StopReason.SESSION_EXPIRED,
                )
                raise SystemExit("session_expired")
            print(f"[SAFE_GET] done get: {url}")
            return
        except SystemExit:
            raise
        except Exception as e:
            if type(e).__name__ == "TimeoutError":
                print(f"[SAFE_GET][WARN] Timeout page load vers {url} -> window.stop()")
                try:
                    page.evaluate("window.stop()")
                except Exception:
                    pass
            else:
                raise
    except SystemExit:
        raise
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
    """
    Sur Windows, SIGTERM n'est PAS délivré par les processus Win32 externes
    (NSSM, taskkill, TerminateProcess…). Il ne peut être déclenché que via
    os.kill(pid, signal.SIGTERM) depuis un autre process Python.
    On l'enregistre pour la portabilité Linux et les cas de test Python-to-Python,
    mais ce n'est pas le chemin d'arrêt réel sous Windows — voir install_sigint_handler.
    """
    signal.signal(signal.SIGTERM, _make_stop_handler(account_id, sig_name="SIGTERM"))

def install_sigint_handler(account_id: str):
    """
    Handlers des signaux console Windows — seul canal d'arrêt externe fonctionnel
    pour un process Python sur Windows :
      - SIGINT   : Ctrl+C dans le terminal (arrêt manuel opérateur).
      - SIGBREAK : GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT) — signal exact
                   envoyé par NSSM lors d'un `nssm stop` (méthode Console).
    Les deux déclenchent la même séquence de fermeture propre.
    """
    signal.signal(signal.SIGINT, _make_stop_handler(account_id, sig_name="SIGINT"))
    if hasattr(signal, "SIGBREAK"):   # Windows uniquement
        signal.signal(signal.SIGBREAK, _make_stop_handler(account_id, sig_name="SIGBREAK"))

def _make_stop_handler(aid: str, sig_name: str = "SIGTERM"):
    """
    Fabrique un handler d'arrêt propre pour SIGTERM ou SIGINT.
    Libère le slot Postgres, supprime le fichier PID, stoppe le heartbeat, puis exit.
    """
    def _handle(signum, frame):
        print(f"🛑 {sig_name} reçu | account_id={aid}")

        try:
            update_state(aid, lambda st: (
                st.__setitem__("ecs_stop_requested", True),
                st.__setitem__("ecs_stop_ts", _now()),
                st.__setitem__("ecs_stop_notified", False),
                st.__setitem__("status", "idle"),
                st.__setitem__("cooldown_until_ts", "1970-01-01T00:00:00"),
            ))
        except Exception as e:
            print(f"[{sig_name}][WARN] update_state échoué:", e)

        finally:
            from bot_supervisor import record_exit, EXIT_VOLUNTARY
            record_exit(aid, EXIT_VOLUNTARY, f"{sig_name.lower()}_received")
            stop_heartbeat_thread()
            delete_pid_file(aid)
            print(f"{sig_name} traité → exit immédiat")
            raise SystemExit(EXIT_VOLUNTARY)

    return _handle

def build_notifier(config):
    tg_token = os.getenv("telegram_bot_token", "").strip()
    tg_chat  = os.getenv("telegram_chat_id", "").strip()

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

def soft_restart_cleanup(driver, platform=None):
    """
    Prépare un soft restart.
    IMPORTANT : se replacer sur la page APP avant la logique payout,
    sinon la lecture du solde échoue sur la landing marketing.
    """
    _home = platform.get_home_url() if platform else "https://app.topsurveys.app/surveys"
    try:
        safe_get(driver, _home)
    except Exception as e:
        print(f"[SOFT_RESTART][WARN] échec accès {_home}: {e}")


def soft_restart_resume(ctx, driver, platform=None):
    from Survey.survey_context import SurveyContext

    # Détection de redirection silencieuse vers la landing/login page.
    # safe_get() ne la détecte pas (pas d'erreur HTTP), on la sonde via ses sélecteurs DOM.
    # Deux interfaces de login possibles :
    #   topsurveys.app     → check-email-field-input
    #   app.topsurveys.app → app-page-email-field-input
    from preselection.auth_handler import LOGIN_PAGE_SELECTORS
    _page = driver
    _on_login_page = any(
        _page.query_selector(sel)
        for sel in LOGIN_PAGE_SELECTORS
    )
    if _on_login_page:
        print("[SOFT_RESTART] session expirée détectée → re-login")
        if platform:
            platform.login(driver, {"Email": ctx["email"], "Password": ctx["password"]})
        else:
            login(driver, ctx["email"], ctx["password"])
        if any(_page.query_selector(sel) for sel in LOGIN_PAGE_SELECTORS):
            raise RuntimeError("soft_restart_resume: re-login échoué, page de login toujours présente")

    survey_ctx = SurveyContext(session_id=ctx["account_id"], openai_api_key=ctx["api_key"])
    if platform:
        platform.select_survey(driver)
    else:
        go_to_best_value_survey(driver)
    run_survey(
        driver,
        ctx["api_key"],
        account_id=ctx["account_id"],
        ctx=survey_ctx,
        payout_name=ctx.get("payout_name", ""),
        payout_revolut_tag=ctx.get("payout_revolut_tag", ""),
        platform=platform,
    )

def soft_restart(ctx, driver, reason, platform=None):
    print(f"[SOFT_RESTART] {reason}")

    soft_restart_cleanup(driver, platform=platform)
    time.sleep(1)

    # DAILY STOP : si l'objectif journalier (1€) est atteint, on s'arrête
    from Management.guards.runtime_guard import get_guard, StopReason
    from Management.pause_policy import PausePolicy
    guard = get_guard()
    # FIX-C: le try/except AttributeError était du dead code en prod (RuntimeGuard a
    # toujours state.earnings_today_eur). On utilise getattr pour gérer proprement le
    # cas _NullGuard (pas de .state) sans branche redondante — Fix-B garantit
    # que guard.state.earnings_today_eur est déjà hydraté depuis Postgres au démarrage.
    earnings = float(getattr(getattr(guard, "state", None), "earnings_today_eur", 0.0))
    if earnings >= DAILY_TARGET_EUR:
        print(f"[DAILY_STOP] {earnings:.2f}€ >= {DAILY_TARGET_EUR}€ → arrêt journalier")
        guard.pause(PausePolicy.DAILY_RESET, StopReason.DAILY_TARGET_REACHED)
        return  # jamais atteint (pause lève SystemExit)

    soft_restart_resume(ctx, driver, platform=platform)

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

    # FIX-B: réhydrater les gains du jour depuis Postgres avant de démarrer le guard.
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
            print(f"[RUNTIME_GUARD] earnings_today_eur restauré depuis Postgres: {persisted_earnings:.2f}€")
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

def _purge_old_session_logs(account_id: str, keep: int = 10) -> None:
    """
    Purge additive, distincte de la rotation NSSM elle-même (nssm_setup_bot.ps1 :
    AppRotateFiles=1, AppRotateSeconds=0, AppRotateBytes=0 -> 1 fichier roté par
    démarrage de process, suffixe NSSM "<fichier>.<YYYYMMDDHHMMSS>"). NSSM rote
    mais ne purge jamais les fichiers rotés -> accumulation indéfinie sans ce
    patch. Ne touche jamais le fichier actif (sans suffixe, en cours d'écriture
    par CE process) ni les mécanismes PID/heartbeat/signaux/update_checker.
    Best-effort : une erreur ici ne doit jamais empêcher le boot du bot.
    """
    try:
        from bot_supervisor import _pids_dir
        log_dir = os.path.join(os.path.dirname(_pids_dir()), "logs")
        if not os.path.isdir(log_dir):
            return
        from Survey.log_utils import log_info
        for stream in ("stdout", "stderr"):
            prefix = f"bot_{account_id}_{stream}.log."
            rotated = [
                f for f in os.listdir(log_dir)
                if f.startswith(prefix) and f[len(prefix):].isdigit()
            ]
            rotated.sort(reverse=True)  # suffixe timestamp NSSM -> tri chronologique
            stale = rotated[max(0, keep - 1):]
            for f in stale:
                try:
                    os.remove(os.path.join(log_dir, f))
                except Exception:
                    pass
            if stale:
                log_info("[LOG_PURGE]", f"account={account_id} stream={stream} removed={len(stale)} kept={keep}")
    except Exception as e:
        print(f"[LOG_PURGE][WARN] échec purge logs session pour {account_id}: {e}")

def setup_logging(account_id: str | None = None):
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

    # 4) purge des logs de session NSSM au-delà des 10 dernières (cf. nssm_setup_bot.ps1)
    if account_id:
        _purge_old_session_logs(account_id)

def mark_bot_running(account_id: str, email):
    print(f"🚀 Démarrage surveybot pour account_id={account_id}, EMAIL={email}")
    write_pid_file(account_id)
    update_state(account_id, lambda st: (
        st.__setitem__("status", "running"),
        st.__setitem__("last_boot_ts", _now())
    ))

def launch_driver_or_fail(config, account_id: str):
    try:
        driver = launch_browser_playwright(config)
        if driver is None:
            raise RuntimeError("launch_browser_playwright() a retourné None")
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
                st.__setitem__("cooldown_until_ts", "1970-01-01T00:00:00"),
                st.__setitem__("last_stop_reason", "browser_launch_failed"),
            ))
        delete_pid_file(account_id)
        raise SystemExit("browser_launch_failed")

def start_debug_http_server(survey_ctx_getter):
    """
    Serveur HTTP de debug accessible sur chrome_port + 1000.
    Exemple : bot sur port 9222 → http://localhost:10222/ctx
    Uniquement en mode attach — ignoré en prod.
    """
    if not is_attach_mode():
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
    
def init_session_and_enter_surveys(driver, config, account_id: str, notify_fn, platform=None):
    api_key = config.get("openai_api_key")
    payout_name = config.get("payout_name")
    payout_revolut_tag = config.get("payout_revolut_tag")

    _home_url = platform.get_home_url() if platform else "https://www.topsurveys.app"
    safe_get(driver, _home_url)
    print("🚀 Brave lancé.")

    _SESSION_SEL = "[data-test-id='surveys-nav']"
    _page = driver
    _session_active = False
    try:
        _page.wait_for_selector(_SESSION_SEL, state="attached", timeout=8_000)
        _session_active = True
    except Exception:
        pass

    if _session_active:
        print("[INIT] session active détectée — login ignoré")
        if SNAP_ENABLED.strip() == "1":
            from Management.snap_uploader import new_survey, capture_and_upload
            new_survey()
            capture_and_upload(driver, "survey_account")
    else:
        if platform:
            platform.login(driver, config)
        else:
            email = os.getenv("EMAIL") or config.get("Email")
            password = os.getenv("PASSWORD") or config.get("Password")
            login(driver, email, password)
        # Après login, attendre que la page soit hydratée avant de continuer.
        try:
            _page.wait_for_selector(_SESSION_SEL, state="attached", timeout=30_000)
            print("[LOGIN] surveys-nav détecté post-login — page prête.")
        except Exception:
            print("[LOGIN][WARN] surveys-nav non détecté après 30 s — on continue quand même.")

    # try:
    #     _payout_and_check_daily_stop(driver, account_id, email=config.get("Email", ""))  # retrait + DAILY STOP
    # except Exception as e:
    #     print(f"[PAYOUT][WARN] Encaissement automatique: {e}")

    # Attente que la page soit pleinement chargée et hydratée avant de chercher un survey.
    # On réutilise _SESSION_SEL ([data-test-id='surveys-nav']) : il est présent dès
    # que l'app Vue est loggée et rendue, sans dépendre de la disponibilité de surveys.
    # Timeout généreux (30 s) pour absorber les démarrages lents en prod headless.
    # Si le sélecteur n'apparaît pas dans le délai, on continue quand même (best-effort).
    try:
        _page.wait_for_selector(_SESSION_SEL, state="attached", timeout=30_000)
        print("[INIT] surveys-nav détecté — page prête, lancement select_survey.")
    except Exception:
        print("[INIT][WARN] surveys-nav non détecté après 30 s — select_survey lancé quand même.")

    if platform:
        platform.select_survey(driver)
    else:
        go_to_best_value_survey(driver)

    return api_key, payout_name, payout_revolut_tag

def run_main_loop(driver, api_key: str, account_id: str, payout_name: str = "", payout_revolut_tag: str = "", platform=None):
    from Survey.survey_context import SurveyContext

    survey_ctx = SurveyContext(session_id=account_id, openai_api_key=api_key)
    run_survey(
        driver,
        api_key,
        account_id=account_id,
        ctx=survey_ctx,
        payout_name=payout_name,
        payout_revolut_tag=payout_revolut_tag,
        platform=platform,
    )

    # Vérification mise à jour du code au retour au listing (entre deux cycles).
    # No-op si UPDATE_CHECK_ENABLED != "1" ou si git est inaccessible.
    # Si une mise à jour est disponible : git pull + os.execv() (ne retourne pas).
    from update_checker import check_and_apply
    check_and_apply(account_id)

    # H1: en prod le bot doit quitter proprement (pas bloquer Chrome indéfiniment)
    if is_attach_mode():
        print("Script terminé. Navigateur maintenu ouvert pour inspection.")
        while True:
            time.sleep(999)