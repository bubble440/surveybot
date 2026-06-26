"""
ysense_probe.py — Script de test autonome ySense
Objectif : login → /surveys → sélection meilleur ratio → clic → observer la redirection

Usage : python ysense_probe.py
Creds via env : YSENSE_EMAIL, YSENSE_PASSWORD
Proxy via env : PROXY_URL, PROXY_USER, PROXY_PASS
Snap R2 (prod uniquement) : SNAP_ENABLED=1 + SNAP_R2_ACCOUNT_ID,
                             SNAP_R2_ACCESS_KEY_ID, SNAP_R2_SECRET_ACCESS_KEY,
                             SNAP_R2_BUCKET
"""

import os
import sys
import time
import subprocess
import tempfile
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, Page, BrowserContext


# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────

EMAIL    = os.getenv("YSENSE_EMAIL", "wilsaah456@gmail.com")
PASSWORD = os.getenv("YSENSE_PASSWORD", "p@ssw0rD!123")

BASE_URL    = "https://www.ysense.com"
LOGIN_URL   = f"{BASE_URL}/login"
SURVEYS_URL = f"{BASE_URL}/surveys"

NAV_TIMEOUT = 90_000

IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

# Domaines appartenant à ySense (onglets à fermer après switch)
PLATFORM_DOMAINS = ["ysense.com"]

# Sélecteur discriminant : présent uniquement quand les surveys sont chargés
SURVEY_LINK_SEL = "#survey-list-body a.survey-link[data-survey_reward][data-survey_loi]"


# ──────────────────────────────────────────────────────────────────────────────
# LAUNCHER — même pattern que launch_browser_playwright() de prod
# ──────────────────────────────────────────────────────────────────────────────

def _parse_proxy_env() -> tuple:
    """Retourne (server, user, pass) ou (None, None, None) si pas de proxy."""
    proxy_url  = os.getenv("PROXY_URL", "").strip()
    proxy_user = os.getenv("PROXY_USER", "").strip()
    proxy_pass = os.getenv("PROXY_PASS", "").strip()

    if not proxy_url:
        return None, None, None

    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url

    parsed = urlparse(proxy_url)
    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    return server, proxy_user or None, proxy_pass or None


def _detect_chrome_binary() -> str:
    import shutil
    if sys.platform != "win32":
        for p in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
            if os.path.exists(p):
                return p
        for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
            path = shutil.which(name)
            if path and not path.endswith(".exe"):
                return path
    else:
        for name in ("chrome", "chrome.exe", "chromium"):
            path = shutil.which(name)
            if path:
                return path
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Chrome/Chromium introuvable.")


def _fingerprint_js() -> str:
    """JS de spoofing fingerprint — identique à launch_browser_playwright."""
    base = """
        const _origResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            const opts = _origResolvedOptions.apply(this, arguments);
            opts.timeZone = 'Europe/Paris';
            return opts;
        };
        if (!window.chrome) {
            const _chrome = {
                app: { isInstalled: false },
                runtime: {},
                loadTimes: function() { return {}; },
                csi: function() { return {}; },
            };
            try {
                Object.defineProperty(window, 'chrome', { value: _chrome, writable: false, enumerable: true, configurable: false });
            } catch(e) {}
        }
    """
    if not IS_LOCAL:
        base += """
        try {
            Object.defineProperty(window, 'RTCPeerConnection',       { value: undefined, writable: false });
            Object.defineProperty(window, 'webkitRTCPeerConnection', { value: undefined, writable: false });
        } catch(e) {}
        """
    return base


def launch_browser() -> Page:
    """
    Lance Chrome via launch_persistent_context — même pattern que prod.
    Proxy injecté au niveau context uniquement (pas de browser.launch séparé).
    Retourne une Page prête à l'emploi.
    """
    chrome_bin = _detect_chrome_binary()
    proxy_server, proxy_user, proxy_pass = _parse_proxy_env()
    headless = not bool(os.environ.get("DISPLAY")) if sys.platform != "win32" else False
    user_data_dir = tempfile.mkdtemp(prefix="chrome_profile_ysense_")

    chrome_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
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
        "--disable-extensions",
        "--disable-notifications",
        "--window-size=1280,900",
        "--lang=en-US",
    ]
    if not hasattr(os, "getuid") or os.getuid() == 0:
        chrome_args += ["--no-sandbox", "--test-type"]
    if not IS_LOCAL:
        chrome_args += [
            "--disable-features=WebRTC",
            "--enforce-webrtc-ip-permission-check",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        ]
    if not headless and os.environ.get("DISPLAY") and ".exe" not in chrome_bin.lower():
        chrome_args += ["--use-gl=angle", "--use-angle=swiftshader"]

    pw_proxy = None
    if proxy_server:
        pw_proxy = {"server": proxy_server}
        if proxy_user:
            pw_proxy["username"] = proxy_user
        if proxy_pass:
            pw_proxy["password"] = proxy_pass
        print(f"[PROXY] {proxy_server} user={proxy_user or '(none)'}")
    else:
        print("[PROXY] Aucun proxy configuré.")

    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir,
        executable_path=chrome_bin,
        args=chrome_args,
        env={**os.environ, "TZ": "Europe/Paris"},
        headless=headless,
        locale="fr-FR",
        timezone_id="Europe/Paris",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
        proxy=pw_proxy,
    )
    context.add_init_script(_fingerprint_js())
    page = context.pages[0] if context.pages else context.new_page()
    page._pw = pw
    return page


# ──────────────────────────────────────────────────────────────────────────────
# SNAP — upload R2 uniquement si SNAP_ENABLED=1. No-op en local.
# ──────────────────────────────────────────────────────────────────────────────

_session_id: str = time.strftime("%Y%m%d_%H%M%S")
_snap_step: int = 0


def _snap_enabled() -> bool:
    return os.getenv("SNAP_ENABLED", "").strip() == "1"


def _capture_png_scrot() -> bytes:
    """
    Capture via scrot uniquement (sans CDP ni Playwright).
    Independant du browser — pas de blocage fonts/proxy.
    Leve une exception si scrot echoue.
    """
    display = os.getenv("DISPLAY", ":99")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        os.remove(path)
    except Exception:
        pass
    try:
        time.sleep(1.5)  # laisser Chrome finir le rendu avant capture X11
        result = subprocess.run(
            ["scrot", path],
            env={**os.environ, "DISPLAY": display},
            timeout=8,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"scrot returncode={result.returncode} stderr={result.stderr!r}")
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except Exception:
            pass



def snap_and_upload(page: Page, label: str) -> None:
    """En prod (SNAP_ENABLED=1) : capture + upload R2. En local : no-op."""
    if not _snap_enabled():
        return

    global _snap_step
    _snap_step += 1

    try:
        png = _capture_png_scrot()
    except Exception as e:
        print(f"[SNAP][ERROR] capture failed label={label} : {e}")
        return

    try:
        import boto3
        account_id = os.environ["SNAP_R2_ACCOUNT_ID"]
        access_key = os.environ["SNAP_R2_ACCESS_KEY_ID"]
        secret_key = os.environ["SNAP_R2_SECRET_ACCESS_KEY"]
        bucket     = os.environ["SNAP_R2_BUCKET"]
        endpoint   = f"https://{account_id}.r2.cloudflarestorage.com"
        key        = f"ysense/{_session_id}/{_snap_step:03d}_{label}.png"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        client.put_object(Bucket=bucket, Key=key, Body=png, ContentType="image/png")
        print(f"[SNAP] uploaded → r2://{bucket}/{key} ({len(png)}B)")
    except Exception as e:
        print(f"[SNAP][ERROR] R2 upload failed label={label} : {e}")


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS NAVIGATION
# ──────────────────────────────────────────────────────────────────────────────

def _wait_selector(page: Page, selector: str, timeout_ms: int = 15_000) -> bool:
    """Attend qu'un sélecteur soit attaché. Retourne True si trouvé, False si timeout."""
    try:
        page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
        return True
    except Exception:
        return False


def _reload_until_content(
    page: Page,
    selector: str,
    max_reloads: int = 3,
    wait_ms: int = 10_000,
) -> bool:
    """
    Recharge la page jusqu'à max_reloads fois si le sélecteur est absent.
    Retourne True si le contenu est détecté, False si épuisé.
    """
    if page.query_selector(selector) is not None:
        return True

    for attempt in range(1, max_reloads + 1):
        print(f"[RELOAD] Contenu absent — reload {attempt}/{max_reloads}")
        try:
            page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        except Exception as e:
            print(f"[RELOAD][WARN] reload échoué : {e}")
        if _wait_selector(page, selector, timeout_ms=wait_ms):
            print(f"[RELOAD] Contenu détecté après reload {attempt}.")
            return True

    return False


def pick_best_survey(page: Page) -> "dict | None":
    """
    Extrait tous les surveys de #survey-list-body via data-attributes.
    Calcule ratio reward/loi (cents/min) et retourne le meilleur.
    """
    surveys = []
    links = page.query_selector_all(SURVEY_LINK_SEL)
    print(f"[SURVEYS] {len(links)} surveys trouvés dans #survey-list-body")

    for link in links:
        try:
            reward = int(link.get_attribute("data-survey_reward") or 0)
            loi    = int(link.get_attribute("data-survey_loi") or 0)
            sid    = link.get_attribute("data-survey_id") or "?"
            href   = link.get_attribute("href") or ""
            if loi <= 0:
                continue
            surveys.append({
                "element": link,
                "sid":     sid,
                "reward":  reward,
                "loi":     loi,
                "ratio":   reward / loi,
                "href":    href,
            })
        except Exception as e:
            print(f"[SURVEYS][WARN] Erreur parsing survey : {e}")

    if not surveys:
        return None

    best = max(surveys, key=lambda s: s["ratio"])
    print(
        f"[SURVEYS] Meilleur ratio → id={best['sid']} "
        f"reward={best['reward']}¢ loi={best['loi']}min "
        f"ratio={best['ratio']:.2f}¢/min"
    )
    return best


def switch_to_latest_window_and_close_others(
    context: BrowserContext,
    base_handles: list,
    timeout: int = 10,
) -> "Page | None":
    """
    Attend l'ouverture d'un nouvel onglet survey et ferme les onglets plateforme.
    Retourne la Page du survey, ou None si aucun nouvel onglet détecté.
    """
    already_new = [p for p in context.pages if p not in base_handles]
    new_page = already_new[-1] if already_new else None

    if new_page is None:
        try:
            new_page = context.wait_for_event("page", timeout=timeout * 1000)
            if new_page in base_handles:
                new_page = None
        except Exception:
            new_page = None

    if new_page is not None:
        new_page.bring_to_front()
        for p in list(base_handles):
            try:
                p.close()
            except Exception:
                pass
        live = context.pages
        if new_page in live:
            new_page.bring_to_front()
        elif live:
            new_page = live[-1]
            new_page.bring_to_front()
        else:
            raise RuntimeError("Aucun onglet restant après fermeture des onglets plateforme")
        print(f"[SWITCH] Nouvel onglet survey → {new_page.url}")
        return new_page

    # Fallback : chercher un onglet externe
    for p in context.pages:
        try:
            url = p.url or ""
            if not any(d in url for d in PLATFORM_DOMAINS):
                for op in context.pages:
                    if op is not p:
                        try:
                            op.close()
                        except Exception:
                            pass
                p.bring_to_front()
                print(f"[SWITCH] Fallback onglet externe → {url}")
                return p
        except Exception:
            continue

    print("[SWITCH][WARN] Aucun nouvel onglet — navigation même onglet supposée.")
    return None


# ──────────────────────────────────────────────────────────────────────────────
# ÉTAPE 1 : LOGIN
# ──────────────────────────────────────────────────────────────────────────────

def do_login(page: Page):
    """
    Login ySense. Le formulaire a target="dummy" : soumission dans un iframe caché,
    le frame principal ne navigue jamais. On attend 5s côté serveur puis on goto
    /surveys directement (inutile de passer par la home).
    """
    print(f"[LOGIN] Navigation vers {LOGIN_URL}")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    # Attente que le JS de la page login soit exécuté (proxy lent)
    page.wait_for_selector("input#username", state="visible", timeout=30_000)

    try:
        png = _capture_png_scrot()
        snap_path = "/tmp/ysense_login_before_login.png"
        with open(snap_path, "wb") as f:
            f.write(png)
        print(f"[SNAP] login_before_login -> {snap_path}")
    except Exception as e:
        print(f"[SNAP][WARN] scrot login_before_login echoue : {e}")

    page.fill("input#username", EMAIL)
    print(f"[LOGIN] Email saisi : {EMAIL}")
    time.sleep(0.3)

    page.fill("input#password", PASSWORD)
    print("[LOGIN] Mot de passe saisi.")
    time.sleep(0.3)

    # Snap pre-clic via scrot (independant CDP — pas de blocage fonts proxy)
    try:
        png = _capture_png_scrot()
        snap_path = "/tmp/ysense_login_before_submit.png"
        with open(snap_path, "wb") as f:
            f.write(png)
        print(f"[SNAP] login_before_submit -> {snap_path}")
    except Exception as e:
        print(f"[SNAP][WARN] scrot login_before_submit echoue : {e}")

    page.click("button.sbutton.large")
    print("[LOGIN] Bouton Sign In clique.")

    # Attente généreuse : validation serveur via iframe dummy + latence proxy
    time.sleep(8)


# ──────────────────────────────────────────────────────────────────────────────
# ÉTAPE 2 : NAVIGATION VERS /surveys + reload si contenu absent
# ──────────────────────────────────────────────────────────────────────────────

def go_to_surveys(page: Page) -> bool:
    """
    Navigue vers /surveys, attend le contenu.
    Recharge jusqu'à 3 fois si #survey-list-body est vide après chargement.
    Retourne True si des surveys sont détectés, False sinon.
    """
    # Pause supplémentaire pour absorber la latence proxy post-login
    time.sleep(3)
    print(f"[SURVEYS] Navigation vers {SURVEYS_URL}")
    page.goto(SURVEYS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)

    found = _wait_selector(page, SURVEY_LINK_SEL, timeout_ms=15_000)
    if not found:
        found = _reload_until_content(page, SURVEY_LINK_SEL, max_reloads=3, wait_ms=10_000)

    count = len(page.query_selector_all(SURVEY_LINK_SEL))
    print(f"[SURVEYS] URL={page.url} | surveys détectés={count}")
    return count > 0


# ──────────────────────────────────────────────────────────────────────────────
# ÉTAPE 3 : SÉLECTION, CLIC, SWITCH & SNAP
# ──────────────────────────────────────────────────────────────────────────────

def click_best_survey(page: Page, context: BrowserContext) -> "tuple[str | None, Page | None]":
    """
    Sélectionne le meilleur survey, clique, switch vers le nouvel onglet,
    attend la stabilisation avec reload si nécessaire, puis snap (prod).
    """
    best = pick_best_survey(page)
    if best is None:
        print("[SURVEYS][ERROR] Aucun survey disponible.")
        snap_and_upload(page, "no_surveys")
        return None, None

    base_handles = list(context.pages)
    print(f"[CLICK] Clic natif sur survey id={best['sid']}")
    before_url = page.url

    best["element"].click()

    survey_page = switch_to_latest_window_and_close_others(
        context=context,
        base_handles=base_handles,
        timeout=10,
    )

    if survey_page is not None:
        try:
            survey_page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception:
            pass
        # Reload si page vide après switch
        if not survey_page.url or survey_page.url in ("about:blank", ""):
            print("[CLICK][WARN] Page vide après switch — reload")
            try:
                survey_page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            except Exception:
                pass
        final_url = survey_page.url
        print(f"[CLICK] URL survey (nouvel onglet) : {final_url}")
    else:
        survey_page = page
        deadline = time.time() + 15
        while time.time() < deadline:
            if page.url != before_url:
                break
            time.sleep(0.3)
        if page.url == before_url:
            print("[CLICK][WARN] URL inchangée — reload")
            try:
                page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            except Exception:
                pass
        final_url = page.url
        print(f"[CLICK] URL survey (même onglet) : {final_url}")

    snap_and_upload(survey_page, "survey_landing")
    return final_url, survey_page


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    if not EMAIL or not PASSWORD:
        print("[ERROR] Définir YSENSE_EMAIL et YSENSE_PASSWORD dans l'environnement.")
        return

    page = launch_browser()
    context = page.context

    try:
        # ── Étape 1 : Login
        do_login(page)

        # ── Étape 2 : /surveys avec reloads si contenu absent
        surveys_ok = go_to_surveys(page)

        # Vérification session sur /surveys (plusieurs sélecteurs selon rendu)
        SESSION_SELECTORS = [
            "#ysnNavbarRight",
            "a[href='/rewards']",
            "a[onclick*='logout']",
            "#header_avatar",
        ]
        session_ok = any(
            page.query_selector(sel) is not None for sel in SESSION_SELECTORS
        )
        if not session_ok:
            print("[LOGIN][ERROR] Session non établie sur /surveys. Arrêt.")
            snap_and_upload(page, "login_failed")
            return
        print("[LOGIN] Session établie ✓")

        if not surveys_ok:
            print("[SURVEYS][ERROR] Aucun survey après 3 reloads.")
            snap_and_upload(page, "no_surveys")
            return

        # ── Étape 3 : Clic + switch + snap
        final_url, survey_page = click_best_survey(page, context)

        if final_url:
            print(f"\n✅ Résultat final : {final_url}")
            print("\n[PROBE] Pause 3600s pour observation visuelle...")
            time.sleep(3600)
        else:
            print("[PROBE][ERROR] Aucun survey cliqué.")

    except Exception as e:
        print(f"[PROBE][FATAL] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        time.sleep(15)
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            page._pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()