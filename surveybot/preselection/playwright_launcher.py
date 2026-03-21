from __future__ import annotations
import os
from selenium.webdriver.chrome.options import Options
from selenium import webdriver

# IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

# preselection/playwright_launcher.py
"""
Launcher alternatif : Playwright lance Chrome avec proxy authentifié,
puis Selenium s'attache au Chrome existant via remote debugging.
Objectif : contourner les erreurs proxy auth de Chrome/UC (ERR_INVALID_ARGUMENT, no-proxy, etc.)
sans réécrire tout le bot qui dépend de Selenium.
"""

import json
import random
import logging
import tempfile
from urllib.parse import urlparse
import undetected_chromedriver as uc

if not IS_LOCAL:
    from selenium import webdriver
from playwright.sync_api import sync_playwright

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

def _apply_devtools_overrides(context):
    """
    Force des signaux navigateur cohérents France
    (langue, timezone, webdriver, geolocation fallback).
    """
    context.add_init_script("""
        // --- Langue ---
        Object.defineProperty(navigator, 'language', {
            get: () => 'fr-FR'
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['fr-FR', 'fr']
        });

        // --- Timezone ---
        const originalResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            const opts = originalResolvedOptions.apply(this, arguments);
            opts.timeZone = 'Europe/Paris';
            return opts;
        };

        // --- WebDriver (anti-bot basique) ---
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });

        // --- Platform ---
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        });
    """)

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


def launch_browser(config: dict | None = None):
    """
    1) Playwright lance Chrome avec proxy authentifié.
    2) Selenium s'attache au Chrome via debuggerAddress.
    3) On renvoie un driver Selenium (comme avant), mais on conserve Playwright en vie.
    """
    chrome_bin = _detect_chrome_binary()
    if IS_LOCAL:
        print("[LOCAL] Mode local actif : lancement simple Chrome visible (sans proxy).")

        attach_addr = os.getenv("ATTACH_DEBUGGER_ADDRESS", "").strip()
        options = Options()
        if attach_addr:
            # Mode local : attach à un Chrome existant (géré par run_tabs.ps1)
            options.add_experimental_option("debuggerAddress", attach_addr)
            print(f"⚠️ ATTACH MODE → {attach_addr}")
        else:
            # Mode local : Chrome visible
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--start-maximized")
            print("🟢 LAUNCHED NEW CHROME SESSION")
        return webdriver.Chrome(options=options)

    proxy_server, proxy_user, proxy_pass = _parse_proxy_env(config)
    print(
    f"[PW][PROXY] server={proxy_server} "
    f"user={'yes' if proxy_user else 'no'} "
    f"pass={'yes' if proxy_pass else 'no'}"
    )
    headless = _want_headless()

    # Port remote debugging (Selenium va s'attacher dessus)
    debug_port = int(os.getenv("REMOTE_DEBUG_PORT", 0)) or random.randint(42000, 52000)
    debug_address = os.getenv("REMOTE_DEBUG_ADDRESS", "").strip()

    # Profil isolé (évite collisions + garde la session propre)
    user_data_dir = tempfile.mkdtemp(prefix="pw_chrome_profile_")

    print(f"[PW] chrome_bin={chrome_bin}")
    print(f"[PW] headless={headless}")
    print(f"[PW] debug_port={debug_port}")
    print(f"[PW] debug_address={debug_address}")
    print(f"[PW] user_data_dir={user_data_dir}")

    if proxy_server:
        print(f"[PW][PROXY] server={proxy_server} user={'yes' if proxy_user else 'no'} pass={'yes' if proxy_pass else 'no'}")
    else:
        print("[PW][PROXY] aucun proxy (PROXY_URL vide)")

    # --- 1) Lancer Chrome via Playwright ---
    pw = sync_playwright().start()
    try:
        launch_args = [
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-features=Translate,OptimizationHints",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
        ]

        proxy_cfg = None
        if proxy_server:
            # ✅ Playwright gère nativement l'auth proxy ici (c'est le but)
            proxy_cfg = {"server": proxy_server}
            if proxy_user and proxy_pass:
                proxy_cfg["username"] = proxy_user
                proxy_cfg["password"] = proxy_pass

        geo = _parse_geo_env()
        locale, tz = _parse_locale_tz_env()
        print(f"[PW][GEO] {geo}  [PW][LOCALE] {locale}  [PW][TZ] {tz}")

        # Ouvre une page (ça “stabilise” le browser)
        args=[
            f"--remote-debugging-port={debug_port}",
            "--remote-debugging-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-features=Translate,OptimizationHints",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
        ]
        if debug_address:
            args.append(f"--remote-debugging-address={debug_address}")
        if headless:
            args.append("--headless=new"),  # 🔑 CRITIQUE EN DOCKER / ECS

        context = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=chrome_bin,
            headless=headless,
            locale=locale,
            timezone_id=tz,
            geolocation=geo,
            args=args,
            proxy=proxy_cfg,
        )

        # 🔧 DevTools overrides (langue / timezone / webdriver)
        _apply_devtools_overrides(context)
        print("[PW][OVERRIDE] DevTools overrides appliqués (FR / Paris).")

        # --- Relay socat : expose le debug port sur 0.0.0.0 ---
        # Playwright force Chrome sur 127.0.0.1 et ignore --remote-debugging-address.
        # socat relaie le port relay (debug_port+1) vers 127.0.0.1:debug_port.
        if debug_address == "0.0.0.0":
            import subprocess as _sp
            relay_port = debug_port + 1
            _sp.Popen(
                ["socat",
                 f"TCP-LISTEN:{relay_port},fork,reuseaddr,bind=0.0.0.0",
                 f"TCP:127.0.0.1:{debug_port}"],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL
            )
            print(f"[PW] socat relay 0.0.0.0:{relay_port} → 127.0.0.1:{debug_port}")

        page = context.new_page()
        # ✅ Permission geolocation : sans ça, beaucoup de sites voient "denied"
        try:
            context.grant_permissions(["geolocation"], origin="https://app.topsurveys.app")
            context.grant_permissions(["geolocation"], origin="https://www.topsurveys.app")
            print("[PW][GEO] permission geolocation accordée pour TopSurveys.")
        except Exception as e:
            print(f"[PW][GEO][WARN] grant_permissions a échoué: {e}")

        # --- 2) Attacher Selenium au Chrome déjà lancé ---
        opts = webdriver.ChromeOptions()
        # ⚠️ Selenium ne doit PAS relancer chrome : on s'attache au debug port
        opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
        opts.page_load_strategy = "eager"  # ne pas attendre toutes les ressources

        driver = webdriver.Chrome(options=opts)        

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

        # On garde Playwright vivant en attachant les objets au driver
        # (sinon garbage collection / fermeture => Selenium perd la session)
        driver._pw = pw
        driver._pw_context = context
        driver._pw_page = page
        driver._pw_user_data_dir = user_data_dir
        
        return driver

    except Exception:
        # ferme proprement playwright si échec
        try:
            assert pw is not None, "Playwright n'est plus actif"
            pw.stop()
        except Exception:
            pass
        raise