from __future__ import annotations
import os

# IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"
RUN_ENV = os.getenv("RUN_ENV", "docker").lower()
IS_LOCAL = RUN_ENV == "local"

# preselection/playwright_launcher.py
"""
Launcher alternatif : Playwright lance Chrome avec proxy authentifié,
puis Selenium s'attache au Chrome existant via remote debugging.
Objectif : contourner les erreurs proxy auth de Chrome/UC (ERR_INVALID_ARGUMENT, no-proxy, etc.)
sans réécrire tout le bot qui dépend de Selenium.
"""

import re
import time
import json
import shutil
import random
import logging
import tempfile
from urllib.parse import urlparse
import undetected_chromedriver as uc

if not IS_LOCAL:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

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

def _parse_proxy_env():
    proxy_url = os.getenv("PROXY_URL", "").strip()
    proxy_user = os.getenv("PROXY_USER", "").strip()
    proxy_pass = os.getenv("PROXY_PASS", "").strip()

    if not proxy_url:
        return None, None, None

    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url

    parsed = urlparse(proxy_url)
    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"

    # 🔒 Si user ou pass manquant → on désactive l’auth
    if not proxy_user or not proxy_pass:
        return server, None, None

    return server, proxy_user, proxy_pass

def _want_headless() -> bool:
    """
    Headless si SURVEY_HEADLESS=1 et pas de DISPLAY.
    """
    use_display = bool(os.environ.get("DISPLAY"))
    headless_env = os.getenv("SURVEY_HEADLESS", "1") == "1"
    return (not use_display) and headless_env

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

def launch_browser():
    """
    1) Playwright lance Chrome avec proxy authentifié.
    2) Selenium s'attache au Chrome via debuggerAddress.
    3) On renvoie un driver Selenium (comme avant), mais on conserve Playwright en vie.
    """
    chrome_bin = _detect_chrome_binary()
    if IS_LOCAL:
        print("[LOCAL] Mode local actif : lancement simple Chrome visible (sans proxy).")

        chrome_options = uc.ChromeOptions()
        chrome_options.binary_location = chrome_bin
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--new-window")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")

        driver = uc.Chrome(
            browser_executable_path=chrome_bin,
            options=chrome_options,
        )

        # driver.get("https://www.topsurveys.app/")
        return driver

    proxy_server, proxy_user, proxy_pass = _parse_proxy_env()
    print(
    f"[PW][PROXY] server={proxy_server} "
    f"user={'yes' if proxy_user else 'no'} "
    f"pass={'yes' if proxy_pass else 'no'}"
)
    headless = _want_headless()

    # Port remote debugging (Selenium va s'attacher dessus)
    debug_port = random.randint(42000, 52000)

    # Profil isolé (évite collisions + garde la session propre)
    user_data_dir = tempfile.mkdtemp(prefix="pw_chrome_profile_")

    print(f"[PW] chrome_bin={chrome_bin}")
    print(f"[PW] headless={headless}")
    print(f"[PW] debug_port={debug_port}")
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

        # Petit check côté Selenium
        try:
            driver.get("https://api.ipify.org/")
            time.sleep(0.8)
            html = driver.page_source or ""
            m = re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", html)
            ip_sel = m.group(0) if m else None
            print(f"[SEL][CHECK] ipify via Selenium => {ip_sel}")
        except Exception as e:
            print(f"[SEL][WARN] check ipify Selenium a échoué: {e}")

        return driver

    except Exception:
        # ferme proprement playwright si échec
        try:
            assert pw is not None, "Playwright n'est plus actif"
            pw.stop()
        except Exception:
            pass
        raise