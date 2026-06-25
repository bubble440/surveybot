import time, os, requests, base64, re

# ---------------------------------------------------------------------------
# Sélecteurs selon la page de login (TopSurveys expose deux interfaces)
#   - topsurveys.app       → check-email-*  (landing marketing)
#   - app.topsurveys.app/app-login → app-page-* (app SPA)
# Les sélecteurs password/submit sont identiques dans les deux cas.
# ---------------------------------------------------------------------------
_LOGIN_SELECTORS = {
    "app_login": {
        "email_input":    "input[data-test-id='app-page-email-field-input']",
        "continue_btn":   "button[data-test-id='app-page-continue-button']",
        # Sur app-login la page password est une sous-page distincte (data-test sans -id).
        # Le champ <input> est imbriqué dans le wrapper data-test="auth-signin-password" ;
        # son data-test-id vaut "undefined-input" (non fiable) — on cible l'input par type.
        "password_input": "div[data-test='auth-signin-password'] input[type='password']",
        "login_btn":      "button[data-test='auth-signin-submit']",
    },
    "topsurveys": {
        "email_input":    "input[data-test-id='check-email-field-input']",
        "continue_btn":   "button[data-test-id='check-email-continue-button']",
        "password_input": "input[data-test-id='sign-in-password-field-input']",
        "login_btn":      "button[data-test-id='sign-in-submit-button']",
    },
    # Fallback commun si aucun sélecteur spécifique ne matche
    "_common": {
        "password_input": "input[type='password']",
        "login_btn":      "button[type='submit']",
    },
}

# Sélecteurs utilisés par soft_restart_resume (launch.py) pour détecter
# la page de login — doit couvrir les DEUX interfaces.
LOGIN_PAGE_SELECTORS = (
    "[data-test-id='check-email-field-input']",   # topsurveys.app
    "[data-test-id='app-page-email-field-input']", # app.topsurveys.app/app-login
)




def _detect_login_page(driver) -> str:
    """
    Retourne 'app_login' si l'URL courante est app.topsurveys.app/app-login,
    sinon 'topsurveys' (landing marketing).
    """
    try:
        url = (driver.url or "").lower()
        if "app-login" in url or (
            "app.topsurveys.app" in url and "/surveys" not in url
        ):
            return "app_login"
    except Exception:
        pass
    return "topsurveys"


def _get_selectors(driver) -> dict:
    """
    Retourne le dict de sélecteurs adapté à la page courante.
    Les clés de _common ne sont utilisées qu'en fallback si la page
    ne définit pas elle-même password_input / login_btn.
    """
    page = _detect_login_page(driver)
    sel = dict(_LOGIN_SELECTORS["_common"])   # fallback de base
    sel.update(_LOGIN_SELECTORS[page])        # sélecteurs spécifiques à la page (priorité)
    return sel


def _is_prod_env() -> bool:
    """
    Retourne True si on tourne dans un environnement de production (Fly.io/Docker).
    """
    return os.getenv("RUN_ENV", "local").lower() != "local"


def dom_probe(driver):
    """
    Petit dump DOM pour debug.
    ⚠️ Ne s'exécute que lorsque l'on tourne en prod.
    En local, on retourne immédiatement pour éviter le bruit.
    """
    if not _is_prod_env():
        return

    page = driver
    print("[DOM] url=", page.url, "title=", page.title())
    sels = [
        "xpath=//a[normalize-space()=\"Se connecter / S'inscrire\"]",
        "xpath=//a[normalize-space()='Sign in']",
        "a[href*='/auth/login']",
        "button[type='submit']",
    ]
    for sel in sels:
        try:
            els = page.query_selector_all(sel)
            print(f"[DOM] {sel} -> {len(els)}")
        except Exception:
            print(f"[DOM] {sel} -> error")

    try:
        cookies = page.query_selector_all("#onetrust-accept-btn-handler, .cookie-accept")
        print("[DOM] cookies=", len(cookies))
    except Exception:
        print("[DOM] cookies= error")


def is_session_expired(driver) -> bool:
    """
    Détecte une expiration de session / mot de passe.
    Doit être appelée AVANT toute logique survey.
    """
    try:
        txt = (driver.content() or "").lower()
        signals = [
            "session expired",
            "your session has expired",
            "please log in again",
            "mot de passe expiré",
            "reconnectez-vous",
            "password expired",
            "log in again",
        ]
        return any(s in txt for s in signals)
    except Exception:
        return False


def is_proxy_error_page(driver) -> bool:
    """
    Détecte la page d'erreur Chrome ERR_TIMED_OUT indiquant un proxy expiré ou inaccessible.
    """
    try:
        page = driver
        url = page.url or ""
        if "chrome-error://" in url:
            return True
        src = (page.content() or "").lower()
        return "err_timed_out" in src
    except Exception:
        return False


def handle_proxy_error_page_if_needed(driver) -> None:
    """
    Si la page courante est une erreur proxy (ERR_TIMED_OUT) :
    - envoie une notification Telegram avec l'account_id du bot
    - déclenche un DAILY_RESET (arrêt container jusqu'au lendemain)
    Ne fait rien si la page est normale.
    """
    if not is_proxy_error_page(driver):
        return

    from Management.guards.runtime_guard import get_guard, StopReason
    from Management.pause_policy import PausePolicy

    guard = get_guard()
    account_id = getattr(guard, "account_id", "unknown")
    msg = f"🔴 Proxy expiré — TopSurveys inaccessible (ERR_TIMED_OUT) | account={account_id}"
    print(msg)
    try:
        guard.notify_fn(msg)
    except Exception:
        pass
    guard.pause(PausePolicy.DAILY_RESET, StopReason.PROXY_EXPIRED)
    raise SystemExit("proxy_expired")  # garde-fou : pause() lève déjà SystemExit


def net_probe():
    try:
        ip_nat = requests.get("https://api.ipify.org", timeout=8).text
    except Exception as e:
        ip_nat = f"ERR_nat:{e}"

    # Construire le proxy URL depuis les variables du bot (PROXY_URL/USER/PASS)
    proxy_url  = os.getenv("PROXY_URL", "").strip()
    proxy_user = os.getenv("PROXY_USER", "").strip()
    proxy_pass = os.getenv("PROXY_PASS", "").strip()

    if proxy_url:
        if "://" not in proxy_url:
            proxy_url = "http://" + proxy_url
        if proxy_user and proxy_pass:
            from urllib.parse import urlparse, urlunparse
            p = urlparse(proxy_url)
            proxy_url_auth = urlunparse(p._replace(netloc=f"{proxy_user}:{proxy_pass}@{p.hostname}:{p.port}"))
        else:
            proxy_url_auth = proxy_url
        proxies = {"http": proxy_url_auth, "https": proxy_url_auth}
        try:
            ip_proxy = requests.get("https://api.ipify.org", timeout=8, proxies=proxies).text
        except Exception as e:
            ip_proxy = f"ERR_proxy:{e}"
    else:
        ip_proxy = "no-proxy-configured"

    try:
        r = requests.get("https://www.topsurveys.app", timeout=10)
        http = f"{r.status_code} len={len(r.text)}"
    except Exception as e:
        http = f"ERR_http:{e}"

    print(f"[NET] ip_nat={ip_nat} ip_proxy={ip_proxy} topsurveys={http}")


def snap(driver, label: str = "state"):
    """
    Capture un screenshot et :
      1. Sauvegarde le PNG local dans /tmp/ (prod uniquement)
      2. Upload vers Cloudflare R2 si SNAP_ENABLED=1 (optionnel, par bot)
    """
    if not _is_prod_env():
        return

    try:
        png = driver.screenshot()

        path = f"/tmp/{label}.png"
        with open(path, "wb") as f:
            f.write(png)
        print(f"[SNAP] saved {path}")

        from Management.snap_uploader import upload_png
        upload_png(png, label)

    except Exception as e:
        print("[SNAP][ERROR]", e)


def wait_for_vue_hydration(driver, timeout=15):
    page = driver
    start = time.time()
    while time.time() - start < timeout:
        try:
            ready = page.evaluate(
                "() => !!(window.__nuxt && window.__nuxt.isHydrating === false)"
            )
            if ready:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    print("[LOGIN][WARN] Vue hydration timeout — on continue quand même")
    return False


def login(driver, email, password):
    page = driver

    print("[DEBUG][DRIVER] type=", type(driver))
    print("[DEBUG][DRIVER] has _page=", hasattr(driver, "_page"))
    print("[DEBUG][DRIVER] url=", page.url)

    # --- DEBUG: snapshot HTML complet + extraction éventuelle du code d'erreur ---
    try:
        html = page.content()
        path = "/tmp/topsurveys_initial.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

        snippet = html[:800].replace("\n", " ")
        print(f"[HTML_DEBUG] Saved initial HTML to {path}, len={len(html)}")
        print(f"[HTML_DEBUG] Snippet: {snippet}")

        m = re.search(r"ERR_[A-Z0-9_]+", html)
        if m:
            print(f"[HTML_DEBUG] Chrome error code detected: {m.group(0)}")
        else:
            print("[HTML_DEBUG] No ERR_* code found in HTML.")

    except Exception as e:
        print(f"[HTML_DEBUG][ERROR] {type(e).__name__}: {e}")

    # suite normale
    dom_probe(driver)
    wait_for_vue_hydration(driver, timeout=15)

    if os.getenv("SNAP_ENABLED", "").strip() == "1":
        from Management.snap_uploader import new_survey, capture_and_upload
        new_survey()
        capture_and_upload(driver, "survey_account")

    # --- Étape 1 : Saisir l'email dans le champ inline (landing page, pas de modale)
    _sel = _get_selectors(driver)
    print(f"[LOGIN] page détectée={_detect_login_page(driver)} | email_sel={_sel['email_input']}")
    try:
        email_input = page.wait_for_selector(_sel["email_input"], state='visible', timeout=45_000)
        email_input.evaluate("(el) => el.scrollIntoView({block: 'center'})")
        email_input.click()
        time.sleep(0.5)
        email_input.fill("")
        email_input.type(email)
        # Vue ne notifie jamais son v-model sans événements réactifs explicites.
        page.evaluate("""(el) => {
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""", email_input)
        time.sleep(0.5)
        print(f"📧 [LOGIN] Email saisi : {email}")

        continue_btn = page.wait_for_selector(_sel["continue_btn"], state='visible', timeout=20_000)
        # Clic natif Playwright (isTrusted: true)
        continue_btn.click()
        print("[LOGIN] Bouton Continue cliqué.")
        time.sleep(2)

    except Exception as e:
        print("[LOGIN] Echec injection e-mail :", type(e).__name__, "-", e)
        try:
            with open("debug_email_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception:
            pass
        return

    # Attente que le champ password soit présent dans le DOM.
    try:
        pwd_input = page.wait_for_selector(_sel["password_input"], state='attached', timeout=60_000)
        pwd_input.evaluate("(el) => el.scrollIntoView({block: 'center'})")

        # Injection JS de la valeur + événements réactifs Vue
        pwd_input.evaluate(
            "(el, pw) => {"
            "  el.value = pw;"
            "  el.dispatchEvent(new Event('input', { bubbles: true }));"
            "  el.dispatchEvent(new Event('change', { bubbles: true }));"
            "}",
            password
        )
        time.sleep(0.5)

        if (pwd_input.input_value() or "").strip() == "":
            pwd_input.fill(password)
            print("🔁 Fallback : mot de passe injecté via fill()")
        else:
            print("🔑 Mot de passe injecté via JS.")
            time.sleep(1)

        login_btn = page.wait_for_selector(_sel["login_btn"], state='visible', timeout=20_000)
        # JS click pour contourner les éventuels overlays Vue qui bloquent le clic natif
        page.evaluate("(el) => el.click()", login_btn)
        time.sleep(0.5)
        print("✅ Bouton « Se connecter » cliqué.")
        net_probe()

    except Exception as e:
        time.sleep(2)
        print("🛑 Exception mot de passe :", type(e).__name__, "-", e, flush=True)
