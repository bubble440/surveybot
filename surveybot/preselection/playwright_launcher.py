from __future__ import annotations
import os, time
import subprocess
from Survey.functions import _env_truthy

# IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

# preselection/playwright_launcher.py
"""
Launcher alternatif : subprocess.Popen lance Chrome directement (sans Playwright),
puis Selenium s'attache via debuggerAddress.
Objectif : éviter la contamination CDP/cdc_* de Selenium à l'attach et supprimer
la bannière "Chrome is being controlled by automated test software".
"""

import json
import random
import logging
import tempfile
from urllib.parse import urlparse
from Survey.log_utils import log_info, log_debug


def _detect_chrome_binary() -> str:
    import os, shutil, sys

    # 1) variable explicite
    env_bin = os.getenv("SURVEY_BROWSER_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin

    # 2) PATH — sur Linux, priorité aux binaires natifs pour éviter la résolution
    # via l'interop WSL (chrome.exe bind son debug port côté Windows, inaccessible
    # depuis WSL → SessionNotCreatedException à l'attach).
    if sys.platform != "win32":
        for p in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
            if os.path.exists(p):
                return p
        for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
            path = shutil.which(name)
            if path and not path.endswith(".exe"):
                return path
    else:
        for name in ("chrome", "chrome.exe", "chromium", "chromium-browser"):
            path = shutil.which(name)
            if path:
                return path

    # 3) chemins standards
    candidates = [
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        # Linux
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p

    raise FileNotFoundError(
        "Chrome/Chromium introuvable. "
        "Installe Chromium (Linux/Docker) ou Chrome (Windows)."
    )

def _parse_proxy_env(config: dict | None = None):
    """
    Récupère le proxy depuis :
    1) config (source principale)
    2) os.environ (fallback CI / debug)
    """

    def _get(key):
        if config and key in config and config[key]:
            return str(config[key]).strip()
        return os.getenv(key)

    proxy_url  = _get("PROXY_URL")
    proxy_user = _get("PROXY_USER")
    proxy_pass = _get("PROXY_PASS")

    if not proxy_url:
        return None, None, None

    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url

    parsed = urlparse(proxy_url)
    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"

    if not proxy_user or not proxy_pass:
        return server, None, None

    return server, proxy_user, proxy_pass

def _want_headless() -> bool:
    """
    Headless si SURVEY_HEADLESS=1 et pas de DISPLAY.
    """
    import sys
    if sys.platform == "win32":
        return os.getenv("SURVEY_HEADLESS", "0") == "1"

    use_display = bool(os.environ.get("DISPLAY"))
    headless_env = os.getenv("SURVEY_HEADLESS", "0") == "1"
    return headless_env or not use_display

def _parse_geo_env():
    """
    Lit GEO_LAT / GEO_LON depuis les variables d'env.
    Fallback: Paris.
    """
    try:
        lat = float(os.getenv("GEO_LAT", "48.8566"))
    except Exception:
        lat = 48.8566
    try:
        lon = float(os.getenv("GEO_LON", "2.3522"))
    except Exception:
        lon = 2.3522
    return {"latitude": lat, "longitude": lon, "accuracy": 50}


def _parse_locale_tz_env():
    """
    Lit SURVEY_LANG / SURVEY_TZ.
    Fallback: fr-FR + Europe/Paris.
    """
    locale = (os.getenv("SURVEY_LANG", "fr-FR") or "fr-FR").strip()
    tz = (os.getenv("SURVEY_TZ", "Europe/Paris") or "Europe/Paris").strip()
    return locale, tz

def _fingerprint_js() -> str:
    """
    Retourne le JS de spoofing fingerprint à injecter sur chaque nouvelle page.

    Patches appliqués :
      - Timezone : Intl.DateTimeFormat patché pour retourner Europe/Paris
      - window.chrome : injecté si absent (contexte Playwright headless)
      - WebRTC : suppression de RTCPeerConnection en prod (non IS_LOCAL)
    """
    return """
        // ── Timezone ─────────────────────────────────────────────────────────
        const _origResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            const opts = _origResolvedOptions.apply(this, arguments);
            opts.timeZone = 'Europe/Paris';
            return opts;
        };

        // ── window.chrome (absent en headless) ───────────────────────────────
        if (!window.chrome) {
            const _chrome = {
                app: {
                    isInstalled: false,
                    InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                    RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
                },
                runtime: {
                    OnInstalledReason: {}, OnRestartRequiredReason: {},
                    PlatformArch: {}, PlatformNaclArch: {}, PlatformOs: {},
                    RequestUpdateCheckStatus: {},
                },
                loadTimes: function() {
                    return {
                        requestTime: Date.now() / 1000, startLoadTime: Date.now() / 1000,
                        commitLoadTime: Date.now() / 1000, finishDocumentLoadTime: Date.now() / 1000,
                        finishLoadTime: Date.now() / 1000, firstPaintTime: Date.now() / 1000,
                        firstPaintAfterLoadTime: 0, navigationType: 'Other',
                        wasFetchedViaSpdy: false, wasNpnNegotiated: true,
                        npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false,
                        connectionInfo: 'h2',
                    };
                },
                csi: function() {
                    return {
                        startE: Date.now(), onloadT: Date.now(),
                        pageT: Date.now() - performance.timing.navigationStart,
                        tran: 15,
                    };
                },
            };
            try {
                Object.defineProperty(window, 'chrome', { value: _chrome, writable: false, enumerable: true, configurable: false });
            } catch(e) {}
        }
    """ + ("""
        // ── WebRTC suppression (prod uniquement) ────────────────────────────
        // Fallback JS : supprime RTCPeerConnection si les flags Chrome ne
        // suffisent pas à bloquer le STUN/ICE sur ce build.
        try {
            Object.defineProperty(window, 'RTCPeerConnection',       { value: undefined, writable: false });
            Object.defineProperty(window, 'webkitRTCPeerConnection', { value: undefined, writable: false });
        } catch(e) {}
    """ if not IS_LOCAL else "")



def _detect_chrome_major_version(chrome_bin: str) -> int | None:
    """Retourne le numéro de version majeure de Chrome (ex: 145), ou None si échec."""
    import subprocess, re, sys

    def _extract(text):
        m = re.search(r"(\d+)\.\d+\.\d+", text)
        return int(m.group(1)) if m else None

    # Méthode 1 : PowerShell (Windows — fiable)
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Item '{chrome_bin}').VersionInfo.FileVersion"],
                capture_output=True, text=True, timeout=8
            )
            v = _extract(result.stdout.strip())
            if v:
                return v
        except Exception:
            pass

    # Méthode 2 : --version (Linux/Mac)
    try:
        result = subprocess.run(
            [chrome_bin, "--version"],
            capture_output=True, text=True, timeout=5
        )
        v = _extract(result.stdout + result.stderr)
        if v:
            return v
    except Exception:
        pass

    return None


def _start_proxy_relay(proxy_server: str, proxy_user: str, proxy_pass: str, bind_host: str = "127.0.0.1"):
    """
    Relay HTTP CONNECT en Python pur (sans dépendance externe).
    Écoute sur bind_host:<local_port>, intercepte les requêtes CONNECT de Chrome,
    et les relaie vers le proxy ISP upstream avec Proxy-Authorization: Basic.
    Chrome reçoit --proxy-server=http://<bind_host>:<local_port> sans credentials.
    Retourne (relay_handle, local_port) — relay_handle a une méthode terminate().
    """
    import socket
    import threading
    import base64

    parsed = urlparse(proxy_server if "://" in proxy_server else "http://" + proxy_server)
    proxy_host = parsed.hostname
    proxy_port = parsed.port or 8080

    local_port = random.randint(34000, 44000)
    auth_b64 = base64.b64encode(f"{proxy_user}:{proxy_pass}".encode()).decode()
    stop_event = threading.Event()
    _ready_event = threading.Event()

    def _pipe(src: socket.socket, dst: socket.socket) -> None:
        try:
            while not stop_event.is_set():
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    def _handle(client_sock: socket.socket) -> None:
        upstream_sock = None
        try:
            # Lire la requête complète de Chrome (CONNECT ou HTTP directe)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = client_sock.recv(4096)
                if not chunk:
                    return
                buf += chunk

            first_line = buf.split(b"\r\n", 1)[0]  # ex: b"CONNECT host:443 HTTP/1.1"

            # Connexion TCP vers le proxy ISP upstream
            upstream_sock = socket.create_connection((proxy_host, proxy_port), timeout=15)

            if first_line.upper().startswith(b"CONNECT "):
                # ── Tunnel HTTPS (CONNECT) ───────────────────────────────────
                connect_req = (
                    first_line + b"\r\n"
                    + b"Proxy-Authorization: Basic " + auth_b64.encode() + b"\r\n"
                    + b"\r\n"
                )
                upstream_sock.sendall(connect_req)

                # Lire la réponse upstream (ex: "HTTP/1.1 200 Connection established")
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = upstream_sock.recv(4096)
                    if not chunk:
                        break
                    resp += chunk

                # Transmettre la réponse à Chrome
                client_sock.sendall(resp)
            else:
                # ── Requête HTTP directe (non-CONNECT) ──────────────────────
                # Sur Windows, Chrome émet des GET/POST directs au démarrage
                # (update, sync, NTP). Injecter Proxy-Authorization après la
                # première ligne et transmettre au proxy upstream.
                first_line_end = buf.index(b"\r\n")
                req_with_auth = (
                    buf[:first_line_end + 2]
                    + b"Proxy-Authorization: Basic " + auth_b64.encode() + b"\r\n"
                    + buf[first_line_end + 2:]
                )
                upstream_sock.sendall(req_with_auth)

            # Relay bidirectionnel (CONNECT : après établissement du tunnel ;
            # HTTP directe : relaie la réponse upstream → Chrome)
            t1 = threading.Thread(target=_pipe, args=(client_sock, upstream_sock), daemon=True)
            t2 = threading.Thread(target=_pipe, args=(upstream_sock, client_sock), daemon=True)
            t1.start()
            t2.start()
        except Exception:
            if upstream_sock:
                try:
                    upstream_sock.close()
                except Exception:
                    pass
            try:
                client_sock.close()
            except Exception:
                pass

    def _serve() -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((bind_host, local_port))
        srv.listen(64)
        # Signal readiness exactly here: OS will queue incoming connections from now on.
        _ready_event.set()
        srv.settimeout(1.0)
        try:
            while not stop_event.is_set():
                try:
                    client_sock, _ = srv.accept()
                    threading.Thread(target=_handle, args=(client_sock,), daemon=True).start()
                except socket.timeout:
                    continue
                except Exception:
                    break
        finally:
            srv.close()

    threading.Thread(target=_serve, daemon=True).start()

    class _RelayHandle:
        def terminate(self) -> None:
            stop_event.set()

    # Phase 1 — attendre que listen() soit effectif (Event positionné par _serve).
    # Garantit que l'OS peut désormais mettre des connexions en file d'attente.
    if not _ready_event.wait(timeout=5.0):
        stop_event.set()
        raise RuntimeError(
            f"[RELAY] listen() n'a pas abouti sur le port {local_port} après 5s"
        )

    # Phase 2 — sonde TCP active : vérifier que le relay reçoit effectivement
    # des connexions (et non que le thread est mort juste après listen()).
    # Budget explicite : 20 tentatives × 50 ms = 1 s max.
    _PROBE_HOST = "127.0.0.1"
    _PROBE_MAX = 20
    for _attempt in range(_PROBE_MAX):
        try:
            with socket.create_connection((_PROBE_HOST, local_port), timeout=0.3):
                break
        except OSError:
            if _attempt == _PROBE_MAX - 1:
                stop_event.set()
                raise RuntimeError(
                    f"[RELAY] relay non joignable sur {_PROBE_HOST}:{local_port} "
                    f"après {_PROBE_MAX} tentatives"
                )
            time.sleep(0.05)

    log_info("[LAUNCH][RELAY]", f"relay HTTP CONNECT prêt sur port {local_port} → {proxy_host}:{proxy_port}")
    return _RelayHandle(), local_port



def launch_browser_playwright(config: dict | None = None):
    """
    Lance Chrome via Playwright (mode pipe, sans --remote-debugging-port).

    Identique à launch_browser() côté arguments Chrome, sauf :
      - pas de --remote-debugging-port / --remote-debugging-address / --remote-debugging-allow-origins
      - proxy passé directement à Playwright (pas de relay local)
      - fingerprint JS injecté via context.add_init_script()

    Retourne une Page Playwright native prête à l'emploi.
    """
    from playwright.sync_api import sync_playwright

    chrome_bin            = _detect_chrome_binary()
    proxy_server, proxy_user, proxy_pass = _parse_proxy_env(config)
    locale, tz            = _parse_locale_tz_env()
    headless              = _want_headless()

    user_data_dir = tempfile.mkdtemp(prefix="chrome_profile_pw_")

    log_info("[LAUNCH][PW]", f"chrome_bin={chrome_bin} headless={headless} locale={locale} tz={tz} proxy={proxy_server or 'none'}")

    # ── Arguments Chrome (identiques à launch_browser, hors remote-debugging-*) ──
    # --user-data-dir est passé directement à launch_persistent_context(), pas ici.
    chrome_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        *( ["--no-sandbox", "--test-type"] if (not hasattr(os, "getuid") or os.getuid() == 0) else [] ),
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--no-pings",
        "--disable-domain-reliability",
        "--disable-client-side-phishing-detection",
        "--safebrowsing-disable-auto-update",
        "--disable-features=Translate,OptimizationHints,SafeBrowsingProtections,"
            "SafeBrowsingRealTimeUrlLookupEnabled,ChromeWhatsNewUI,"
            "MediaRouter,DialMediaRouteProvider",
        "--ash-no-nudges",
        "--disable-ntp-most-likely-favicons-from-server",
        "--disable-search-engine-choice-screen",
        "--disable-extensions",
        "--disable-notifications",
        "--window-size=1920,1080",
        "--lang=en-US",
    ]

    if not IS_LOCAL:
        chrome_args += [
            "--disable-features=WebRTC",
            "--enforce-webrtc-ip-permission-check",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        ]

    if not headless and os.environ.get("DISPLAY") and ".exe" not in chrome_bin.lower():
        chrome_args.extend(["--use-gl=angle", "--use-angle=swiftshader"])

    # ── Proxy Playwright natif (pas de relay local) ───────────────────────────
    pw_proxy = None
    if proxy_server:
        pw_proxy = {"server": proxy_server}
        if proxy_user and proxy_pass:
            pw_proxy["username"] = proxy_user
            pw_proxy["password"] = proxy_pass

    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    )

    pw = sync_playwright().start()
    # launch_persistent_context reçoit user_data_dir en premier argument positionnel
    # et interdit --user-data-dir dans args (erreur Playwright explicite).
    # Il retourne un BrowserContext (pas un Browser) : context.close() ferme tout.
    context = pw.chromium.launch_persistent_context(
        user_data_dir,
        executable_path=chrome_bin,
        args=chrome_args,
        env={**os.environ, "TZ": tz},
        headless=headless,
        locale=locale,
        timezone_id=tz,
        user_agent=_UA,
        viewport={"width": 1920, "height": 1080},
        proxy=pw_proxy,
    )
    context.add_init_script(_fingerprint_js())
    # launch_persistent_context ouvre toujours une page about:blank dans context.pages[0].
    # On la réutilise pour éviter un second onglet parasite.
    page = context.pages[0] if context.pages else context.new_page()

    page._pw                   = pw
    page._chrome_user_data_dir = user_data_dir
    log_info("[LAUNCH][PW]", "Browser Playwright lancé, fingerprint injecté via add_init_script.")
    return page


# ─────────────────────────────────────────────────────────────────────────────
# MODE DÉBOGAGE LOCAL — fenêtre visible, navigation manuelle
# Pas un mode de production : ne pas appeler depuis le bot en prod.
# ─────────────────────────────────────────────────────────────────────────────

def launch_browser_playwright_debug(config: dict | None = None):
    """
    Lance Chrome via Playwright avec fenêtre visible (headless=False), fingerprint
    et proxy identiques à launch_browser_playwright(), pour observation manuelle.

    Usage : le bot rend la main après le lancement ; l'utilisateur navigue jusqu'à
    la page problématique et appuie sur Entrée pour que le bot reprenne, ou ferme
    la fenêtre pour terminer la session de débogage.

    Ne modifie pas launch_browser() ni launch_browser_playwright().
    """
    from playwright.sync_api import sync_playwright

    chrome_bin                   = _detect_chrome_binary()
    proxy_server, proxy_user, proxy_pass = _parse_proxy_env(config)
    locale, tz                   = _parse_locale_tz_env()

    user_data_dir = tempfile.mkdtemp(prefix="chrome_profile_pw_dbg_")

    log_info("[LAUNCH][PW][DBG]", f"chrome_bin={chrome_bin} locale={locale} tz={tz} proxy={proxy_server or 'none'} user_data_dir={user_data_dir}")

    chrome_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        *( ["--no-sandbox", "--test-type"] if (not hasattr(os, "getuid") or os.getuid() == 0) else [] ),
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--no-pings",
        "--disable-domain-reliability",
        "--disable-client-side-phishing-detection",
        "--safebrowsing-disable-auto-update",
        "--disable-features=Translate,OptimizationHints,SafeBrowsingProtections,"
            "SafeBrowsingRealTimeUrlLookupEnabled,ChromeWhatsNewUI,"
            "MediaRouter,DialMediaRouteProvider",
        "--ash-no-nudges",
        "--disable-ntp-most-likely-favicons-from-server",
        "--disable-search-engine-choice-screen",
        "--disable-extensions",
        "--disable-notifications",
        "--start-maximized",
        "--lang=en-US",
        "--auto-open-devtools-for-tabs",  # DevTools ouverts d'emblée pour observation
    ]

    pw_proxy = None
    if proxy_server:
        pw_proxy = {"server": proxy_server}
        if proxy_user and proxy_pass:
            pw_proxy["username"] = proxy_user
            pw_proxy["password"] = proxy_pass

    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    )

    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir,
        executable_path=chrome_bin,
        args=chrome_args,
        env={**os.environ, "TZ": tz},
        headless=False,          # toujours visible, indépendamment de SURVEY_HEADLESS
        locale=locale,
        timezone_id=tz,
        user_agent=_UA,
        no_viewport=True,        # viewport naturel = taille réelle de la fenêtre OS
        proxy=pw_proxy,
    )
    context.add_init_script(_fingerprint_js())
    page = context.new_page()

    print(
        "\n[DBG] Navigateur Playwright lancé en mode débogage (non-headless).\n"
        "  → Navigue manuellement jusqu'à la page de présélection problématique.\n"
        "  → Ouvre les DevTools (F12) si pas déjà ouverts, onglet Network ou Console.\n"
        "  → Appuie sur Entrée ici pour rendre la main au bot (semi-auto),\n"
        "    ou ferme la fenêtre Chrome pour terminer la session.\n"
    )
    input("[DBG] Appuie sur Entrée pour continuer... ")

    page._pw                   = pw
    page._chrome_user_data_dir = user_data_dir
    log_info("[LAUNCH][PW][DBG]", "Page prête après navigation manuelle.")
    return page


def attach_browser_playwright(attach_addr: str):
    """
    Attache Playwright à une instance Chrome déjà lancée via CDP.

    Paramètre attach_addr : adresse de débogage Chrome, ex. "127.0.0.1:9222"
    (ATTACH_DEBUGGER_ADDRESS). Le préfixe http:// est ajouté si absent.

    Retourne (pw, browser) :
      - pw      : instance Playwright (à garder vivante tant que le browser est utilisé)
      - browser : Browser Playwright connecté via CDP

    L'appelant récupère les pages depuis browser.contexts[0].pages.
    Ne modifie PAS le chemin de lancement prod (launch_browser_playwright).
    """
    from playwright.sync_api import sync_playwright

    endpoint = attach_addr if "://" in attach_addr else f"http://{attach_addr}"
    log_info("[ATTACH_PW]", f"connect_over_cdp → {endpoint}")

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(endpoint)

    contexts = browser.contexts
    if not contexts:
        pw.stop()
        raise RuntimeError(f"[ATTACH_PW] Aucun contexte CDP disponible sur {endpoint}")

    total_pages = sum(len(c.pages) for c in contexts)
    log_info("[ATTACH_PW]", f"Connecté. contexts={len(contexts)} pages_total={total_pages}")
    print(f"[ATTACH_PW] Connecté à {endpoint} | contexts={len(contexts)} pages={total_pages}")
    return pw, browser


if __name__ == "__main__":
    """
    Point d'entrée de débogage local :
        python -m preselection.playwright_launcher [option1] [option2] …

    Exemples :
        python -m preselection.playwright_launcher "Homme" "18-24 ans"
        python -m preselection.playwright_launcher          # saisie interactive

    Variables d'env utiles (optionnelles) :
        PROXY_URL / PROXY_USER / PROXY_PASS  — proxy identique à la prod
        SURVEY_LANG / SURVEY_TZ              — locale/timezone
        SURVEY_BROWSER_BIN                   — chemin Chrome explicite

    Workflow :
      1. Chrome s'ouvre en fenêtre visible avec DevTools.
      2. Navigue manuellement jusqu'à la question à cocher, puis appuie sur Entrée.
      3. Le bot appelle select_checkbox_answers() sur la page en cours.
      4. Le bot appelle click_next_button() — pause de confirmation si
         LOCAL_CTA_REQUIRE_ENTER=1 est positionnée, sinon clic immédiat.
      5. Observe le résultat dans Chrome/DevTools, puis appuie sur Entrée pour fermer.
    """
    import sys
    from preselection.response_executor import select_checkbox_answers, click_next_button

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    # Options CLI pré-lues avant le lancement pour un démarrage immédiat si fournies ;
    # sinon None — la saisie interactive aura lieu après navigation (voir ci-dessous).
    cli_answers = sys.argv[1:] if len(sys.argv) > 1 else None

    print("[DBG] Lancement du navigateur Playwright en mode débogage local…")
    shim = launch_browser_playwright_debug()
    # launch_browser_playwright_debug() contient déjà un input() qui attend que
    # l'utilisateur ait navigué jusqu'à la page cible et appuyé sur Entrée.

    # Saisie des options après navigation : l'utilisateur voit la page dans Chrome
    if cli_answers is not None:
        answers = cli_answers
    else:
        raw = input("[DBG] Options à cocher (séparées par | ) : ")
        answers = [a.strip() for a in raw.split("|") if a.strip()]

    if not answers:
        print("[DBG] Aucune option fournie — abandon.")
        try:
            shim._pw.stop()
        except Exception:
            pass
        sys.exit(1)

    print(f"[DBG] Options cibles : {answers}")
    # Tentative de clic via la fonction existante (non modifiée)
    print(f"[DBG] Appel select_checkbox_answers({answers})…")
    try:
        result = select_checkbox_answers(shim, answers)
        print(f"[DBG] select_checkbox_answers → {result}")
    except Exception as exc:
        print(f"[DBG] Erreur lors de select_checkbox_answers : {exc}")

    # Clic CTA après la coche — réutilise click_next_button() tel qu'utilisé en prod,
    # qui gère déjà lui-même la pause de confirmation (LOCAL_CTA_REQUIRE_ENTER) via
    # should_pause_before_cta(). Rien à dupliquer ici : si la var env n'est pas set,
    # le clic part directement sans confirmation, comme en mode normal.
    print("[DBG] Appel click_next_button()…")
    try:
        cta_result = click_next_button(shim)
        print(f"[DBG] click_next_button → {cta_result}")
    except Exception as exc:
        print(f"[DBG] Erreur lors de click_next_button : {exc}")

    # Maintien de la session pour observation — fermeture explicite par l'utilisateur
    input("[DBG] Observe le navigateur, puis appuie sur Entrée pour fermer la session… ")
    try:
        shim._pw.stop()
    except Exception:
        pass
    sys.exit(0)