from __future__ import annotations
import os, time
import subprocess
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium import webdriver
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

log = logging.getLogger(__name__)


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

    Source unique utilisée par apply_fingerprint_overrides_cdp() via CDP Selenium
    (Page.addScriptToEvaluateOnNewDocument), ce qui garantit que le script est
    exécuté avant tout autre JS pour TOUTES les navigations Selenium.

    Patches appliqués :
      - Langue / Timezone
      - navigator.webdriver  (patch robuste sur Navigator.prototype)
      - navigator.platform
      - navigator.plugins    (vide en headless → simuler 3 plugins Chrome réels)
      - navigator.mimeTypes  (lié aux plugins)
      - window.chrome        (absent en headless → injecter l'objet complet)
      - WebGL renderer       (SwiftShader détectable → spoofer Intel)
      - screen dimensions    (cohérent avec --window-size=1920,1080)
      - hardwareConcurrency / deviceMemory
    """
    return """
        // ── Langue ──────────────────────────────────────────────────────────
        Object.defineProperty(navigator, 'language',  { get: () => 'fr-FR' });
        Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr'] });

        // ── Timezone ─────────────────────────────────────────────────────────
        const _origResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            const opts = _origResolvedOptions.apply(this, arguments);
            opts.timeZone = 'Europe/Paris';
            return opts;
        };

        // ── navigator.webdriver (patch robuste sur le prototype) ─────────────
        // Simple Object.defineProperty(navigator, ...) est contournable via
        // Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver').
        try {
            Object.defineProperty(Navigator.prototype, 'webdriver', {
                get: () => undefined,
                configurable: true,
                enumerable: true,
            });
        } catch(e) {}

        // ── Platform ─────────────────────────────────────────────────────────
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

        // ── navigator.plugins (vide = signal bot primaire) ───────────────────
        // Chrome réel expose 3 plugins PDF/NaCl. On les simule.
        try {
            const _pluginData = [
                { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client',      filename: 'internal-nacl-plugin',             description: '' },
            ];
            const _makePlugin = (d) => {
                const mt = { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: d.description };
                const p  = Object.create(Plugin.prototype);
                Object.defineProperties(p, {
                    name:        { value: d.name,        enumerable: true },
                    filename:    { value: d.filename,    enumerable: true },
                    description: { value: d.description, enumerable: true },
                    length:      { value: 1,             enumerable: true },
                    0:           { value: mt,             enumerable: true },
                });
                return p;
            };
            const _pa = Object.create(PluginArray.prototype);
            _pluginData.forEach((d, i) => Object.defineProperty(_pa, i, { value: _makePlugin(d), enumerable: true }));
            Object.defineProperties(_pa, {
                length:    { value: _pluginData.length },
                refresh:   { value: () => {} },
                item:      { value: (i) => _pa[i] },
                namedItem: { value: (n) => { const i = _pluginData.findIndex(p => p.name === n); return i >= 0 ? _pa[i] : null; } },
            });
            Object.defineProperty(navigator, 'plugins', { get: () => _pa });
        } catch(e) {}

        // ── navigator.mimeTypes ───────────────────────────────────────────────
        try {
            const _mta = Object.create(MimeTypeArray.prototype);
            const _mt0 = { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' };
            Object.defineProperty(_mta, 0,        { value: _mt0, enumerable: true });
            Object.defineProperty(_mta, 'length', { value: 1 });
            Object.defineProperty(_mta, 'item',   { value: (i) => _mta[i] });
            Object.defineProperty(navigator, 'mimeTypes', { get: () => _mta });
        } catch(e) {}

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

        // ── WebGL renderer (SwiftShader = signal bot connu) ──────────────────
        // Même si le renderer réel est SwiftShader (pas de GPU en container),
        // on spoofe la chaîne pour correspondre à un Intel intégré classique.
        try {
            const _glProxy = {
                apply(target, ctx, args) {
                    const p = args[0];
                    if (p === 37445) return 'Intel Inc.';                    // UNMASKED_VENDOR_WEBGL
                    if (p === 37446) return 'Intel(R) Iris(R) Xe Graphics';  // UNMASKED_RENDERER_WEBGL
                    return Reflect.apply(target, ctx, args);
                }
            };
            WebGLRenderingContext.prototype.getParameter  = new Proxy(WebGLRenderingContext.prototype.getParameter,  _glProxy);
            WebGL2RenderingContext.prototype.getParameter = new Proxy(WebGL2RenderingContext.prototype.getParameter, _glProxy);
        } catch(e) {}

        // ── screen dimensions (cohérent avec --window-size=1920,1080) ────────
        try {
            Object.defineProperty(screen, 'width',       { get: () => 1920 });
            Object.defineProperty(screen, 'height',      { get: () => 1080 });
            Object.defineProperty(screen, 'availWidth',  { get: () => 1920 });
            Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
            Object.defineProperty(screen, 'colorDepth',  { get: () => 24 });
            Object.defineProperty(screen, 'pixelDepth',  { get: () => 24 });
        } catch(e) {}

        // ── Hardware hints (cohérents avec un laptop standard) ───────────────
        try {
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4 });
            Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8 });
        } catch(e) {}
    """


def apply_fingerprint_overrides_cdp(driver) -> None:
    """
    Injecte le JS de spoofing fingerprint via CDP Selenium.

    Utilise Page.addScriptToEvaluateOnNewDocument : le script est exécuté
    avant tout autre JS pour TOUTES les navigations futures du processus Chrome,
    indépendamment de qui navigue (Playwright ou Selenium).

    Doit être appelé une seule fois, juste après l'attach Selenium et avant
    tout driver.get().
    """
    # Surcharge User-Agent via CDP : élimine "HeadlessChrome" et "Linux x86_64"
    # qui sont les signaux de détection les plus triviaux des anti-bots.
    # Doit correspondre à un vrai Chrome Windows pour rester cohérent avec
    # les patches JS (platform=Win32, screen=1920x1080).
    try:
        import re as _re
        raw_ua = driver.execute_script("return navigator.userAgent") or ""
        # Remplacer HeadlessChrome → Chrome et Linux x86_64 → Windows NT 10.0; Win64; x64
        spoofed_ua = _re.sub(r"HeadlessChrome", "Chrome", raw_ua)
        spoofed_ua = _re.sub(r"Linux x86_64", "Windows NT 10.0; Win64; x64", spoofed_ua)
        driver.execute_cdp_cmd("Network.setUserAgentOverride", {
            "userAgent": spoofed_ua,
            "platform": "Win32",
        })
        log.info("[FP][CDP] User-Agent surchargé : %s", spoofed_ua)
    except Exception as e:
        log.warning("[FP][CDP][WARN] Échec User-Agent override : %s", e)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _fingerprint_js()}
        )
        log.info("[FP][CDP] Fingerprint overrides enregistrés via CDP.")
    except Exception as e:
        log.warning("[FP][CDP][WARN] Échec enregistrement fingerprint CDP : %s", e)


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

    # Attendre que le relay accepte des connexions (max 10s)
    deadline = time.time() + 10.0
    ready = False
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.5):
                ready = True
                break
        except Exception:
            time.sleep(0.1)

    if not ready:
        stop_event.set()
        raise RuntimeError(
            f"relay HTTP CONNECT n'a pas exposé le port {local_port} après 10s"
        )

    print(f"[LAUNCH][RELAY] relay HTTP CONNECT prêt sur port {local_port} → {proxy_host}:{proxy_port}")
    return _RelayHandle(), local_port


def launch_browser(config: dict | None = None):
    """
    Chemin unique : subprocess.Popen lance Chrome, puis Selenium s'attache via
    debuggerAddress — identique en local et en prod.
    Exception : ATTACH_DEBUGGER_ADDRESS (attach externe) reste inchangé.
    """
    chrome_bin = _detect_chrome_binary()

    attach_addr = os.getenv("ATTACH_DEBUGGER_ADDRESS", "").strip()
    if attach_addr:
        print(f"⚠️ ATTACH MODE → {attach_addr}")
        opts = webdriver.ChromeOptions()
        opts.add_experimental_option("debuggerAddress", attach_addr)
        opts.page_load_strategy = "eager"
        return webdriver.Chrome(options=opts, service=Service(log_output=subprocess.DEVNULL))

    proxy_server, proxy_user, proxy_pass = _parse_proxy_env(config)
    headless = _want_headless()

    # Port remote debugging (Selenium va s'attacher dessus)
    debug_port = int(os.getenv("REMOTE_DEBUG_PORT", 0)) or random.randint(42000, 52000)
    debug_address = os.getenv("REMOTE_DEBUG_ADDRESS", "").strip()

    # Profil isolé (évite collisions + garde la session propre)
    # Si ACCOUNT_ID + DATABASE_URL sont définis, on utilise un répertoire fixe et
    # on charge le profil persisté depuis Postgres (anti-bot : évite le profil vierge).
    # Sinon : comportement original (mkdtemp éphémère, ou %TEMP% pour Chrome Windows).
    _persist_account_id = os.getenv("ACCOUNT_ID", "").strip()
    _persist_db_url = os.getenv("DATABASE_URL", "").strip()

    if _persist_account_id and _persist_db_url:
        user_data_dir = f"/tmp/chrome_profile_{_persist_account_id}"
        os.makedirs(user_data_dir, exist_ok=True)
        from preselection.chrome_profile_store import load_profile
        load_profile(_persist_account_id, user_data_dir)
    elif ".exe" in chrome_bin.lower():
        # Si chrome_bin est un binaire Windows, créer le profil sous %TEMP% Windows natif
        # (wslpath -w produit un chemin UNC \\wsl.localhost\... rejeté par Chrome comme profil).
        try:
            win_temp = subprocess.check_output(
                ["cmd.exe", "/c", "echo %TEMP%"], text=True
            ).strip()
            import uuid
            user_data_dir = win_temp + "\\chrome_profile_" + uuid.uuid4().hex[:8]
            os.makedirs(user_data_dir, exist_ok=True)
        except Exception:
            user_data_dir = tempfile.mkdtemp(prefix="chrome_profile_")
    else:
        user_data_dir = tempfile.mkdtemp(prefix="chrome_profile_")

    print(f"[LAUNCH] chrome_bin={chrome_bin}")
    print(f"[LAUNCH] headless={headless}")
    print(f"[LAUNCH] debug_port={debug_port}")
    print(f"[LAUNCH] user_data_dir={user_data_dir}")

    if proxy_server:
        print(f"[LAUNCH][PROXY] server={proxy_server} user={'yes' if proxy_user else 'no'} pass={'yes' if proxy_pass else 'no'}")
    else:
        print("[LAUNCH][PROXY] aucun proxy (PROXY_URL vide)")

    locale, tz = _parse_locale_tz_env()
    print(f"[LAUNCH][LOCALE] {locale}  [LAUNCH][TZ] {tz}")

    # ── Arguments Chrome ──────────────────────────────────────────────────────
    # NOTE: --disable-gpu SUPPRIMÉ volontairement.
    #   Avec Xvfb (DISPLAY=:99), Chrome tourne en mode headed sur un écran
    #   virtuel et n'a pas besoin de désactiver le GPU.
    #   --disable-gpu forçait SwiftShader comme renderer WebGL, une signature
    #   de bot détectée par ThreatMetrix/Datadome dès le premier chargement.
    cmd = [
        chrome_bin,
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-features=Translate,OptimizationHints",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1920,1080",
        f"--lang={locale}",
    ]

    relay_proc = None
    if proxy_server and proxy_user and proxy_pass:
        # Relay local pproxy : Chrome reçoit un proxy sans credentials
        # Chrome Windows (WSL) ne peut pas atteindre 127.0.0.1 WSL — on bind sur 0.0.0.0
        # et on passe l'IP du bridge WSL (hostname -I) à la place de 127.0.0.1.
        if ".exe" in chrome_bin.lower():
            try:
                wsl_ip = subprocess.check_output(["hostname", "-I"], text=True).strip().split()[0]
            except Exception:
                wsl_ip = "127.0.0.1"
            relay_proc, local_port = _start_proxy_relay(proxy_server, proxy_user, proxy_pass, bind_host="0.0.0.0")
            cmd.append(f"--proxy-server=http://{wsl_ip}:{local_port}")
        else:
            relay_proc, local_port = _start_proxy_relay(proxy_server, proxy_user, proxy_pass)
            cmd.append(f"--proxy-server=http://127.0.0.1:{local_port}")
    elif proxy_server:
        # Proxy sans auth : on passe directement
        cmd.append(f"--proxy-server={proxy_server}")

    if debug_address:
        cmd.append(f"--remote-debugging-address={debug_address}")

    if headless:
        # Fallback si Xvfb indisponible (ex: test local sans DISPLAY).
        # En prod normale, DISPLAY=:99 est positionné par entrypoint.sh
        # et headless=False → ce bloc ne s'exécute pas.
        cmd.append("--headless=new")

    # ── Env subprocess : TZ pour la timezone ─────────────────────────────────
    proc_env = os.environ.copy()
    proc_env["TZ"] = tz

    # --- 1) Lancer Chrome via subprocess.Popen ---
    chrome_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=proc_env,
    )
    print(f"[LAUNCH] Chrome PID={chrome_proc.pid}")

    # --- Relay socat : expose le debug port sur 0.0.0.0 ---
    # (Playwright forçait Chrome sur 127.0.0.1 ; subprocess respecte
    #  --remote-debugging-address mais socat reste utile en Docker)
    if debug_address == "0.0.0.0":
        relay_port = debug_port + 1
        subprocess.Popen(
            ["socat",
             f"TCP-LISTEN:{relay_port},fork,reuseaddr,bind=0.0.0.0",
             f"TCP:127.0.0.1:{debug_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"[LAUNCH] socat relay 0.0.0.0:{relay_port} → 127.0.0.1:{debug_port}")

    # --- 2) Attacher Selenium au Chrome déjà lancé ---
    # Attendre que Chrome expose son debug port (jusqu'à 10s)
    import urllib.request
    for attempt in range(20):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json", timeout=1)
            print(f"[LAUNCH] Debug port prêt après {attempt * 0.5:.1f}s")
            break
        except Exception:
            time.sleep(0.5)
    else:
        print(f"[LAUNCH][WARN] Debug port toujours indisponible après 10s — tentative attach quand même")

    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
    opts.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=opts, service=Service(log_output=subprocess.DEVNULL))

    # Fingerprint spoofing via CDP Selenium.
    # Injecté AVANT toute navigation pour que le script soit actif dès la première
    # vraie navigation. Page.addScriptToEvaluateOnNewDocument persiste pour
    # toutes les navigations futures du processus Chrome.
    apply_fingerprint_overrides_cdp(driver)
    print("[LAUNCH][OVERRIDE] Fingerprint overrides enregistrés via CDP Selenium.")

    try:
        fingerprint = driver.execute_script("""
            return {
                language: navigator.language,
                languages: navigator.languages,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                platform: navigator.platform,
                webdriver: navigator.webdriver,
                userAgent: navigator.userAgent,
                geolocation: !!navigator.geolocation
            };
        """)
        print("[FP][BROWSER]", json.dumps(fingerprint, indent=2))
    except Exception as e:
        print("[FP][ERROR]", e)

    # Attacher le processus Chrome et le profil au driver pour nettoyage dans main.py
    driver._chrome_proc = chrome_proc
    driver._chrome_user_data_dir = user_data_dir
    if relay_proc is not None:
        driver._proxy_relay_proc = relay_proc

    # Pause manuelle en local non-unattended : permet la navigation préalable avant
    # que le bot prenne la main. Skippé si LOCAL_UNATTENDED=1 ou en prod (IS_LOCAL=False).
    if IS_LOCAL and os.getenv("LOCAL_UNATTENDED", "") != "1":
        print(
            f"\n[LAUNCH] Chrome lancé sur port {debug_port}.\n"
            f"  → Navigue manuellement vers la page cible dans Chrome.\n"
            f"  → Appuie sur Entrée ici quand tu es prêt à continuer."
        )
        input("[LAUNCH] Appuie sur Entrée pour continuer... ")

    return driver
