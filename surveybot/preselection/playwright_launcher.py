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

    log.info("[LAUNCH][RELAY] relay HTTP CONNECT prêt sur port %d → %s:%d",
             local_port, proxy_host, proxy_port)
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
        log.info("[LAUNCH][PROXY] server=%s user=%s pass=%s",
                 proxy_server, "yes" if proxy_user else "no", "yes" if proxy_pass else "no")
    else:
        log.info("[LAUNCH][PROXY] aucun proxy (PROXY_URL vide)")

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
        *( ["--no-sandbox"] if (not hasattr(os, "getuid") or os.getuid() == 0) else [] ),
        # ── Réseau interne Chrome — neutralisation complète ────────────────────
        # Ces connexions surviennent dès le lancement, avant le premier driver.get(),
        # et saturent/contournent le relay proxy, déclenchant la popup d'auth native.
        "--disable-background-networking",       # déjà présent — base
        "--disable-component-update",            # déjà présent — base
        "--disable-sync",                        # Google Sync (contacts, bookmarks…)
        "--no-pings",                            # hyperlink auditing pings
        "--disable-domain-reliability",          # rapports d'erreurs réseau → Google
        "--disable-client-side-phishing-detection",  # modèle ML local, pas de réseau
        "--safebrowsing-disable-auto-update",    # stoppe le téléchargement des listes Safe Browsing
        "--disable-features=Translate,OptimizationHints,SafeBrowsingProtections,"
            "SafeBrowsingRealTimeUrlLookupEnabled,ChromeWhatsNewUI,"
            "NetworkService,MediaRouter,DialMediaRouteProvider",
        # ── NTP / background fetch ────────────────────────────────────────────
        "--ash-no-nudges",                       # supprime les popups Ash (ChromeOS no-op sur Linux)
        "--disable-ntp-most-likely-favicons-from-server",  # NTP : pas de fetch favicon
        "--disable-search-engine-choice-screen",  # pas de requête réseau au démarrage
        # ── Extensions & notifications (pas de connexion background) ─────────
        "--disable-extensions",
        "--disable-notifications",
        # ── Anti-fingerprint / automation ────────────────────────────────────
        # NOTE IMPORTANTE : --disable-blink-features=AutomationControlled est
        # intentionnellement ABSENT de la ligne de commande.
        #
        # Ce flag est contre-productif : il supprime navigator.webdriver=true
        # mais Chrome >= 112 affiche une banniere "You are using an unsupported
        # command-line flag" qui est elle-meme un signal d'automation primaire
        # (visible en screenshot, detecte par les SDK anti-bot).
        #
        # La suppression de navigator.webdriver est assuree exclusivement par
        # apply_fingerprint_overrides_cdp() via Page.addScriptToEvaluateOnNewDocument
        # (patch sur Navigator.prototype avant tout JS de la page).
        # Idem pour les proprietes cdc_* de ChromeDriver.
        "--window-size=1920,1080",
        "--lang=en-US",  # aligné sur navigator.language = 'en-US' (cohérence JS ↔ HTTP headers)
    ]

    # En prod (Fly.io), désactiver WebRTC au niveau Chrome pour éliminer toute
    # fuite d'IP datacenter ou locale via le handshake ICE/STUN.
    # Non appliqué en local : comportement natif conservé pour éviter toute
    # divergence de profil Chrome entre local et prod.
    if not IS_LOCAL:
        cmd += [
            "--disable-features=WebRTC",
            "--enforce-webrtc-ip-permission-check",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
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
    elif os.environ.get("DISPLAY") and ".exe" not in chrome_bin.lower():
        # Mode Xvfb (prod Linux) : activer WebGL via ANGLE/SwiftShader.
        # Sans GPU physique, Chrome ne crée pas de contexte WebGL sans ces flags.
        cmd.extend(["--use-gl=angle", "--use-angle=swiftshader"])

    # ── Env subprocess : TZ pour la timezone ─────────────────────────────────
    proc_env = os.environ.copy()
    proc_env["TZ"] = tz

    _LOCK_FILES = ["SingletonLock", "lockfile", "CrashpadMetrics-active.pma"]
    for _lf in _LOCK_FILES:
        _lf_path = os.path.join(user_data_dir, _lf)
        if os.path.exists(_lf_path):
            try:
                os.remove(_lf_path)
                print(f"[LAUNCH] Lock file supprimé: {_lf}")
            except Exception as _e:
                print(f"[LAUNCH][WARN] Impossible de supprimer {_lf}: {_e}")

    # --- 1) Lancer Chrome via subprocess.Popen ---
    import threading as _threading
    chrome_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=proc_env,
        start_new_session=True
    )
    print(f"[LAUNCH] Chrome PID={chrome_proc.pid}")

    # Drain stderr en thread daemon : évite le blocage pipe-buffer et rend
    # les messages Chrome (OOM, crash, profil lock) visibles en cas de mort.
    _stderr_lines: list[str] = []

    def _drain_stderr(proc):
        try:
            for raw in proc.stderr:
                _stderr_lines.append(raw.decode(errors="replace").rstrip())
        except Exception:
            pass

    _threading.Thread(target=_drain_stderr, args=(chrome_proc,), daemon=True).start()

    # --- Relay socat : expose le debug port sur 0.0.0.0 ---
    # (Playwright forçait Chrome sur 127.0.0.1 ; subprocess respecte
    #  --remote-debugging-address mais socat reste utile en Docker)
    if debug_address == "0.0.0.0":
        relay_port = debug_port + 1
        subprocess.Popen(
            ["socat",
             f"TCP-LISTEN:{relay_port},fork,reuseaddr,bind=0.0.0.0",
             f"TCP:127.0.0.1:{debug_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        print(f"[LAUNCH] socat relay 0.0.0.0:{relay_port} → 127.0.0.1:{debug_port}")

    # --- 2) Attacher Selenium au Chrome déjà lancé ---
    # Attendre que Chrome expose son debug port (jusqu'à 60s).
    # À chaque itération : vérifier que Chrome est toujours vivant avant de réessayer.
    import urllib.request
    for attempt in range(120):
        ret = chrome_proc.poll()
        if ret is not None:
            stderr_tail = "\n".join(_stderr_lines[-30:])
            print(f"[LAUNCH][FATAL] Chrome mort (code={ret}) après {attempt * 0.5:.1f}s.\nstderr:\n{stderr_tail}")
            raise RuntimeError(f"Chrome a quitté avec code={ret}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json", timeout=1)
            print(f"[LAUNCH] Debug port prêt après {attempt * 0.5:.1f}s")
            break
        except Exception:
            time.sleep(0.5)
    else:
        stderr_tail = "\n".join(_stderr_lines[-30:])
        print(f"[LAUNCH][WARN] Debug port toujours indisponible après 60s.\nstderr:\n{stderr_tail}")

    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
    opts.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=opts, service=Service(log_output=subprocess.DEVNULL))

    # ── Suppression immédiate du flag d'automation sur la page COURANTE ────────
    # Page.addScriptToEvaluateOnNewDocument ne couvre PAS la page déjà chargée
    # au moment de l'attach Selenium. On applique les patches critiques de façon
    # synchrone via execute_script pour éliminer les signaux visibles immédiatement.
    #
    # 1) navigator.webdriver → undefined  (sur Navigator.prototype pour couvrir
    #    les checks via Object.getOwnPropertyDescriptor)
    # 2) Suppression des propriétés cdc_* injectées par ChromeDriver dans window
    #    (cdc_adoQpoasnfa76pfcZLmcfl_Array, cdc_adoQpoasnfa76pfcZLmcfl_Promise…)
    try:
        driver.execute_script("""
            // Patch navigator.webdriver sur le prototype (robuste)
            try {
                Object.defineProperty(Navigator.prototype, 'webdriver', {
                    get: () => undefined,
                    configurable: true,
                    enumerable: true,
                });
            } catch(e) {}

            // Supprimer les propriétés cdc_* de ChromeDriver dans window
            try {
                for (const key of Object.getOwnPropertyNames(window)) {
                    if (key.startsWith('cdc_')) {
                        try { delete window[key]; } catch(e) {}
                        try { Object.defineProperty(window, key, { get: () => undefined, configurable: true }); } catch(e) {}
                    }
                }
            } catch(e) {}

            // Supprimer $chrome_asyncScriptInfo et $cdc_asdjflasutopfhvcZLmcfl_
            // (variantes selon version ChromeDriver)
            const _legacyKeys = [
                '$chrome_asyncScriptInfo',
                '$cdc_asdjflasutopfhvcZLmcfl_',
            ];
            for (const k of _legacyKeys) {
                try { delete window[k]; } catch(e) {}
                try { Object.defineProperty(window, k, { get: () => undefined, configurable: true }); } catch(e) {}
            }

            // Patch deviceMemory et hardwareConcurrency sur la page courante
            // (Page.addScriptToEvaluateOnNewDocument ne couvre pas la page déjà chargée)
            try {
                Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8 });
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4  });
            } catch(e) {}
        """)
        log.info("[FP][IMMEDIATE] Flag automation supprimé sur la page courante.")
    except Exception as e:
        log.warning("[FP][IMMEDIATE][WARN] Échec suppression flag automation : %s", e)

    # ── Dump fingerprint POST-spoofing ───────────────────────────────────────
    # Page.addScriptToEvaluateOnNewDocument ne s'applique qu'aux navigations
    # futures, pas à la page déjà chargée au moment de l'attach Selenium.
    # On force une navigation about:blank pour que tous les overrides (userAgentData,
    # platform, plugins, WebGL…) soient actifs avant de lire les valeurs.
    # Ce dump reflète exactement ce que les sites tiers verront.
    try:
        driver.get("about:blank")
        fingerprint = driver.execute_script("""
            const uad = navigator.userAgentData;
            return {
                language: navigator.language,
                languages: navigator.languages,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                platform: navigator.platform,
                webdriver: navigator.webdriver,
                userAgent: navigator.userAgent,
                geolocation: !!navigator.geolocation,
                userAgentData: uad ? {
                    platform: uad.platform,
                    mobile:   uad.mobile,
                    brands:   uad.brands
                } : null
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


def launch_browser_playwright(config: dict | None = None):
    """
    Lance Chrome via Playwright (mode pipe, sans --remote-debugging-port).

    Identique à launch_browser() côté arguments Chrome, sauf :
      - pas de --remote-debugging-port / --remote-debugging-address / --remote-debugging-allow-origins
      - proxy passé directement à Playwright (pas de relay local)
      - fingerprint JS injecté via context.add_init_script()

    Retourne un PlaywrightDriverShim prêt à l'emploi.
    """
    from playwright.sync_api import sync_playwright
    from preselection.playwright_shim import PlaywrightDriverShim

    chrome_bin            = _detect_chrome_binary()
    proxy_server, proxy_user, proxy_pass = _parse_proxy_env(config)
    locale, tz            = _parse_locale_tz_env()
    headless              = _want_headless()

    # ── user_data_dir : même logique que launch_browser() ────────────────────
    _persist_account_id = os.getenv("ACCOUNT_ID", "").strip()
    _persist_db_url     = os.getenv("DATABASE_URL", "").strip()
    if _persist_account_id and _persist_db_url:
        user_data_dir = f"/tmp/chrome_profile_{_persist_account_id}"
        os.makedirs(user_data_dir, exist_ok=True)
        from preselection.chrome_profile_store import load_profile
        load_profile(_persist_account_id, user_data_dir)
    else:
        user_data_dir = tempfile.mkdtemp(prefix="chrome_profile_pw_")

    log.info("[LAUNCH][PW] chrome_bin=%s headless=%s locale=%s tz=%s proxy=%s",
             chrome_bin, headless, locale, tz, proxy_server or "none")

    # ── Arguments Chrome (identiques à launch_browser, hors remote-debugging-*) ──
    # --user-data-dir est passé directement à launch_persistent_context(), pas ici.
    chrome_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        *( ["--no-sandbox"] if (not hasattr(os, "getuid") or os.getuid() == 0) else [] ),
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--no-pings",
        "--disable-domain-reliability",
        "--disable-client-side-phishing-detection",
        "--safebrowsing-disable-auto-update",
        "--disable-features=Translate,OptimizationHints,SafeBrowsingProtections,"
            "SafeBrowsingRealTimeUrlLookupEnabled,ChromeWhatsNewUI,"
            "NetworkService,MediaRouter,DialMediaRouteProvider",
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
    page = context.new_page()

    # Pas d'objet Browser séparé : on passe context aux deux premiers slots du shim.
    # shim._browser.close() appellera context.close(), ce qui ferme aussi le browser.
    shim = PlaywrightDriverShim(context, context, page)
    shim._pw                   = pw
    shim._chrome_user_data_dir = user_data_dir
    log.info("[LAUNCH][PW] Browser Playwright lancé, fingerprint injecté via add_init_script.")
    return shim