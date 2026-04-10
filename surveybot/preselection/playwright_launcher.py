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

    # 2) PATH (Windows + Linux)
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


def _build_proxy_auth_extension(proxy_user: str, proxy_pass: str, base_dir: str) -> str:
    """
    Génère une extension Chrome minimale (manifest v2) dans base_dir/proxy_auth_ext/.
    Elle écoute webRequest.onAuthRequired et fournit les credentials proxy.
    Retourne le chemin du répertoire de l'extension.

    Note : --proxy-server=<server> doit être passé en arg Chrome séparément.
    """
    ext_dir = os.path.join(base_dir, "proxy_auth_ext")
    os.makedirs(ext_dir, exist_ok=True)

    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy Auth",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking"
        ],
        "background": {
            "scripts": ["background.js"],
            "persistent": True
        },
        "minimum_chrome_version": "22.0.0"
    }

    # json.dumps assure l'échappement correct des credentials dans le JS
    background_js = (
        "chrome.webRequest.onAuthRequired.addListener(\n"
        "  function(details) {\n"
        "    return {\n"
        "      authCredentials: {\n"
        f"        username: {json.dumps(proxy_user)},\n"
        f"        password: {json.dumps(proxy_pass)}\n"
        "      }\n"
        "    };\n"
        "  },\n"
        "  {urls: ['<all_urls>']},\n"
        "  ['blocking']\n"
        ");\n"
    )

    with open(os.path.join(ext_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    with open(os.path.join(ext_dir, "background.js"), "w", encoding="utf-8") as f:
        f.write(background_js)

    return ext_dir


def launch_browser(config: dict | None = None):
    """
    Mode prod/prod-like : subprocess.Popen lance Chrome directement,
    puis Selenium s'attache via debuggerAddress (identique au mode attach manuel).

    Mode local sans proxy : inchangé (webdriver.Chrome direct).
    Mode attach local (ATTACH_DEBUGGER_ADDRESS) : inchangé.
    """
    chrome_bin = _detect_chrome_binary()
    if IS_LOCAL and not _env_truthy("LOCAL_USE_PROXY"):
        print("[LOCAL] Mode local actif : lancement simple Chrome visible (sans proxy).")

        attach_addr = os.getenv("ATTACH_DEBUGGER_ADDRESS", "").strip()
        options = Options()
        if attach_addr:
            # Mode local : attach à un Chrome existant (géré par run_tabs.ps1)
            options.add_experimental_option("debuggerAddress", attach_addr)
            print(f"⚠️ ATTACH MODE → {attach_addr}")
        else:
            # Mode local : headless si LOCAL_HEADLESS=1 ou pas de DISPLAY fiable (WSL)
            options.add_argument("--disable-blink-features=AutomationControlled")
            import sys
            if sys.platform != "win32":
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu")
                force_headless = (os.getenv("LOCAL_HEADLESS", "0") == "1")
                if force_headless:
                    options.add_argument("--headless=new")
                    options.add_argument("--window-size=1920,1080")
                else:
                    options.add_argument("--start-maximized")
            else:
                options.add_argument("--start-maximized")
            print("🟢 LAUNCHED NEW CHROME SESSION")
            driver = webdriver.Chrome(options=options, service=Service(log_output=subprocess.DEVNULL))
            return driver

    proxy_server, proxy_user, proxy_pass = _parse_proxy_env(config)
    headless = _want_headless()

    # Port remote debugging (Selenium va s'attacher dessus)
    debug_port = int(os.getenv("REMOTE_DEBUG_PORT", 0)) or random.randint(42000, 52000)
    debug_address = os.getenv("REMOTE_DEBUG_ADDRESS", "").strip()

    # Profil isolé (évite collisions + garde la session propre)
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

    if proxy_server:
        cmd.append(f"--proxy-server={proxy_server}")

    # Extension proxy auth : uniquement si credentials présents
    # (--proxy-server= suffit pour les proxies sans auth)
    ext_dir = None
    if proxy_server and proxy_user and proxy_pass:
        ext_dir = _build_proxy_auth_extension(proxy_user, proxy_pass, user_data_dir)
        cmd.append(f"--load-extension={ext_dir}")
        print(f"[LAUNCH][PROXY] extension auth générée : {ext_dir}")

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

    return driver
